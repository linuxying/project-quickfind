# -*- coding: utf-8 -*-
"""按窗口标题截图（用于验证打包后的 exe 真实运行状态）。"""
import ctypes
import sys

import shot

title = sys.argv[1] if len(sys.argv) > 1 else "项目速查"
out = sys.argv[2] if len(sys.argv) > 2 else "shot_running.png"

u32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
found = []


def _cb(hwnd, _lp):
    n = u32.GetWindowTextLengthW(hwnd)
    if n:
        b = ctypes.create_unicode_buffer(n + 2)
        u32.GetWindowTextW(hwnd, b, n + 2)
        if b.value == title and u32.IsWindowVisible(hwnd):
            rc = ctypes.wintypes.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(rc))
            if rc.right - rc.left > 200 and rc.bottom - rc.top > 150:
                found.append(hwnd)
    return True


u32.EnumWindows(EnumProc(_cb), None)
allw = []


def _cb2(hwnd, _lp):
    n = u32.GetWindowTextLengthW(hwnd)
    if n:
        b = ctypes.create_unicode_buffer(n + 2)
        u32.GetWindowTextW(hwnd, b, n + 2)
        if title in b.value:
            cb = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, cb, 64)
            rc = ctypes.wintypes.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(rc))
            pid = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            allw.append((hwnd, b.value, cb.value, rc.right - rc.left,
                         rc.bottom - rc.top, u32.IsWindowVisible(hwnd), pid.value))
    return True


u32.EnumWindows(EnumProc(_cb2), None)
for w in allw:
    print("  候选 hwnd=%s title=%r class=%s %dx%d visible=%s pid=%s" % w)
if not found:
    print("NOT_FOUND")
    sys.exit(2)
hwnd = found[0]
print("hwnd =", hwnd)
rect = ctypes.wintypes.RECT()
ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
print("rect = (%d,%d)-(%d,%d)  %dx%d" % (
    rect.left, rect.top, rect.right, rect.bottom,
    rect.right - rect.left, rect.bottom - rect.top))
print("saved:", shot.capture(hwnd, out))
