"""说说 · 语音输入客户端（Windows / macOS）

按住热键（Windows 默认 CapsLock，macOS 默认右 Option）说话，松开后把整段录音发给识别服务，结果打到当前光标处。
短按热键仍是原来的功能（CapsLock 照常切换大小写）。平台相关的部分在 platform_win.py / platform_mac.py。
"""
import configparser
import io
import json
import logging
import logging.handlers
import math
import os
import queue
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave

import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

if sys.platform == "darwin":
    import platform_mac as plat
else:
    import platform_win as plat

HERE = plat.data_dir(os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(HERE, "config.ini")
log = logging.getLogger("shuoshuo")

DEFAULTS = {
    "server": "http://127.0.0.1:8765",
    "token": "",
    "hotkey": plat.DEFAULT_HOTKEY,
    "tap_seconds": "0.3",
    "strip_trailing_punct": "false",
    "sound": "true",
    "keep_mic_open": "false",
    "server_cmd": "",
    "mac_inject": "auto",
}
RATE = 16000
TAIL_SECONDS = 0.15  # 松键后多录一小段，免得吞掉最后一个字
MAX_SECONDS = 300    # 万一漏了松键事件，最多录这么久
COLORS = {"idle": (37, 99, 235), "rec": (220, 38, 38), "busy": (217, 119, 6), "off": (128, 128, 128)}

class Recorder:
    """16-bit 单声道录音。默认按下才开麦；keep_open 时常开（蓝牙耳机切换模式慢，首字容易被吞）。"""

    def __init__(self, keep_open, on_overflow):
        self.keep_open, self.on_overflow = keep_open, on_overflow
        self.lock = threading.Lock()
        self.buf, self.active, self.stream, self.rate = bytearray(), False, None, RATE
        if keep_open:
            self._open()

    def _open(self):
        # 每次按下都重新打开，这样换了默认麦克风（插耳机）也能跟上
        try:
            self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", callback=self._callback)
        except sd.PortAudioError:  # 个别声卡不支持 16k：按设备默认采样率录，服务端会重采样
            rate = int(sd.query_devices(kind="input")["default_samplerate"])
            self.stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", callback=self._callback)
        self.rate = int(self.stream.samplerate)
        self.stream.start()

    def _callback(self, data, frames, time_info, status):
        with self.lock:
            if self.active:
                self.buf += data
                if len(self.buf) > MAX_SECONDS * self.rate * 2:
                    self.active = False
                    self.on_overflow()

    def start(self):
        with self.lock:
            self.buf, self.active = bytearray(), True
        if not self.keep_open:
            self._open()

    def stop(self, tail=TAIL_SECONDS):
        time.sleep(tail)
        if not self.keep_open:
            self.stream.stop()
            self.stream.close()
        with self.lock:
            self.active = False
            data, self.buf = bytes(self.buf), bytearray()
        return data


def make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=color)
    d.rounded_rectangle((25, 12, 39, 38), radius=7, fill="white")  # 话筒
    d.arc((18, 22, 46, 46), 0, 180, fill="white", width=4)          # 托架
    d.line((32, 46, 32, 53), fill="white", width=4)
    d.line((24, 53, 40, 53), fill="white", width=4)
    return img


def make_sounds():
    """生成两段很短的提示音：上扬 = 开始录音，下降 = 录音结束。"""
    paths = {}
    for name, freqs in (("start", (660, 880)), ("stop", (880, 660))):
        frames = bytearray()
        for f in freqs:
            n = int(RATE * 0.06)
            for i in range(n):
                fade = min(1.0, i / 160, (n - i) / 160)
                frames += struct.pack("<h", int(5000 * fade * math.sin(2 * math.pi * f * i / RATE)))
        paths[name] = os.path.join(tempfile.gettempdir(), f"shuoshuo_{name}.wav")
        with wave.open(paths[name], "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(frames)
    return paths


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.server = cfg["server"].rstrip("/")
        self.headers = {"Content-Type": "audio/wav"}
        if cfg["token"]:
            self.headers["Authorization"] = f"Bearer {cfg['token']}"
        # 识别服务在本机 / 局域网 / Tailscale 上，绕开系统代理（Clash 之类会把请求转走）
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.key_name = (cfg["hotkey"].strip() or plat.DEFAULT_HOTKEY).lower()
        if self.key_name not in plat.HOTKEYS:
            raise ValueError(f"不认识的热键 {cfg['hotkey']}，可选：{', '.join(plat.HOTKEYS)}")
        self.vk = plat.HOTKEYS[self.key_name]
        self.tap = cfg.getfloat("tap_seconds")
        self.strip_punct = cfg.getboolean("strip_trailing_punct")
        self.sounds = make_sounds() if cfg.getboolean("sound") else None

        self.events, self.jobs = queue.Queue(), queue.Queue()
        self.recorder = Recorder(cfg.getboolean("keep_mic_open"), lambda: self.events.put(("up",)))
        self.hook = None
        self.paused = self.recording = self.offline = self.starting = False
        self.last_failed = None
        self.server_proc = None
        self.status = ""
        self.ui_lock = threading.Lock()
        self.icons = {name: make_icon(color) for name, color in COLORS.items()}
        self.icon = pystray.Icon("shuoshuo", self.icons["off"], "说说", menu=pystray.Menu(
            pystray.MenuItem(lambda _: self.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("暂停", self.toggle_pause, checked=lambda _: self.paused),
            pystray.MenuItem("重试上一条", self.retry, enabled=lambda _: self.last_failed is not None),
            pystray.MenuItem("打开配置文件", lambda: plat.open_file(CONFIG_PATH)),
            pystray.MenuItem("退出", self.quit),
        ))

    # ---------- 生命周期 ----------
    def run(self):
        self.icon.run(setup=self.startup)
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()

    def startup(self, icon):
        plat.ui(lambda: setattr(icon, "visible", True))
        self.hook = plat.KeyHook(self.vk, self.events, on_fail=self.notify)
        threading.Thread(target=self.control_loop, daemon=True).start()
        threading.Thread(target=self.worker_loop, daemon=True).start()
        self.refresh()
        autostarted = False
        if not self.healthy() and self.cfg["server_cmd"]:
            self.starting = autostarted = True
            self.refresh()
            log_file = open(os.path.join(HERE, "server.log"), "ab")
            self.server_proc = plat.spawn(self.cfg["server_cmd"], log_file)
            while self.server_proc.poll() is None and not self.healthy():
                time.sleep(1)
            self.starting = False
        self.offline = not self.healthy()
        self.refresh()
        if self.offline:
            self.notify(f"连不上识别服务 {self.server}")
        elif autostarted:
            self.notify(f"识别服务已就绪，按住 {self.key_name} 说话")

    def quit(self):
        self.icon.stop()

    # ---------- 按键 → 录音 ----------
    def control_loop(self):
        while True:
            ev = self.events.get()
            if ev[0] != "down":
                continue
            info = ev[1]
            try:
                self.recorder.start()
            except Exception as e:
                log.exception("麦克风打开失败")
                self.notify(f"麦克风打开失败：{e}")
                continue
            t0 = time.monotonic()
            try:
                self.events.get(timeout=self.tap)
            except queue.Empty:
                pass
            else:  # 阈值内就松开了：当普通按键补发回去
                plat.replay_tap(self.vk, info)
                self.recorder.stop(tail=0)
                continue

            self.recording = True
            self.refresh()
            self.play("start")
            while self.events.get()[0] != "up":
                pass
            pcm = self.recorder.stop()
            self.recording = False
            self.play("stop")
            if len(pcm) < self.recorder.rate // 5:  # 不到 0.1 秒（每个采样 2 字节）：麦克风没送来数据，传上去也识别不了
                log.warning("没录到声音：按住 %.1f 秒，只收到 %d 字节", time.monotonic() - t0, len(pcm))
                self.notify("没录到声音：麦克风没送来数据\n检查一下系统的默认录音设备")
                self.refresh()
                continue
            # 记下松键时的前台窗口：识别回来如果用户已经切走了，就不往别处打字
            self.jobs.put((pcm, self.recorder.rate, plat.foreground()))
            self.refresh()

    # ---------- 识别 → 输出 ----------
    def worker_loop(self):
        while True:
            pcm, rate, hwnd = self.jobs.get()
            try:
                text = self.recognize(pcm, rate)
                self.offline = False
                if self.strip_punct:
                    text = text.rstrip("。.")
                if text:
                    self.output(text, hwnd)
            except Exception as e:
                log.warning("识别失败：%s", e)
                self.offline, self.last_failed = True, (pcm, rate)
                self.notify(f"识别失败：{e}\n托盘菜单里可以「重试上一条」")
            finally:
                self.jobs.task_done()
                self.refresh()

    def recognize(self, pcm, rate):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        seconds = len(pcm) / 2 / rate
        req = urllib.request.Request(self.server + "/asr", data=buf.getvalue(), headers=self.headers)
        t0 = time.perf_counter()
        for attempt in range(3):
            try:
                with self.http.open(req, timeout=10 + seconds) as r:
                    res = json.load(r)
                log.info("%.1fs 音频 → %d 字，往返 %.0f ms（推理 %d ms）",
                         seconds, len(res["text"]), (time.perf_counter() - t0) * 1000, res["ms"])
                return res["text"]
            except urllib.error.HTTPError as e:
                if e.code < 500:  # token 不对之类，重试没用
                    raise RuntimeError(f"HTTP {e.code} {e.reason}") from None
                err = e
            except OSError as e:  # 连不上、超时、连接被重置
                err = e
            if attempt < 2:
                time.sleep(attempt + 1)
        raise err

    def output(self, text, hwnd):
        if hwnd and plat.foreground() == hwnd:
            plat.type_text(text)
        else:  # 识别期间切了窗口，或者是手动重试：放进剪贴板，别往不相干的地方打字
            plat.set_clipboard(text)
            self.notify(f"已复制到剪贴板：{text[:60]}")

    # ---------- 托盘 ----------
    def toggle_pause(self):
        self.paused = not self.paused
        self.hook.enabled, self.hook.down = not self.paused, False
        if self.paused:
            self.events.put(("up",))  # 正录着音就收尾，否则这条会被 control_loop 忽略
        self.refresh()

    def retry(self):
        if self.last_failed:
            pcm, rate = self.last_failed
            self.last_failed = None
            self.jobs.put((pcm, rate, None))
            self.refresh()

    def healthy(self):
        try:
            with self.http.open(self.server + "/health", timeout=3) as r:
                return r.status == 200
        except OSError:
            return False

    def refresh(self):
        if self.paused:
            name, text = "off", "已暂停"
        elif self.recording:
            name, text = "rec", "录音中…"
        elif self.jobs.unfinished_tasks:
            name, text = "busy", "识别中…"
        elif self.starting:
            name, text = "busy", "正在启动识别服务…"
        elif self.offline:
            name, text = "off", "识别服务不可用"
        else:
            name, text = "idle", f"就绪 · 按住 {self.key_name} 说话"
        def apply():
            with self.ui_lock:
                self.status = text
                self.icon.icon = self.icons[name]
                self.icon.title = f"说说 · {text}"
                self.icon.update_menu()

        plat.ui(apply)

    def notify(self, message):
        try:
            plat.notify(self.icon, message)
        except Exception:
            log.exception("通知失败")

    def play(self, name):
        if self.sounds:
            plat.play(self.sounds[name])


def main():
    handlers = [logging.handlers.RotatingFileHandler(os.path.join(HERE, "shuoshuo.log"), maxBytes=1 << 20,
                                                     backupCount=1, encoding="utf-8")]
    if sys.stderr:  # pythonw 下没有控制台
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    threading.excepthook = lambda a: log.error("线程异常", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    if not plat.single_instance():  # 已经开着一个了
        return
    try:
        cfg = configparser.ConfigParser(interpolation=None)  # 命令里可能有 %VAR%
        cfg.read_dict({"shuoshuo": DEFAULTS})
        cfg.read(CONFIG_PATH, encoding="utf-8-sig")  # 记事本可能存成带 BOM 的 UTF-8
        plat.setup_app(cfg["shuoshuo"])
        app = App(cfg["shuoshuo"])
    except Exception as e:
        log.exception("启动失败")
        plat.alert("说说 · 启动失败", str(e))
        return
    app.run()


if __name__ == "__main__":
    main()
