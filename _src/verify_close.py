# -*- coding: utf-8 -*-
"""对打包好的 exe 做关闭行为端到端验证。

1. PostMessage(WM_CLOSE) → 主窗口应该弹出「关闭窗口时」询问框（而不是直接退出）
2. 关闭询问框（相当于点「取消」）→ 主窗口应该还活着
3. 收尾：结束进程
"""
import ctypes
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
MAIN, DIALOG = "项目速查", "关闭窗口时"


def windows(title, min_w=0):
    hit = []

    def cb(hwnd, _l):
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 2)
            u32.GetWindowTextW(hwnd, b, n + 2)
            if b.value == title and u32.IsWindowVisible(hwnd):
                rc = wintypes.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(rc))
                if rc.right - rc.left >= min_w:
                    hit.append(hwnd)
        return True

    u32.EnumWindows(EnumProc(cb), None)
    return hit


# 等主窗口
main = []
for _ in range(60):
    main = windows(MAIN, 400)
    if main:
        break
    time.sleep(0.25)
if not main:
    print("FAIL 主窗口没出现")
    sys.exit(2)
hwnd = main[0]
print("主窗口 hwnd =", hwnd)

print(">>> PostMessage(WM_CLOSE) 触发点 × 行为")
u32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE

dlg = []
for _ in range(30):
    dlg = windows(DIALOG, 100)
    if dlg:
        break
    time.sleep(0.2)

if dlg:
    print("OK   弹出了「%s」询问框" % DIALOG)
    print(">>> 关闭询问框（相当于点取消）")
    u32.PostMessageW(dlg[0], 0x0010, 0, 0)
    time.sleep(1.0)
    still = bool(windows(MAIN, 400))
    print("OK   取消后主窗口仍在" if still else "FAIL 取消后主窗口没了")
    code = 0 if still else 4
else:
    print("FAIL 没有弹出询问框（可能设过记忆值，或直接退出了）")
    code = 3

# 收尾：杀进程
pid = ctypes.c_ulong()
u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
if pid.value:
    h = k32.OpenProcess(0x0001, False, pid.value)   # PROCESS_TERMINATE
    if h:
        k32.TerminateProcess(h, 0)
        print("已结束进程 pid=%s" % pid.value)
sys.exit(code)
