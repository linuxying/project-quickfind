# -*- coding: utf-8 -*-
"""实测 exe 从启动到窗口出现（可见）的耗时。"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# 默认取「本脚本所在目录的上一级」里的 exe，也可以用参数指定
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "项目速查.exe")
TITLE = "项目速查"
u32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def find():
    hit = []

    def cb(hwnd, _l):
        n = u32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 2)
            u32.GetWindowTextW(hwnd, b, n + 2)
            if b.value == TITLE and u32.IsWindowVisible(hwnd):
                rc = wintypes.RECT()
                u32.GetWindowRect(hwnd, ctypes.byref(rc))
                if rc.right - rc.left > 400:
                    hit.append(hwnd)
        return True

    u32.EnumWindows(EnumProc(cb), None)
    return hit


for i in range(3):
    # 等上一轮窗口彻底消失，避免误判
    t_wait = time.time()
    while find() and time.time() - t_wait < 10:
        time.sleep(0.1)
    t0 = time.time()
    p = subprocess.Popen([EXE])
    seen = None
    while time.time() - t0 < 20:
        if find():
            seen = time.time() - t0
            break
        time.sleep(0.05)
    print("第 %d 次：窗口出现耗时 %s 秒" % (
        i + 1, ("%.2f" % seen) if seen else "超时未出现"))
    p.terminate()
    try:
        p.wait(timeout=6)
    except Exception:
        p.kill()
