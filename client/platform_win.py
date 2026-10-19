"""说说 · Windows 平台层：低级键盘钩子、SendInput、剪贴板、提示音。"""
import ctypes
import logging
import os
import struct
import subprocess
import threading
import time
import winsound
from ctypes import wintypes

log = logging.getLogger("shuoshuo")

HOTKEYS = {"capslock": 0x14, "rctrl": 0xA3, "ralt": 0xA5, "rshift": 0xA1, "pause": 0x13,
           "scrolllock": 0x91, "insert": 0x2D, **{f"f{i}": 0x6F + i for i in range(1, 25)}}
DEFAULT_HOTKEY = "capslock"

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


def replay_tap(vk, info):
    """短按：把吞掉的热键原样补发回去（CapsLock 照常切换大小写）。"""
    scan, flags = info
    ext = KEYEVENTF_EXTENDEDKEY if flags & LLKHF_EXTENDED else 0
    send_keys([(vk, scan, ext), (vk, scan, ext | KEYEVENTF_KEYUP)])


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


def foreground():
    return user32.GetForegroundWindow()


class KeyHook:
    """低级键盘钩子：吞掉热键本身（含按住时的自动重复），把按下/松开事件丢进队列。"""

    def __init__(self, vk, events, on_fail=None):
        self.vk, self.events, self.on_fail = vk, events, on_fail
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
                        self.events.put(("down", (kb.scanCode, kb.flags)))
                    return 1
                if self.down:  # 没见过按下的松开（比如暂停期间按下的）照常放行
                    self.down = False
                    self.events.put(("up",))
                    return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _run(self):
        if not user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0):
            log.error("键盘钩子安装失败：%s", ctypes.WinError(ctypes.get_last_error()))
            if self.on_fail:
                self.on_fail("键盘钩子安装失败")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # 钩子回调在这个消息循环里被调用
            pass


def play(path):
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def open_file(path):
    os.startfile(path)


def spawn(cmd, log_file):
    return subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)


def single_instance():
    kernel32.CreateMutexW(None, False, "Local\\shuoshuo-voice-input")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS：已经开着一个了


def alert(title, message):
    user32.MessageBoxW(None, message, title, 0x10)


def notify(icon, message):
    icon.notify(message, "说说")


def ui(fn):
    fn()


def data_dir(default):
    return default


def setup_app(cfg):
    pass
