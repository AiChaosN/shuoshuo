"""说说 · Windows 语音输入客户端

按住热键（默认 CapsLock）说话，松开后把整段录音发给识别服务，结果打到当前光标处。
短按热键仍是原来的功能（CapsLock 照常切换大小写）。
"""
import configparser
import ctypes
import io
import json
import logging
import logging.handlers
import math
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
import winsound
from ctypes import wintypes

import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.ini")
log = logging.getLogger("shuoshuo")

DEFAULTS = {
    "server": "http://127.0.0.1:8765",
    "token": "",
    "hotkey": "capslock",
    "tap_seconds": "0.3",
    "strip_trailing_punct": "false",
    "sound": "true",
    "keep_mic_open": "false",
    "server_cmd": "",
}
HOTKEYS = {"capslock": 0x14, "rctrl": 0xA3, "ralt": 0xA5, "rshift": 0xA1, "pause": 0x13,
           "scrolllock": 0x91, "insert": 0x2D, **{f"f{i}": 0x6F + i for i in range(1, 25)}}
RATE = 16000
TAIL_SECONDS = 0.15  # 松键后多录一小段，免得吞掉最后一个字
MAX_SECONDS = 300    # 万一漏了松键事件，最多录这么久
COLORS = {"idle": (37, 99, 235), "rec": (220, 38, 38), "busy": (217, 119, 6), "off": (128, 128, 128)}

# ---------- Win32：低级键盘钩子、SendInput、剪贴板 ----------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, HC_ACTION = 13, 0
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLKHF_EXTENDED = 0x01
MARK = 0x53485553  # 写进 dwExtraInfo，钩子据此认出自己注入的按键
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x2

LRESULT = wintypes.LPARAM
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]  # mi 用不到，只为让结构体大小和系统一致

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


for fn, argtypes, restype in [
    (user32.SetWindowsHookExW, (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD), wintypes.HHOOK),
    (user32.CallNextHookEx, (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM), LRESULT),
    (user32.GetMessageW, (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT), wintypes.BOOL),
    (user32.SendInput, (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int), wintypes.UINT),
    (user32.GetForegroundWindow, (), wintypes.HWND),
    (user32.CreateWindowExW, (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                              wintypes.HINSTANCE, wintypes.LPVOID), wintypes.HWND),
    (user32.DestroyWindow, (wintypes.HWND,), wintypes.BOOL),
    (user32.OpenClipboard, (wintypes.HWND,), wintypes.BOOL),
    (user32.SetClipboardData, (wintypes.UINT, wintypes.HANDLE), wintypes.HANDLE),
    (user32.MessageBoxW, (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT), ctypes.c_int),
    (kernel32.GetModuleHandleW, (wintypes.LPCWSTR,), wintypes.HMODULE),
    (kernel32.CreateMutexW, (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR), wintypes.HANDLE),
    (kernel32.GlobalAlloc, (wintypes.UINT, ctypes.c_size_t), wintypes.HGLOBAL),
    (kernel32.GlobalLock, (wintypes.HGLOBAL,), wintypes.LPVOID),
    (kernel32.GlobalUnlock, (wintypes.HGLOBAL,), wintypes.BOOL),
    (kernel32.GlobalFree, (wintypes.HGLOBAL,), wintypes.HGLOBAL),
]:
    fn.argtypes, fn.restype = argtypes, restype


def send_keys(events):
    """events: [(vk, scan, flags), ...]，一次 SendInput 整体注入，不会和用户自己的按键交错。"""
    arr = (INPUT * len(events))(*[INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, MARK))
                                  for vk, scan, flags in events])
    if user32.SendInput(len(events), arr, ctypes.sizeof(INPUT)) != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


def type_text(text):
    """按 Unicode 字符直接注入，不经过输入法，也不动剪贴板。"""
    data = text.encode("utf-16-le")
    units = struct.unpack(f"<{len(data) // 2}H", data)
    send_keys([(0, u, KEYEVENTF_UNICODE | up) for u in units for up in (0, KEYEVENTF_KEYUP)])


def set_clipboard(text):
    data = text.encode("utf-16-le") + b"\0\0"
    mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ctypes.memmove(kernel32.GlobalLock(mem), data, len(data))
    kernel32.GlobalUnlock(mem)
    # OpenClipboard(NULL) 会让 SetClipboardData 失败，得给个窗口
    hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    try:
        for _ in range(20):  # 剪贴板可能正被别的程序占着
            if user32.OpenClipboard(hwnd):
                break
            time.sleep(0.05)
        else:
            kernel32.GlobalFree(mem)
            raise OSError("剪贴板被占用")
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, mem)
        finally:
            user32.CloseClipboard()
    finally:
        user32.DestroyWindow(hwnd)


class KeyHook:
    """低级键盘钩子：吞掉热键本身（含按住时的自动重复），把按下/松开事件丢进队列。"""

    def __init__(self, vk, events):
        self.vk, self.events = vk, events
        self.enabled, self.down = True, False
        self._proc = HOOKPROC(self._callback)  # 必须持有引用，被 GC 后系统再回调会直接崩
        threading.Thread(target=self._run, daemon=True).start()

    def _callback(self, n_code, w_param, l_param):
        if n_code == HC_ACTION and self.enabled:
            kb = KBDLLHOOKSTRUCT.from_address(l_param)
            # 只放行自己补发的短按；鼠标软件 / AutoHotkey 映射出来的热键（也算注入）照样能用
            if kb.vkCode == self.vk and kb.dwExtraInfo != MARK:
                if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not self.down:
                        self.down = True
                        self.events.put(("down", kb.scanCode, kb.flags))
                    return 1
                if self.down:  # 没见过按下的松开（比如暂停期间按下的）照常放行
                    self.down = False
                    self.events.put(("up",))
                    return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self):
        if not user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0):
            log.error("键盘钩子安装失败：%s", ctypes.WinError(ctypes.get_last_error()))
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # 钩子回调在这个消息循环里被调用
            pass


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
        self.key_name = cfg["hotkey"].lower()
        if self.key_name not in HOTKEYS:
            raise ValueError(f"不认识的热键 {cfg['hotkey']}，可选：{', '.join(HOTKEYS)}")
        self.vk = HOTKEYS[self.key_name]
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
            pystray.MenuItem("打开配置文件", lambda: os.startfile(CONFIG_PATH)),
            pystray.MenuItem("退出", self.quit),
        ))

    # ---------- 生命周期 ----------
    def run(self):
        self.icon.run(setup=self.startup)
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()

    def startup(self, icon):
        icon.visible = True
        self.hook = KeyHook(self.vk, self.events)
        threading.Thread(target=self.control_loop, daemon=True).start()
        threading.Thread(target=self.worker_loop, daemon=True).start()
        self.refresh()
        autostarted = False
        if not self.healthy() and self.cfg["server_cmd"]:
            self.starting = autostarted = True
            self.refresh()
            log_file = open(os.path.join(HERE, "server.log"), "ab")
            self.server_proc = subprocess.Popen(self.cfg["server_cmd"], stdout=log_file, stderr=subprocess.STDOUT,
                                                creationflags=subprocess.CREATE_NO_WINDOW)
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
            _, scan, flags = ev
            try:
                self.recorder.start()
            except Exception as e:
                log.exception("麦克风打开失败")
                self.notify(f"麦克风打开失败：{e}")
                continue
            try:
                self.events.get(timeout=self.tap)
            except queue.Empty:
                pass
            else:  # 阈值内就松开了：当普通按键补发回去
                ext = KEYEVENTF_EXTENDEDKEY if flags & LLKHF_EXTENDED else 0
                send_keys([(self.vk, scan, ext), (self.vk, scan, ext | KEYEVENTF_KEYUP)])
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
            # 记下松键时的前台窗口：识别回来如果用户已经切走了，就不往别处打字
            self.jobs.put((pcm, self.recorder.rate, user32.GetForegroundWindow()))
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
        if hwnd and user32.GetForegroundWindow() == hwnd:
            type_text(text)
        else:  # 识别期间切了窗口，或者是手动重试：放进剪贴板，别往不相干的地方打字
            set_clipboard(text)
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
        with self.ui_lock:
            self.status = text
            self.icon.icon = self.icons[name]
            self.icon.title = f"说说 · {text}"
            self.icon.update_menu()

    def notify(self, message):
        try:
            self.icon.notify(message, "说说")
        except Exception:
            log.exception("通知失败")

    def play(self, name):
        if self.sounds:
            winsound.PlaySound(self.sounds[name], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def main():
    handlers = [logging.handlers.RotatingFileHandler(os.path.join(HERE, "shuoshuo.log"), maxBytes=1 << 20,
                                                     backupCount=1, encoding="utf-8")]
    if sys.stderr:  # pythonw 下没有控制台
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    threading.excepthook = lambda a: log.error("线程异常", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    kernel32.CreateMutexW(None, False, "Local\\shuoshuo-voice-input")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS：已经开着一个了
        return
    try:
        cfg = configparser.ConfigParser(interpolation=None)  # 命令里可能有 %VAR%
        cfg.read_dict({"shuoshuo": DEFAULTS})
        cfg.read(CONFIG_PATH, encoding="utf-8-sig")  # 记事本可能存成带 BOM 的 UTF-8
        app = App(cfg["shuoshuo"])
    except Exception as e:
        log.exception("启动失败")
        user32.MessageBoxW(None, str(e), "说说 · 启动失败", 0x10)
        return
    app.run()


if __name__ == "__main__":
    main()
