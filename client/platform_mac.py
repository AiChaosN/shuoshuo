"""说说 · macOS 平台层：CGEventTap 热键、CGEvent 打字 / ⌘V 粘贴、NSPasteboard、菜单栏。

需要在「系统设置 → 隐私与安全性」里给运行它的程序（终端或 shuoshuo.app）授权：
辅助功能（吞热键、注入按键）、输入监控（监听热键）、麦克风。
"""
import ctypes
import fcntl
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import AppKit
import Quartz

log = logging.getLogger("shuoshuo")

# 修饰键只产生 flagsChanged 事件，按下/松开要看设备级的标志位（区分左右）
MODIFIERS = {"ralt": (61, 0x40), "rcmd": (54, 0x10), "rctrl": (62, 0x2000), "rshift": (60, 0x04),
             "fn": (63, 0x800000)}
FKEYS = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80]  # F1 ~ F19
HOTKEYS = {**{name: code for name, (code, _) in MODIFIERS.items()},
           **{f"f{i}": code for i, code in enumerate(FKEYS, 1)}}
DEFAULT_HOTKEY = "ralt"
MARK = 0x53485553  # 写进 kCGEventSourceUserData，钩子据此认出自己注入的按键
KEY_V = 9
CHUNK = 20  # CGEventKeyboardSetUnicodeString 一次最多带这么多个 UTF-16 单元

inject = "auto"
_lock_file = None
_sounds = {}


def data_dir(default):
    """打包成 .app 后配置和日志放到 ~/Library/Application Support/shuoshuo，第一次运行时放一份配置模板。"""
    if not getattr(sys, "frozen", False):
        return default
    path = os.path.expanduser("~/Library/Application Support/shuoshuo")
    os.makedirs(path, exist_ok=True)
    template = os.path.join(getattr(sys, "_MEIPASS", default), "config.example.ini")
    if not os.path.exists(os.path.join(path, "config.ini")) and os.path.exists(template):
        shutil.copy(template, os.path.join(path, "config.ini"))
    return path


def setup_app(cfg):
    global inject
    inject = cfg.get("mac_inject", "auto").lower()
    # 只在菜单栏显示，不占 Dock
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


def _on_main(fn, timeout=1.0):
    """在主线程上跑 fn 并取回结果（输入法相关的 TIS 接口只能在主线程调）。"""
    box, done = [], threading.Event()

    def run():
        try:
            box.append(fn())
        finally:
            done.set()

    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(run)
    if not done.wait(timeout) or not box:
        raise TimeoutError("主线程没响应")
    return box[0]


def ui(fn):
    """菜单栏图标只能在主线程上改，后台线程来的更新派发过去。"""
    if AppKit.NSThread.isMainThread():
        fn()
    else:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


# ---------- 输入法检测（Carbon TIS，pyobjc 没有封装）----------
_carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
_cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
_carbon.TISGetInputSourceProperty.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
_carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
_cf.CFEqual.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
_cf.CFEqual.restype = ctypes.c_bool
_cf.CFRelease.argtypes = (ctypes.c_void_p,)
_TYPE_KEY = ctypes.c_void_p.in_dll(_carbon, "kTISPropertyInputSourceType")
_TYPE_LAYOUT = ctypes.c_void_p.in_dll(_carbon, "kTISTypeKeyboardLayout")


def _ime_active():
    """当前是输入法（拼音之类）而不是单纯的键盘布局。"""
    src = _carbon.TISCopyCurrentKeyboardInputSource()
    try:
        return not _cf.CFEqual(_carbon.TISGetInputSourceProperty(src, _TYPE_KEY), _TYPE_LAYOUT)
    finally:
        _cf.CFRelease(src)


# ---------- 注入 ----------
def _post(down, code=0, flags=0, text=None):
    ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
    Quartz.CGEventSetFlags(ev, flags)  # 不带上用户手上还按着的修饰键
    Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGEventSourceUserData, MARK)
    if text is not None:
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _chunks(text):
    """按 UTF-16 长度切块，不把 emoji 的代理对切开。"""
    chunk, n = "", 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if n + w > CHUNK:
            yield chunk
            chunk, n = "", 0
        chunk, n = chunk + ch, n + w
    if chunk:
        yield chunk


def _type_unicode(text):
    for chunk in _chunks(text):
        _post(True, text=chunk)
        _post(False, text=chunk)
        time.sleep(0.005)  # 太快有的应用会丢


def _paste(text):
    """剪贴板 + ⌘V，粘完把用户原来的剪贴板内容放回去。"""
    pb = AppKit.NSPasteboard.generalPasteboard()
    saved = [{t: item.dataForType_(t) for t in item.types()} for item in (pb.pasteboardItems() or [])]
    set_clipboard(text)
    ours = pb.changeCount()
    _post(True, KEY_V, Quartz.kCGEventFlagMaskCommand)
    _post(False, KEY_V, Quartz.kCGEventFlagMaskCommand)
    time.sleep(0.3)  # 等目标应用读完剪贴板
    if pb.changeCount() == ours and saved:  # 这期间用户自己复制了东西就不覆盖
        pb.clearContents()
        items = []
        for types in saved:
            item = AppKit.NSPasteboardItem.alloc().init()
            for t, data in types.items():
                if data is not None:
                    item.setData_forType_(data, t)
            items.append(item)
        pb.writeObjects_(items)


def type_text(text):
    mode = inject
    if mode == "auto":  # 拼音等输入法开着时 Unicode 按键事件可能被输入法截走，改走粘贴
        try:
            mode = "paste" if _on_main(_ime_active) else "type"
        except Exception:
            mode = "paste"
    (_paste if mode == "paste" else _type_unicode)(text)


def replay_tap(code, info):
    """短按：修饰键单按本来就没作用，不用补；F 键补发回去。"""
    if code in FKEYS:
        _post(True, code)
        _post(False, code)


def set_clipboard(text):
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)


def foreground():
    """前台应用 pid + 它最前面那个普通窗口的编号，相当于 Windows 的 HWND。"""
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    pid = app.processIdentifier()
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
    for w in windows or []:
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer") == 0:
            return pid, w.get("kCGWindowNumber")
    return pid, None


# ---------- 热键 ----------
class KeyHook:
    """CGEventTap：吞掉热键本身（含按住时的自动重复），把按下/松开事件丢进队列。"""

    def __init__(self, code, events, on_fail=None):
        self.code, self.events, self.on_fail = code, events, on_fail
        self.mask = next((m for c, m in MODIFIERS.values() if c == code), None)
        self.enabled, self.down = True, False
        self.tap = None
        threading.Thread(target=self._run, daemon=True).start()

    def _callback(self, proxy, etype, event, refcon):
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            # 回调太慢或者 Secure Input 时系统会停掉 tap，不重新打开热键就悄悄失灵了
            log.warning("事件 tap 被系统停用，重新启用")
            Quartz.CGEventTapEnable(self.tap, True)
            return event
        if not self.enabled or Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == MARK:
            return event
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode) != self.code:
            return event
        if etype == Quartz.kCGEventFlagsChanged:
            pressed = bool(Quartz.CGEventGetFlags(event) & self.mask) if self.mask else False
        else:
            pressed = etype == Quartz.kCGEventKeyDown
        if pressed:
            if not self.down:
                self.down = True
                self.events.put(("down", None))
            return None
        if self.down:  # 没见过按下的松开（比如暂停期间按下的）照常放行
            self.down = False
            self.events.put(("up",))
            return None
        return event

    def _run(self):
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        warned = False
        while True:  # 没授权时等用户去系统设置里勾上，授权后不用重启
            self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                               Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
            if self.tap:
                break
            if not warned:
                warned = True
                log.error("键盘事件 tap 创建失败：缺少辅助功能 / 输入监控权限")
                Quartz.CGRequestListenEventAccess()
                Quartz.CGRequestPostEventAccess()
                if self.on_fail:
                    self.on_fail("需要授权：系统设置 → 隐私与安全性 → 辅助功能、输入监控")
            time.sleep(3)
        if warned:
            log.info("已获得键盘权限")
        src = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        Quartz.CFRunLoopRun()


# ---------- 杂项 ----------
def play(path):
    sound = _sounds.get(path)
    if sound is None:
        sound = _sounds[path] = AppKit.NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
    sound.stop()
    sound.play()


def open_file(path):
    subprocess.Popen(["open", "-t", path])


def spawn(cmd, log_file):
    return subprocess.Popen(shlex.split(cmd), stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)


def single_instance():
    global _lock_file
    _lock_file = open(os.path.join(tempfile.gettempdir(), "shuoshuo.lock"), "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:  # 已经开着一个了
        return False


def _osascript(script, *args):
    subprocess.Popen(["osascript", "-e", "on run argv", "-e", script, "-e", "end run", *args])


def alert(title, message):
    _osascript("display alert (item 1 of argv) message (item 2 of argv) as critical", title, message)


def notify(icon, message):
    # pystray 在 macOS 上的通知不可靠，直接用系统通知
    _osascript('display notification (item 1 of argv) with title "说说"', message)
