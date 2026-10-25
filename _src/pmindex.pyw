# -*- coding: utf-8 -*-
"""项目速查 —— 输入合同编号/项目编号/项目名称，回车直接打开本地资料目录。

单文件、纯标准库（tkinter + ctypes），可打包为单 exe。
数据默认存 %APPDATA%\\项目速查\\pmindex.json；若 exe 同目录存在 pmindex.json 则优先用它（便携模式）。
"""
import ctypes
import json
import logging
import os
import queue
import re
import struct
import sys
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

# ============================ §0 常量 ============================
APP_TITLE = "项目速查"
APP_VERSION = "1.1.1"
SCHEMA_VERSION = 1
BACKUP_KEEP = 10                  # 备份保留份数
ENTER_IN_SEARCH_OPENS = True      # 搜索框回车=打开第一条匹配；改 False 则只把焦点移入列表
FONT = "Microsoft YaHei UI"

COLUMNS = [
    # key,  heading, 宽度, 对齐, stretch
    ("flag", "★",       42,  "center", False),
    ("customer", "客户", 116, "w", True),
    ("name", "项目名称", 400, "w", True),
    ("pno", "项目编号", 152, "w", False),
    ("cno", "合同编号", 140, "w", False),
    ("last", "最近打开", 128, "center", False),
    ("note", "备注", 150, "w", True),
]

DEFAULT_UI = {
    "geometry": "",
    "zoomed": False,
    "columns": {},          # 空 = 首次运行按字体实际测量自动定宽
    "sort": {"column": "", "desc": False},
    "close_action": "",     # ""=每次询问 / "exit"=直接退出 / "minimize"=最小化到任务栏
}

RE_CUST_PREFIX = re.compile(r"^(?:\d{6}|\d{4})[_\-－—\s\u3000]*")
RE_BRACKET = re.compile(r"^\s*[【\[]\s*([^】\]]+?)\s*[】\]]\s*(.+)$", re.S)
RE_BARE_PNO = re.compile(
    r"^([A-Z]{2,8}(?:[-–—]?\d{2,4}){2,4})[\s\-_、]*(\S.*)$", re.I)
RE_CONTRACT = [
    re.compile(r"合同编号\s*[：:]\s*([A-Za-z0-9\-]{6,})"),
    re.compile(r"[\(（]\s*([A-Z]{2,}[A-Z0-9\-]*\d{6,12})\s*[\)）]"),
    re.compile(r"[-_—–\s]([A-Z]{2,}[A-Z0-9\-]*\d{6,12})\s*$"),
]


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm(s):
    """NFKC 归一 + casefold：让全角括号/全角数字也能被半角输中。"""
    return unicodedata.normalize("NFKC", s or "").casefold()


# 搜索时剥掉词两端的标点：用户会把「（ABC2025001）」整个粘进来
TOKEN_EDGE = "()（）[]【】{}<>《》\"'“”‘’·,，、。;；:：!！?？_—-"
# 常用客户简称 → 全称（只在搜索时做一次简单的同义词展开）。
# 这张表只是内置示例，可按自己客户的叫法直接增删。
ABBR = {
    "农行": "中国农业银行", "工行": "中国工商银行", "建行": "中国建设银行",
    "中行": "中国银行", "交行": "交通银行", "招行": "招商银行",
    "邮储": "邮储银行", "中信": "中信银行", "民生": "民生银行",
    "兴业": "兴业银行", "光大": "光大银行", "广发": "广发银行",
    "浦发": "浦发银行",
}


def token_variants(t):
    """一个搜索词的可接受写法：原样 + 剥掉两端标点 + 简称展开。"""
    vs = {t}
    s = t.strip(TOKEN_EDGE)
    if s:
        vs.add(s)
    for v in list(vs):
        if v in ABBR:
            vs.add(norm(ABBR[v]))
    return vs


def norm_path(p):
    """规范路径：去引号、展开变量、normpath、去尾部分隔符（保留盘符根）。"""
    p = (p or "").strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1].strip()
    if not p:
        return ""
    p = os.path.expandvars(os.path.expanduser(p))
    try:
        p = os.path.normpath(p)
    except Exception:
        return p
    if len(p) > 3:
        p = p.rstrip("\\/")
    return p


def path_exists(p):
    try:
        return os.path.isdir(p) or os.path.isfile(p)
    except Exception:
        return False


def mask_path(p):
    """把家目录前缀换成 %APPDATA% 一类的占位符。

    界面上给用户看的路径一律过这个函数，统一显示成 %APPDATA% 这类占位符。
    """
    if not p:
        return p
    low = p.lower()
    for var, ph in (("APPDATA", "%APPDATA%"), ("LOCALAPPDATA", "%LOCALAPPDATA%"),
                    ("USERPROFILE", "%USERPROFILE%")):
        v = os.environ.get(var)
        if v and low.startswith(v.lower()):
            return ph + p[len(v):]
    try:
        home = os.path.expanduser("~")
        if home and home != "~" and low.startswith(home.lower()):
            return "~" + p[len(home):]
    except Exception:
        pass
    return p


# ============================ §1 Bootstrap ============================
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    env = os.environ.get("PMINDEX_DATA_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    base = app_dir()
    if os.path.isfile(os.path.join(base, "pmindex.json")):
        return base
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(root, "项目速查")
    os.makedirs(d, exist_ok=True)
    return d


def guard_stdio():
    """--windowed 下 sys.stdout/stderr 为 None，任何 print 都会炸。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def setup_logging(log_file):
    try:
        logging.basicConfig(
            filename=log_file, filemode="a", level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    except Exception:
        pass


def setup_dpi():
    """必须在 Tk() 之前调用，否则高分屏字体糊。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def system_dpi():
    try:
        d = ctypes.windll.user32.GetDpiForSystem()
        if d:
            return int(d)
    except Exception:
        pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        d = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return int(d or 96)
    except Exception:
        return 96


def install_excepthook(log_file):
    def hook(t, v, tb):
        logging.exception("未捕获异常", exc_info=(t, v, tb))
        try:
            messagebox.showerror(
                APP_TITLE, "%s: %s\n\n详情见日志：\n%s" % (t.__name__, v, log_file))
        except Exception:
            pass

    sys.excepthook = hook


def single_instance(title):
    """已在运行则前置已有窗口并返回 False。"""
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW(None, False, "Local\\pmindex_project_index_single")
        if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            u32 = ctypes.windll.user32
            hwnd = u32.FindWindowW(None, title)
            if hwnd:
                u32.ShowWindow(hwnd, 9)      # SW_RESTORE
                u32.SetForegroundWindow(hwnd)
            return False
        return True
    except Exception:
        logging.exception("mutex 失败，忽略")
        return True


# ============================ §2 数据层 ============================
class DataStore:
    def __init__(self, ddir):
        self.dir = ddir
        self.path = os.path.join(ddir, "pmindex.json")
        self.backup_dir = os.path.join(ddir, "backups")
        self.records = []
        self.customers = []
        self.ui = json.loads(json.dumps(DEFAULT_UI))
        self.meta = {}
        self.readonly = False
        self._by_id = {}
        self.recovered_from = ""

    # ---- 读写 ----
    def load(self):
        data = None
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("records", []), list):
                    raise ValueError("结构不合法")
            except Exception:
                logging.exception("主数据损坏，尝试从备份恢复")
                data = self._recover()
        if data is None:
            data = {"schema_version": SCHEMA_VERSION, "records": [], "customers": [],
                    "ui": {}, "meta": {"created_at": now_str()}}
        try:
            if int(data.get("schema_version", 1) or 1) > SCHEMA_VERSION:
                self.readonly = True
                logging.warning("数据版本高于程序，进入只读模式")
        except Exception:
            pass
        self._apply(data)

    def _recover(self):
        try:
            bad = self.path + ".bad-" + datetime.now().strftime("%Y%m%d%H%M%S")
            os.replace(self.path, bad)
            logging.info("坏文件已改名: %s", bad)
        except Exception:
            logging.exception("改名坏文件失败")
        for p in self._backup_files():
            try:
                with open(p, "r", encoding="utf-8-sig") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.recovered_from = os.path.basename(p)
                    logging.info("已从备份恢复: %s", p)
                    return d
            except Exception:
                continue
        return None

    def _backup_files(self):
        if not os.path.isdir(self.backup_dir):
            return []
        fs = [os.path.join(self.backup_dir, f) for f in os.listdir(self.backup_dir)
              if f.lower().endswith(".json")]
        fs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return fs

    def _apply(self, data):
        self.records = []
        for r in data.get("records", []):
            if not isinstance(r, dict):
                continue
            rec = {
                "id": str(r.get("id") or uuid.uuid4().hex[:12]),
                "customer": str(r.get("customer", "") or ""),
                "name": str(r.get("name", "") or ""),
                "project_no": str(r.get("project_no", "") or ""),
                "contract_no": str(r.get("contract_no", "") or ""),
                "path": str(r.get("path", "") or ""),
                "note": str(r.get("note", "") or ""),
                "pinned": bool(r.get("pinned", False)),
                "open_count": int(r.get("open_count", 0) or 0),
                "created_at": str(r.get("created_at", "") or ""),
                "updated_at": str(r.get("updated_at", "") or ""),
                "last_opened": str(r.get("last_opened", "") or ""),
            }
            self.records.append(rec)
        self._by_id = {r["id"]: r for r in self.records}

        cs = [str(c) for c in (data.get("customers") or []) if str(c).strip()]
        for r in self.records:
            if r["customer"] and r["customer"] not in cs:
                cs.append(r["customer"])
        self.customers = cs

        ui = data.get("ui") or {}
        self.ui = json.loads(json.dumps(DEFAULT_UI))
        for k in ("geometry", "zoomed"):
            if k in ui:
                self.ui[k] = ui[k]
        if isinstance(ui.get("columns"), dict):
            for k, v in ui["columns"].items():
                try:
                    self.ui["columns"][k] = int(v)
                except Exception:
                    pass
        if isinstance(ui.get("sort"), dict):
            self.ui["sort"]["column"] = str(ui["sort"].get("column", "") or "")
            self.ui["sort"]["desc"] = bool(ui["sort"].get("desc", False))
        ca = str(ui.get("close_action", "") or "")
        self.ui["close_action"] = ca if ca in ("exit", "minimize") else ""
        self.meta = data.get("meta") or {}
        self.meta.setdefault("created_at", now_str())

    # ---- 备份 ----
    def _snapshot(self, force=False):
        """把当前数据文件快照一份（内容与最新备份相同则跳过），只保留最近 BACKUP_KEEP 份。

        策略：写完立刻快照，保证备份永远不落后于真实数据；靠内容比对去重，不靠时间节流。
        """
        if not os.path.isfile(self.path):
            return False
        try:
            with open(self.path, "rb") as f:
                cur = f.read()
        except Exception:
            logging.exception("读取数据文件失败")
            return False
        os.makedirs(self.backup_dir, exist_ok=True)
        files = self._backup_files()
        if not force and files:
            try:
                with open(files[0], "rb") as f:
                    if f.read() == cur:
                        return False
            except Exception:
                pass
        base = "pmindex-%s" % datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(self.backup_dir, base + ".json")
        i = 1
        while os.path.exists(dst):
            dst = os.path.join(self.backup_dir, "%s-%d.json" % (base, i))
            i += 1
        try:
            with open(dst, "wb") as f:
                f.write(cur)
        except Exception:
            logging.exception("写备份失败")
            return False
        self.meta["last_backup"] = now_str()
        for old in self._backup_files()[BACKUP_KEEP:]:
            try:
                os.remove(old)
            except Exception:
                pass
        return True

    def save(self):
        if self.readonly:
            return False
        payload = {
            "schema_version": SCHEMA_VERSION,
            "records": [{k: v for k, v in r.items() if not k.startswith("_")}
                        for r in self.records],
            "customers": self.customers,
            "ui": self.ui,
            "meta": self.meta,
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            logging.exception("写临时文件失败")
            return False
        for _ in range(3):
            try:
                os.replace(tmp, self.path)
                try:
                    self._snapshot()
                except Exception:
                    logging.exception("快照异常")
                return True
            except PermissionError:
                time.sleep(0.05)
            except Exception:
                logging.exception("替换数据文件失败")
                break
        logging.error("保存失败，临时文件保留：%s", tmp)
        return False

    # ---- 增删改 ----
    def get(self, rid):
        return self._by_id.get(rid)

    def by_path(self, path):
        k = os.path.normcase(norm_path(path))
        for r in self.records:
            if os.path.normcase(norm_path(r["path"])) == k:
                return r
        return None

    def add(self, **kw):
        rec = {
            "id": uuid.uuid4().hex[:12], "customer": "", "name": "", "project_no": "",
            "contract_no": "", "path": "", "note": "", "pinned": False, "open_count": 0,
            "created_at": now_str(), "updated_at": now_str(), "last_opened": "",
        }
        rec.update(kw)
        rec["path"] = norm_path(rec["path"])
        self.records.append(rec)
        self._by_id[rec["id"]] = rec
        self._remember_customer(rec["customer"])
        self.save()
        return rec

    def update(self, rid, **kw):
        rec = self.get(rid)
        if not rec:
            return None
        if "path" in kw:
            kw["path"] = norm_path(kw["path"])
        rec.update(kw)
        rec["updated_at"] = now_str()
        self._remember_customer(rec["customer"])
        self.save()
        return rec

    def remove(self, rid):
        rec = self.get(rid)
        if not rec:
            return
        self.records = [r for r in self.records if r["id"] != rid]
        self._by_id.pop(rid, None)
        self.save()

    def toggle_pin(self, rid):
        rec = self.get(rid)
        if rec:
            rec["pinned"] = not rec["pinned"]
            self.save()

    def touch(self, rid):
        rec = self.get(rid)
        if rec:
            rec["last_opened"] = now_str()
            rec["open_count"] = int(rec.get("open_count", 0)) + 1
            self.save()

    def _remember_customer(self, c):
        c = (c or "").strip()
        if not c:
            return
        if c in self.customers:
            self.customers.remove(c)
        self.customers.insert(0, c)


# ============================ §3 解析 ============================
def strip_customer_prefix(dirname):
    s = (dirname or "").strip()
    if not s:
        return ""
    out = RE_CUST_PREFIX.sub("", s).strip(" _-－—\u3000")
    return out or s


def extract_contract_no(s):
    for pat in RE_CONTRACT:
        m = pat.search(s or "")
        if m:
            return m.group(1).strip()
    return ""


def _strip_trailing_contract(name, cno):
    """名称尾部如果就是合同号（如 -AB2501080016 / (ABC2025001) ），去掉以免和合同编号列重复。"""
    if not name or not cno:
        return name
    pat = r"[\s\-_—–]*[\(（]?\s*" + re.escape(cno) + r"\s*[\)）]?\s*$"
    if re.search(pat, name, re.I):
        out = re.sub(pat, "", name, flags=re.I).rstrip(" -_—–")
        return out or name
    return name


def parse_project(folder_name):
    """目录名 → (项目编号, 项目名称, 合同编号)。尽力而为，失败时名称取原名。"""
    s = (folder_name or "").strip()
    if not s:
        return "", "", ""
    no = name = ""
    m = RE_BRACKET.match(s)
    if m:
        no, name = m.group(1).strip(), m.group(2).strip()
    else:
        m = RE_BARE_PNO.match(s)
        if m:
            no, name = m.group(1).strip(), m.group(2).strip()
        else:
            name = s
    cno = extract_contract_no(s)
    return no, _strip_trailing_contract(name, cno), cno


def infer_from_path(path):
    p = norm_path(path)
    base = os.path.basename(p)
    parent = os.path.basename(os.path.dirname(p))
    no, name, cno = parse_project(base)
    if RE_CUST_PREFIX.match(base):
        cust = strip_customer_prefix(base)
    else:
        cust = strip_customer_prefix(parent)
    if not name:
        name = base
    return {"customer": cust, "name": name, "project_no": no,
            "contract_no": cno, "path": p}


# ============================ §7 系统集成 ============================
def open_target(path):
    """目录 → 资源管理器；文件 → 默认程序。"""
    p = norm_path(path)
    if not p:
        return False
    try:
        os.startfile(p)
        return True
    except Exception:
        logging.exception("startfile 失败: %s", p)
    try:
        os.startfile(p, "explore")
        return True
    except Exception:
        logging.exception("startfile explore 失败: %s", p)
    return False


def reveal_in_parent(path):
    p = norm_path(path)
    if not p:
        return False
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "open", "explorer.exe", '/select,"%s"' % p.replace('"', ""), None, 1)
        return True
    except Exception:
        logging.exception("reveal 失败: %s", p)
        return False


def copy_text(widget, text):
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
        widget.update_idletasks()
        widget.update()
        return True
    except Exception:
        logging.exception("复制失败")
        return False


# ---- 拖拽文件夹（WM_DROPFILES + 子类化窗口过程）----
#
# 纪律（踩过坑，必须遵守）：
#   窗口过程里只允许做 DragQueryFile / DragFinish，把路径丢进队列；
#   绝不调 Tk/Tcl（包括 after_idle 里开模态窗口）——那等于在 Win32 消息派发中途
#   再开一层 Tcl 事件循环，会把 CRT 栈搞坏，进程直接 0xC0000409 硬崩（ucrtbase/BEX），
#   Python 层连异常都抓不到。弹窗一律交给主线程的 after 定时器。
#
# 逃生开关：exe 旁边或数据目录放一个 nodrag.txt，就不装拖拽（粘贴路径照常可用）。

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
_pending_drops = []           # 窗口过程写入 → 主线程消费
_drop_keepalive = []          # 回调对象必须全局持有，否则 GC 后被调用即崩
_drop_state = {"installed": False, "old": None, "hwnd": 0}
_drop_buf = ctypes.create_unicode_buffer(32768)

_IS64 = ctypes.sizeof(ctypes.c_void_p) == 8
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_s32 = ctypes.WinDLL("shell32", use_last_error=True)

_DragQueryFileW = _s32.DragQueryFileW
_DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
_DragQueryFileW.restype = wintypes.UINT
_DragFinish = _s32.DragFinish
_DragFinish.argtypes = [wintypes.HANDLE]
_DragFinish.restype = None
_DragAcceptFiles = _s32.DragAcceptFiles
_DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
_DragAcceptFiles.restype = None
_CallWndProc = _u32.CallWindowProcW
_CallWndProc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                         wintypes.WPARAM, wintypes.LPARAM]
_CallWndProc.restype = ctypes.c_ssize_t

if _IS64:
    _GetWL = _u32.GetWindowLongPtrW
    _SetWL = _u32.SetWindowLongPtrW
    _WL_RET = ctypes.c_ssize_t
else:
    _GetWL = _u32.GetWindowLongW
    _SetWL = _u32.SetWindowLongW
    _WL_RET = ctypes.c_long
_GetWL.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWL.restype = _WL_RET
_SetWL.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_SetWL.restype = _WL_RET

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


def _toplevel_hwnd(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    for _ in range(8):
        _u32.GetClassNameW(hwnd, buf, 256)
        if buf.value != "TkChild":
            return hwnd
        parent = _u32.GetParent(hwnd)
        if not parent:
            return hwnd
        hwnd = parent
    return hwnd


def _drop_wndproc(h, msg, wp, lp):
    """窗口过程：只入队，不碰 Tk，异常一律吞掉。"""
    if msg == WM_DROPFILES:
        try:
            hdrop = wintypes.HANDLE(wp)
            n = _DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            for i in range(min(int(n or 0), 10)):
                if _DragQueryFileW(hdrop, i, _drop_buf, len(_drop_buf)) and _drop_buf.value:
                    _pending_drops.append(_drop_buf.value)
        except Exception:
            pass
        finally:
            try:
                _DragFinish(wintypes.HANDLE(wp))
            except Exception:
                pass
        return 0
    try:
        return _CallWndProc(_drop_state["old"], h, msg, wp, lp)
    except Exception:
        return 0


def install_folder_drop(widget):
    """窗口映射之后调用；幂等。返回是否安装成功。"""
    if _drop_state["installed"]:
        return True
    try:
        hwnd = _toplevel_hwnd(widget.winfo_id())
        if not hwnd:
            return False
        _DragAcceptFiles(hwnd, True)
        old = _GetWL(hwnd, GWLP_WNDPROC)
        cb = WNDPROC(_drop_wndproc)
        _drop_keepalive.append(cb)
        _SetWL(hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
        _drop_state.update(installed=True, old=old, hwnd=hwnd)
        logging.info("拖拽已启用 hwnd=%s", hwnd)
        return True
    except Exception:
        logging.exception("拖拽功能安装失败")
        return False


def drop_disabled_by_user():
    """逃生开关：exe 旁边或数据目录里放一个 nodrag.txt 就禁用拖拽。"""
    try:
        for d in (app_dir(), data_dir()):
            if os.path.isfile(os.path.join(d, "nodrag.txt")):
                return True
    except Exception:
        pass
    return False


# ============================ §6 编辑对话框 ============================
class RecordDialog(tk.Toplevel):
    def __init__(self, parent, customers, prefill=None, record=None):
        super().__init__(parent)
        self.result = None
        self.record = record
        self.cancel_flag = True
        self.title("编辑记录" if record else "新增记录")
        self.resizable(True, False)
        self.transient(parent)

        src = record or prefill or {}
        self.v_cust = tk.StringVar(value=src.get("customer", ""))
        self.v_name = tk.StringVar(value=src.get("name", ""))
        self.v_pno = tk.StringVar(value=src.get("project_no", ""))
        self.v_cno = tk.StringVar(value=src.get("contract_no", ""))
        self.v_path = tk.StringVar(value=src.get("path", ""))
        self.v_pin = tk.BooleanVar(value=bool(src.get("pinned", False)))

        body = ttk.Frame(self, padding=(14, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        rows = [
            ("客户", ttk.Combobox(body, textvariable=self.v_cust, values=customers)),
            ("项目名称", ttk.Entry(body, textvariable=self.v_name)),
            ("项目编号", ttk.Entry(body, textvariable=self.v_pno)),
            ("合同编号", ttk.Entry(body, textvariable=self.v_cno)),
        ]
        r = 0
        self.w_cust = rows[0][1]
        for label, widget in rows:
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="e", padx=(0, 8), pady=5)
            widget.grid(row=r, column=1, columnspan=2, sticky="ew", pady=5)
            r += 1

        ttk.Label(body, text="资料路径").grid(row=r, column=0, sticky="e", padx=(0, 8), pady=5)
        self.e_path = ttk.Entry(body, textvariable=self.v_path)
        self.e_path.grid(row=r, column=1, sticky="ew", pady=5)
        ttk.Button(body, text="选择…", width=7, command=self._pick_dir).grid(
            row=r, column=2, sticky="e", padx=(6, 0), pady=5)
        r += 1

        ttk.Label(body, text="备注").grid(row=r, column=0, sticky="ne", padx=(0, 8), pady=5)
        self.t_note = tk.Text(body, height=3, wrap="word", font=(FONT, 9),
                              relief="solid", borderwidth=1, highlightthickness=0)
        self.t_note.grid(row=r, column=1, columnspan=2, sticky="ew", pady=5)
        self.t_note.insert("1.0", src.get("note", "") or "")
        r += 1

        ttk.Checkbutton(body, text="置顶（始终排在列表最前）",
                        variable=self.v_pin).grid(row=r, column=1, sticky="w", pady=(2, 0))

        btns = ttk.Frame(self, padding=(14, 0, 14, 12))
        btns.pack(fill="x")
        ttk.Button(btns, text="取消", width=9, command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="保存", width=9, command=self._ok).pack(side="right")

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<Control-Return>", lambda e: self._ok())
        for w in (self.w_cust, self.e_path):
            w.bind("<FocusOut>", self._maybe_fill)
        self.e_path.bind("<Return>", lambda e: self._maybe_fill_then_ok())

        self.update_idletasks()
        self._center(parent)
        self.w_cust.focus_set()
        self._show_path_tail()

    def _show_path_tail(self):
        """路径很长时让输入框显示末尾（能一眼看出是哪一层目录）。"""
        try:
            self.e_path.xview_moveto(1.0)
            self.after(80, lambda: self.e_path.xview_moveto(1.0))
        except Exception:
            pass

    def _center(self, parent):
        try:
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            w, h = max(self.winfo_width(), 640), self.winfo_height()
            self.geometry("+%d+%d" % (px + (pw - w) // 2, py + (ph - h) // 3))
        except Exception:
            pass

    def _pick_dir(self):
        d = filedialog.askdirectory(parent=self, title="选择项目资料目录")
        if d:
            self.v_path.set(norm_path(d))
            self._maybe_fill(auto_name=True)
            self._show_path_tail()

    def _maybe_fill(self, _evt=None):
        self._maybe_fill_impl(False)

    def _maybe_fill_then_ok(self):
        self._maybe_fill_impl(True)
        self._ok()

    def _maybe_fill_impl(self, auto_name):
        p = norm_path(self.v_path.get())
        if not p or not os.path.isdir(p):
            return
        info = infer_from_path(p)
        if not self.v_cust.get().strip():
            self.v_cust.set(info["customer"])
        if not self.v_pno.get().strip():
            self.v_pno.set(info["project_no"])
        if not self.v_cno.get().strip():
            self.v_cno.set(info["contract_no"])
        if auto_name and not self.v_name.get().strip():
            self.v_name.set(info["name"])

    def _ok(self):
        path = norm_path(self.v_path.get())
        name = self.v_name.get().strip()
        if not path:
            messagebox.showwarning(APP_TITLE, "请填写资料路径。", parent=self)
            return
        if not path_exists(path):
            if not messagebox.askyesno(
                    APP_TITLE, "该路径当前不存在：\n%s\n\n仍然保存？" % mask_path(path), parent=self):
                return
        if not name:
            name = os.path.basename(path) or path
        self.result = {
            "customer": self.v_cust.get().strip(),
            "name": name,
            "project_no": self.v_pno.get().strip(),
            "contract_no": self.v_cno.get().strip(),
            "path": path,
            "note": self.t_note.get("1.0", "end-1c").strip(),
            "pinned": bool(self.v_pin.get()),
        }
        self.cancel_flag = False
        self.destroy()

    def _cancel(self):
        self.cancel_flag = True
        self.destroy()

    def show(self):
        self.wait_visibility()
        self.grab_set()
        self.wait_window()
        return None if self.cancel_flag else self.result


# ============================ §5 主窗口 ============================
class App:
    def __init__(self, root, store):
        self.root = root
        self.store = store
        self.dpi = system_dpi()
        self.scale = self.dpi / 96.0
        self.path_state = {}       # id -> "ok" / "missing" / "unknown"
        self.closing = False
        self._poll_job = None
        self._debounce_job = None
        self._drop_job = None
        self._dialog_busy = False
        self._visible = []
        self.v_close_mode = tk.StringVar(value=store.ui.get("close_action") or "")

        self._setup_style()
        self._build_ui()
        self._build_menu()
        self._bind_keys()
        self._apply_saved_ui()
        self.refresh(select_first=True, reset_scroll=True)
        self.start_path_check()
        self.root.after(80, self._initial_focus)
        self.root.after(160, self._fit_px)
        self.root.after(250, self._start_drop)

    # ---- 拖拽接入（窗口映射后再装，避免拿到无效 hwnd）----
    def _start_drop(self):
        if self.closing:
            return
        if drop_disabled_by_user():
            logging.info("检测到 nodrag.txt，已跳过拖拽功能")
            return
        if not install_folder_drop(self.root):
            logging.info("拖拽不可用，走粘贴兜底")
            return
        self._poll_drops()

    def _poll_drops(self):
        """主线程消费窗口过程投递的路径——弹窗只在这里开，绝不在窗口过程里开。"""
        if self.closing:
            return
        while _pending_drops:
            try:
                p = _pending_drops.pop(0)
            except IndexError:
                break
            try:
                self._on_dropped([p])
            except Exception:
                logging.exception("处理拖入的路径失败: %s", p)
        self._drop_job = self.root.after(250, self._poll_drops)

    def _open_dialog(self, record=None, prefill=None):
        """统一入口：同时只允许一个编辑框，避免嵌套模态把事件循环搞乱。"""
        if self._dialog_busy:
            return None
        self._dialog_busy = True
        try:
            dlg = RecordDialog(self.root, self.store.customers,
                               prefill=prefill, record=record)
            return dlg.show()
        finally:
            self._dialog_busy = False

    def _initial_focus(self):
        """启动即把光标放进搜索框，打开就能打字。"""
        try:
            self.e_search.focus_force()
        except Exception:
            pass
        self._sync_placeholder()

    # ---- 外观 ----
    def _setup_style(self):
        r = self.root
        r.title(APP_TITLE)
        try:
            r.tk.call("tk", "scaling", self.dpi / 72.0)
        except Exception:
            pass
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except Exception:
            pass
        row_h = max(22, int(26 * self.scale))
        style.configure("Treeview", font=(FONT, 10), rowheight=row_h,
                        background="#FFFFFF", fieldbackground="#FFFFFF", borderwidth=0)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), padding=(6, 5))
        style.configure("TLabel", font=(FONT, 9))
        style.configure("TButton", font=(FONT, 9))
        style.configure("Hint.TLabel", font=(FONT, 9), foreground="#8A8A8A")

    def _auto_widths(self):
        """按当前 DPI 下 10pt 字体的真实像素宽度定列宽（不靠硬编码猜）。

        客户/项目名称/备注 三列取「已有数据里最长的实际取值」，避免一上来就截断。
        """
        try:
            f = tkfont.Font(font=(FONT, 10))
            m = f.measure
        except Exception:
            m = lambda s: len(s) * 7  # noqa: E731
        pad = int(20 * self.scale)

        def widest(field, sample, floor, ceiling):
            vals = [r.get(field, "") for r in self.store.records[:300]]
            w = max([m(v) for v in vals] + [m(sample)])
            return max(floor, min(w, ceiling)) + pad

        return {
            "flag": max(40, int(34 * self.scale)),
            "customer": widest("customer", "示例客户名称八个字",
                               int(90 * self.scale), int(240 * self.scale)),
            "name": widest("name", "示例项目名称：某某系统建设与维保服务项目合同",
                           int(260 * self.scale), int(560 * self.scale)),
            "pno": max(m("PRJ-2024-0101-0001"), m("项目编号")) + pad,
            "cno": max(m("ABC2025010001"), m("合同编号")) + pad,
            "last": max(m("2026-10-24 18:03"), m("最近打开")) + pad,
            "note": widest("note", "备注示例，联系人某某",
                           int(100 * self.scale), int(260 * self.scale)),
        }

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=(10, 8, 10, 6))
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(1, weight=1)

        top = ttk.Frame(outer)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="搜索").grid(row=0, column=0, padx=(0, 6))
        self.q = tk.StringVar()
        self.e_search = tk.Entry(top, textvariable=self.q, font=(FONT, 11),
                                 relief="solid", borderwidth=1, highlightthickness=0)
        self.e_search.grid(row=0, column=1, sticky="ew", ipady=3)
        ttk.Button(top, text="＋ 新增  Ctrl+N", command=self.new_record).grid(
            row=0, column=2, padx=(8, 0))

        # 输入提示（浮在输入框上，有内容就消失）
        self.ph = tk.Label(top, text="输入 合同编号 / 项目编号 / 项目名称 / 客户，回车直接打开目录",
                           bg="#FFFFFF", fg="#A6A6A6", font=(FONT, 10), bd=0, anchor="w")
        self.ph.place(in_=self.e_search, x=7, rely=0.5, anchor="w")
        self.ph.bind("<Button-1>", lambda e: self.e_search.focus_set())

        cols = [c[0] for c in COLUMNS]
        self.tree = ttk.Treeview(outer, columns=cols, show="headings", selectmode="browse")
        widths = self._auto_widths()
        for key, head, width, anchor, stretch in COLUMNS:
            self.tree.heading(key, text=head, anchor=anchor,
                              command=lambda k=key: self.on_heading_click(k))
            self.tree.column(key, width=int(widths.get(key, width * self.scale)),
                             minwidth=36, anchor=anchor, stretch=stretch)
        self.tree.grid(row=1, column=0, columnspan=2, sticky="nsew")
        sb = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=2, sticky="ns")
        hsb = ttk.Scrollbar(outer, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.hsb = hsb
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=hsb.set)
        self.tree.bind("<Configure>", self._sync_hsb, add="+")

        self.empty_hint = tk.Label(
            outer,
            text="还没有记录\n\n把项目文件夹从资源管理器拖进本窗口，会自动填好客户名和项目编号\n"
                 "也可以点右上角「＋ 新增」，或用 Ctrl+V 粘贴路径",
            bg="#FFFFFF", fg="#8C8C8C", font=(FONT, 10), justify="center", bd=0)
        self.empty_hint.bind("<Button-1>", lambda e: self.new_record())

        self.tree.tag_configure("missing", foreground="#B03A2E")
        self.tree.tag_configure("unknown", foreground="#8A8A8A")

        bottom = ttk.Frame(outer)
        bottom.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        bottom.columnconfigure(0, weight=1)
        self.lbl_status = ttk.Label(bottom, text="", style="Hint.TLabel")
        self.lbl_status.grid(row=0, column=0, sticky="w")
        ttk.Button(bottom, text="重新检查路径", command=self.start_path_check).grid(
            row=0, column=1, padx=(8, 0))

        self.q.trace_add("write", lambda *a: self._on_query_changed())
        self.e_search.bind("<Return>", self._on_search_enter)
        self.e_search.bind("<Down>", lambda e: (self._move(1), "break")[1])
        self.e_search.bind("<Up>", lambda e: (self._move(-1), "break")[1])
        self.e_search.bind("<Control-v>", self._on_paste)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda e: self.open_selected())
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_status())

    def _build_menu(self):
        bar = tk.Menu(self.root)
        m_file = tk.Menu(bar, tearoff=0)
        m_file.add_command(label="新增记录", accelerator="Ctrl+N", command=self.new_record)
        m_file.add_separator()
        m_file.add_command(label="立即备份", command=self.do_backup)
        m_file.add_command(label="从备份恢复…", command=self.do_restore)
        m_file.add_command(label="打开数据文件位置", command=self.open_data_dir)
        m_file.add_separator()

        m_close = tk.Menu(m_file, tearoff=0)
        m_close.add_radiobutton(label="每次询问", variable=self.v_close_mode, value="",
                                command=self._save_close_mode)
        m_close.add_radiobutton(label="直接退出", variable=self.v_close_mode, value="exit",
                                command=self._save_close_mode)
        m_close.add_radiobutton(label="最小化到任务栏", variable=self.v_close_mode,
                                value="minimize", command=self._save_close_mode)
        m_file.add_cascade(label="点 × 时", menu=m_close)
        m_file.add_command(label="退出", accelerator="Ctrl+Q", command=self.quit_app)
        bar.add_cascade(label="文件", menu=m_file)

        m_help = tk.Menu(bar, tearoff=0)
        m_help.add_command(label="快捷键与用法", command=self.show_help)
        m_help.add_command(label="关于", command=self.show_about)
        bar.add_cascade(label="帮助", menu=m_help)
        self.root.config(menu=bar)

    def _save_close_mode(self):
        self.store.ui["close_action"] = self.v_close_mode.get()
        self.store.save()

    def _bind_keys(self):
        r = self.root
        r.bind("<Control-n>", lambda e: self.new_record())
        r.bind("<Control-f>", lambda e: self.focus_search())
        r.bind("<F2>", lambda e: self.edit_selected())
        r.bind("<Delete>", lambda e: self.delete_selected())
        r.bind("<Escape>", self._on_escape)
        r.bind("<Control-q>", lambda e: self.quit_app())
        r.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- 搜索/视图 ----
    def _fit_px(self):
        """窗口映射后再校一次：列装不下就把窗口放宽（只放大，不缩小，尊重用户手调）。"""
        try:
            need = sum(self.tree.column(k, "width") for k in [c[0] for c in COLUMNS])
            need += int(40 * self.scale)
            cw, _ch = self._physical_client()
            lw = self.root.winfo_width()
            logging.debug("fit_px: need=%s cw=%s lw=%s", need, cw, lw)
            if not cw or not lw or cw + 8 >= need:
                return
            ratio = cw / float(lw)
            sw = self.root.winfo_screenwidth()
            target_tk = min(int(need / ratio) + 4, int((sw - 40) / ratio))
            h = self.root.winfo_height()
            self.root.geometry("%dx%d" % (target_tk, h))
        except Exception:
            logging.exception("窗口宽度校正失败")

    def _sync_empty_hint(self):
        try:
            if self.store.records:
                self.empty_hint.place_forget()
            else:
                self.empty_hint.place(in_=self.tree, relx=0.5, rely=0.35, anchor="center")
                self.empty_hint.lift()
        except Exception:
            pass

    def _sync_hsb(self, _evt=None):
        """列装得下就把横向滚动条藏起来，界面干净些。"""
        try:
            a, b = self.tree.xview()
            need = not (a <= 0.002 and b >= 0.998)
            if need and not self.hsb.winfo_ismapped():
                self.hsb.grid()
            elif not need and self.hsb.winfo_ismapped():
                self.hsb.grid_remove()
        except Exception:
            pass

    def _sync_placeholder(self):
        try:
            if self.q.get():
                self.ph.place_forget()
            else:
                self.ph.place(in_=self.e_search, x=7, rely=0.5, anchor="w")
                self.ph.lift()
        except Exception:
            pass

    def _on_query_changed(self):
        self._sync_placeholder()
        if self._debounce_job:
            try:
                self.root.after_cancel(self._debounce_job)
            except Exception:
                pass
        self._debounce_job = self.root.after(120, lambda: self.refresh(select_first=True))

    def _haystack(self, rec):
        hs = rec.get("_hs")
        if hs is None:
            hs = " ".join(norm(rec.get(k, "")) for k in
                          ("customer", "name", "project_no", "contract_no", "note", "path"))
            rec["_hs"] = hs
        return hs

    def _visible_records(self):
        q = norm(self.q.get().strip())
        tokens = [t for t in re.split(r"[\s\u3000]+", q) if t]
        recs = self.store.records
        if tokens:
            groups = [token_variants(t) for t in tokens]
            recs = [r for r in recs
                    if all(any(v in self._haystack(r) for v in g) for g in groups)]
        return self._sorted(recs)

    def _sort_key(self, col):
        if col == "last":
            return lambda r: norm(r.get("last_opened", ""))
        if col == "flag":
            return lambda r: (0 if r.get("pinned") else 1, norm(r.get("name", "")))
        return lambda r: norm(r.get(col, "")) if col in ("customer", "name", "note") \
            else norm(r.get(col, ""))

    def _sorted(self, recs):
        col = self.store.ui["sort"]["column"]
        desc = bool(self.store.ui["sort"]["desc"])
        if col:
            key = self._sort_key(col)
        else:
            def key(r):
                ts = r.get("_ts")
                if ts is None:
                    ts = 0.0
                    if r.get("last_opened"):
                        try:
                            ts = time.mktime(time.strptime(
                                r["last_opened"], "%Y-%m-%d %H:%M:%S"))
                        except Exception:
                            ts = 0.0
                    r["_ts"] = ts
                return (-ts, norm(r.get("customer", "")), norm(r.get("name", "")))
        pins = [r for r in recs if r.get("pinned")]
        rest = [r for r in recs if not r.get("pinned")]
        pins.sort(key=key, reverse=desc)
        rest.sort(key=key, reverse=desc)
        return pins + rest

    def _flag(self, rec):
        s = ""
        if rec.get("pinned"):
            s += "★"
        st = self.path_state.get(rec["id"])
        if st == "missing":
            s += "✘"
        elif st == "unknown":
            s += "?"
        return s

    def _row_values(self, rec):
        return (
            self._flag(rec),
            rec.get("customer", ""),
            rec.get("name", ""),
            rec.get("project_no", ""),
            rec.get("contract_no", ""),
            rec.get("last_opened", "")[:16],
            rec.get("note", ""),
        )

    def _row_tags(self, rec):
        st = self.path_state.get(rec["id"])
        if st == "missing":
            return ("missing",)
        if st == "unknown":
            return ("unknown",)
        return ()

    def refresh(self, select_first=False, reset_scroll=False):
        recs = self._visible_records()
        self._visible = recs
        ids = [r["id"] for r in recs]
        cur = self._selected_id()
        self._apply_view(ids)
        if select_first:
            target = ids[0] if ids else None
        else:
            target = cur if cur in ids else (ids[0] if ids else None)
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            if reset_scroll or select_first:
                self.tree.see(target)
        if reset_scroll:
            self.tree.yview_moveto(0)
        self._sync_empty_hint()
        self._update_status()

    def _apply_view(self, ids):
        tree = self.tree
        want = list(ids)
        want_set = set(want)
        for iid in tree.get_children():
            if iid not in want_set:
                tree.delete(iid)
        for idx, iid in enumerate(want):
            rec = self.store.get(iid)
            if rec is None:
                continue
            if not tree.exists(iid):
                tree.insert("", idx, iid=iid, values=self._row_values(rec),
                            tags=self._row_tags(rec))
            else:
                if tree.index(iid) != idx:
                    tree.move(iid, "", idx)
                tree.item(iid, values=self._row_values(rec), tags=self._row_tags(rec))

    def _update_status(self):
        total = len(self.store.records)
        shown = len(self._visible)
        miss = sum(1 for r in self.store.records
                   if self.path_state.get(r["id"]) == "missing")
        txt = "共 %d 条 · 匹配 %d 条" % (total, shown)
        if miss:
            txt += " · %d 条路径当前不可访问" % miss
        if self.store.readonly:
            txt += " · 只读模式（数据版本较新）"
        if self.store.recovered_from:
            txt += " · 已从备份恢复"
        self.lbl_status.config(text=txt)

    # ---- 选择 ----
    def _selected_id(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _selected_record(self):
        rid = self._selected_id()
        return self.store.get(rid) if rid else None

    def _move(self, delta):
        ids = list(self.tree.get_children())
        if not ids:
            return
        cur = self._selected_id()
        idx = ids.index(cur) if cur in ids else (-1 if delta > 0 else 0)
        idx = max(0, min(len(ids) - 1, idx + delta))
        self.tree.selection_set(ids[idx])
        self.tree.focus(ids[idx])
        self.tree.see(ids[idx])

    # ---- 打开 ----
    def open_selected(self):
        rec = self._selected_record()
        if not rec:
            return
        p = norm_path(rec.get("path", ""))
        if not p:
            messagebox.showwarning(APP_TITLE, "该记录没有路径。", parent=self.root)
            return
        if open_target(p):
            self.store.touch(rec["id"])
            self.path_state[rec["id"]] = "ok"
            self.refresh()
        else:
            self.path_state[rec["id"]] = "missing"
            self.refresh()
            messagebox.showerror(APP_TITLE, "打开失败，路径可能已不存在：\n%s" % mask_path(p),
                                 parent=self.root)

    def _on_double_click(self, evt):
        if self.tree.identify_region(evt.x, evt.y) not in ("cell", "tree"):
            return
        if not self.tree.identify_row(evt.y):
            return
        self.open_selected()

    def _on_search_enter(self, _evt):
        if not ENTER_IN_SEARCH_OPENS:
            ids = self.tree.get_children()
            if ids:
                self.tree.focus_set()
            return "break"
        self.open_selected()
        return "break"

    def _on_paste(self, _evt):
        try:
            txt = self.root.clipboard_get()
        except Exception:
            return None
        p = norm_path(txt)
        if p and os.path.isdir(p):
            hit = self.store.by_path(p)
            if hit:
                self.tree.selection_set(hit["id"])
                self.tree.focus(hit["id"])
                self.tree.see(hit["id"])
                if messagebox.askyesno(APP_TITLE, "该目录已有记录，现在打开它吗？",
                                       parent=self.root):
                    self.open_selected()
                return "break"
            if messagebox.askyesno(APP_TITLE, "检测到目录路径：\n%s\n\n新增这条记录？" % p,
                                   parent=self.root):
                self._new_with_prefill(p)
                return "break"
            return None
        return None

    def _on_dropped(self, paths):
        """处理拖入的路径。只读状态栏提示，不弹 messagebox——这条路径要尽量无嵌套弹窗。"""
        paths = [p for p in (paths or []) if p]
        if not paths:
            return
        if len(paths) > 1:
            self.lbl_status.config(text="一次只能加一个，已取第一个：%s" % os.path.basename(paths[0]))
        p = norm_path(paths[0])
        if not path_exists(p):
            self.lbl_status.config(text="拖入的路径不存在：%s" % mask_path(p))
            return
        if self._dialog_busy:
            self.lbl_status.config(text="有编辑窗口没关，先处理它再来")
            return
        hit = self.store.by_path(p)
        if hit:
            self.q.set("")
            self.tree.selection_set(hit["id"])
            self.tree.focus(hit["id"])
            self.tree.see(hit["id"])
            self.root.lift()
            self.open_selected()
            return
        self._new_with_prefill(p)

    def _new_with_prefill(self, path):
        self.root.lift()
        info = infer_from_path(path)
        res = self._open_dialog(prefill=info)
        if res:
            rec = self.store.add(**res)
            self.q.set("")
            self.refresh(select_first=True)
            self.tree.selection_set(rec["id"])
            self.tree.focus(rec["id"])
            self.tree.see(rec["id"])

    # ---- CRUD ----
    def new_record(self):
        res = self._open_dialog()
        if res:
            self.store.add(**res)
            self.q.set("")
            self.refresh(select_first=True)

    def edit_selected(self):
        rec = self._selected_record()
        if not rec:
            return
        res = self._open_dialog(record=dict(rec))
        if res:
            self.store.update(rec["id"], **res)
            rec.pop("_hs", None)
            self.refresh()

    def delete_selected(self):
        rec = self._selected_record()
        if not rec:
            return
        msg = "确定删除这条记录？\n\n客户：%s\n项目：%s\n路径：%s\n\n（数据文件有自动备份，可从「文件 → 从备份恢复」找回）" % (
            rec.get("customer", "") or "-", rec.get("name", "") or "-",
            mask_path(rec.get("path", "")) or "-")
        if messagebox.askyesno(APP_TITLE, msg, parent=self.root, icon="warning"):
            self.store.remove(rec["id"])
            self.path_state.pop(rec["id"], None)
            self.refresh(select_first=True)

    def toggle_pin_selected(self):
        rec = self._selected_record()
        if rec:
            self.store.toggle_pin(rec["id"])
            self.refresh()

    def _on_right_click(self, evt):
        iid = self.tree.identify_row(evt.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        rec = self.store.get(iid)
        if not rec:
            return
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="打开（回车）", command=self.open_selected)
        m.add_command(label="在上级目录中定位",
                      command=lambda: reveal_in_parent(rec["path"]))
        m.add_command(label="打开客户根目录",
                      command=lambda: self.open_customer_root(rec))
        m.add_separator()
        m.add_command(label="复制路径", command=lambda: self.copy_path(rec))
        m.add_command(label="复制项目名称", command=lambda: copy_text(self.root, rec["name"]))
        m.add_separator()
        m.add_command(label="编辑（F2）", command=self.edit_selected)
        m.add_command(label="取消置顶" if rec.get("pinned") else "置顶",
                      command=self.toggle_pin_selected)
        m.add_command(label="删除（Del）", command=self.delete_selected)
        try:
            m.tk_popup(evt.x_root, evt.y_root)
        finally:
            m.grab_release()

    def open_customer_root(self, rec):
        p = norm_path(rec.get("path", ""))
        if os.path.isfile(p):
            p = os.path.dirname(p)
        parent = os.path.dirname(p)
        if parent and os.path.isdir(parent):
            open_target(parent)
        else:
            messagebox.showinfo(APP_TITLE, "找不到上级目录。", parent=self.root)

    def copy_path(self, rec):
        if copy_text(self.root, rec.get("path", "")):
            self.lbl_status.config(text="已复制路径")

    def on_heading_click(self, col):
        if col == "flag":
            self.toggle_pin_selected()
            return
        s = self.store.ui["sort"]
        if s["column"] == col:
            s["desc"] = not s["desc"]
        else:
            s["column"] = col
            s["desc"] = False
        self._update_headings()
        self.refresh()

    def _update_headings(self):
        cur = self.store.ui["sort"]
        for key, head, _w, anchor, _s in COLUMNS:
            mark = ""
            if key == cur["column"]:
                mark = "  ▼" if cur["desc"] else "  ▲"
            self.tree.heading(key, text=head + mark, anchor=anchor)

    # ---- 路径检查（后台线程 + 主线程轮询）----
    def start_path_check(self):
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        self._pq = queue.Queue()
        recs = [(r["id"], r["path"]) for r in self.store.records]

        def work(items, q):
            try:
                for rid, path in items:
                    if self.closing:
                        return
                    p = norm_path(path)
                    if path_exists(p):
                        st = "ok"
                    elif len(p) > 240:
                        st = "unknown"
                    else:
                        st = "missing"
                    q.put((rid, st))
            finally:
                q.put(("__done__", None))     # 哨兵：告诉主线程收工

        threading.Thread(target=work, args=(recs, self._pq), daemon=True).start()
        self._poll_job = self.root.after(50, self._poll_paths)

    def _poll_paths(self):
        if self.closing:
            return
        changed = finished = False
        while True:
            try:
                rid, st = self._pq.get_nowait()
            except queue.Empty:
                break
            if rid == "__done__":
                finished = True
                continue
            if self.path_state.get(rid) != st:
                self.path_state[rid] = st
                changed = True
        if changed:
            self.refresh()
        if not finished:
            self._poll_job = self.root.after(120, self._poll_paths)
        else:
            self._poll_job = None
            if changed:
                self.refresh()

    # ---- 菜单动作 ----
    def do_backup(self):
        self.store.save()
        self.store._snapshot(force=True)
        self.lbl_status.config(text="已备份到 %s" % mask_path(self.store.backup_dir))

    def do_restore(self):
        files = self.store._backup_files()
        if not files:
            messagebox.showinfo(APP_TITLE, "还没有可用的备份。", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("从备份恢复")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="选择一个备份（时间新→旧）：").pack(anchor="w")
        lb = tk.Listbox(frame, height=12, width=64, font=(FONT, 9))
        for f in files:
            try:
                ts = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = "?"
            lb.insert("end", "%s   %s" % (ts, os.path.basename(f)))
        lb.pack(fill="both", expand=True, pady=8)
        lb.selection_set(0)

        def do_it():
            sel = lb.curselection()
            if not sel:
                return
            src = files[sel[0]]
            if not messagebox.askyesno(
                    APP_TITLE, "用这份备份覆盖当前数据？\n%s\n\n当前数据会先自动备份一份。" % mask_path(src),
                    parent=win):
                return
            try:
                with open(src, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except Exception as e:
                messagebox.showerror(APP_TITLE, "读取备份失败：%s" % e, parent=win)
                return
            self.store._snapshot(force=True)
            self.store._apply(data)
            self.store.save()
            self.path_state.clear()
            win.destroy()
            self.refresh(select_first=True)
            self.start_path_check()
            self.lbl_status.config(text="已从备份恢复")

        bar = ttk.Frame(frame)
        bar.pack(fill="x")
        ttk.Button(bar, text="恢复", command=do_it).pack(side="right")
        ttk.Button(bar, text="取消", command=win.destroy).pack(side="right", padx=8)
        win.wait_visibility()
        win.grab_set()

    def open_data_dir(self):
        open_target(self.store.dir)

    def show_help(self):
        txt = (
            "用法\n\n"
            "1. 打开程序，光标已在搜索框。\n"
            "2. 输入合同编号 / 项目编号 / 项目名称 / 客户，任一均可是片段。\n"
            "   多个词用空格分隔 = 同时满足（例如：客户 维保）。\n"
            "3. 回车 = 直接打开当前高亮的第一条。\n\n"
            "录入\n"
            "  · 把文件夹从资源管理器拖进窗口 → 自动解析客户名/项目编号/项目名称\n"
            "  · 或在资源管理器里 Shift+右键「复制为路径」，到这里 Ctrl+V，会自动问是否新增\n"
            "  · Ctrl+N 手工新增；选完目录后会自动预填，可再改\n\n"
            "快捷键\n"
            "  Ctrl+F 回到搜索框     Ctrl+N 新增      F2 编辑\n"
            "  Del 删除              Esc 清空搜索     回车 打开选中/第一条\n"
            "  Ctrl+Q 退出程序       ↑ ↓ 在搜索框与列表之间移动选择\n\n"
            "列头点击可排序（★ 列点击 = 切换置顶，置顶项永远排最前）\n"
            "  ✘ = 路径当前不可访问（红色行）   ? = 路径过长未能验证\n\n"
            "点 × 时的行为\n"
            "  首次会问一次：最小化到任务栏 / 直接退出，可勾选记住。\n"
            "  随时可在「文件 → 点 × 时」修改。\n\n"
            "数据位置\n"
            "  %s\n"
            "  每次保存后自动快照，最多保留 %d 份在 backups 子目录。\n"
            "  若 exe 同目录放一个 pmindex.json，则优先用它（便携模式）。"
        ) % (mask_path(self.store.path), BACKUP_KEEP)
        self._text_window("快捷键与用法", txt)

    def show_about(self):
        self._text_window("关于", "%s\n\n版本 %s\n单文件本地工具，数据全部留在本机、不联网。\n\n数据文件：\n%s" % (
            APP_TITLE, APP_VERSION, mask_path(self.store.path)))

    def _text_window(self, title, content):
        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)

        # 高度按内容自适应（够长就不出滚动条），太长的内容配滚动条
        lines = content.count("\n") + 2
        h = max(12, min(28, lines))
        wrap = ttk.Frame(frame)
        wrap.pack(fill="both", expand=True)
        t = tk.Text(wrap, wrap="word", width=70, height=h, font=(FONT, 9),
                    relief="flat", highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        t.insert("1.0", content)
        t.config(state="disabled")
        ttk.Button(frame, text="关闭", command=win.destroy).pack(pady=(8, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        win.wait_visibility()
        win.grab_set()

    # ---- 窗口状态 ----
    def _apply_saved_ui(self):
        ui = self.store.ui
        geo = ui.get("geometry") or ""
        if geo and self._geometry_on_screen(geo):
            try:
                self.root.geometry(geo)
            except Exception:
                self._default_fit()
        else:
            self._default_fit()
        if ui.get("zoomed"):
            try:
                self.root.state("zoomed")
            except Exception:
                pass
        saved = ui.get("columns") or {}
        for key, _h, _w, _a, _s in COLUMNS:
            try:
                if key in saved and int(saved[key]) >= 30:
                    self.tree.column(key, width=int(saved[key]))
            except Exception:
                pass
        self._update_headings()

    def _physical_client(self):
        """窗口客户区的真实物理像素尺寸（Tk 的 winfo_width 会是折算后的逻辑值）。"""
        try:
            hwnd = _toplevel_hwnd(self.root.winfo_id())
            rc = wintypes.RECT()
            ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
            return int(rc.right), int(rc.bottom)
        except Exception:
            return 0, 0

    def _default_fit(self):
        """首次运行：按列宽总和定窗口宽度，并自校正一次（Tk 会把尺寸按缩放折算）。"""
        try:
            want_w = sum(self._auto_widths().values()) + int(70 * self.scale)
        except Exception:
            want_w = int(1080 * self.scale)
        want_h = int(660 * self.scale)
        try:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080
        want_w, want_h = min(want_w, sw - 60), min(want_h, sh - 100)
        x = max(20, (sw - want_w) // 2)
        self.root.geometry("%dx%d+%d+%d" % (want_w, want_h, x, 60))
        try:
            self.root.update_idletasks()
            got_w, got_h = self._physical_client()
            if got_w and abs(got_w - want_w) > 24:
                r = want_w / float(got_w)
                w2 = min(int(want_w * r), sw - 40)
                h2 = min(int(want_h * r), sh - 80)
                self.root.geometry("%dx%d+%d+%d" % (w2, h2, max(20, (sw - w2) // 2), 60))
                self.root.update_idletasks()
        except Exception:
            logging.exception("窗口尺寸校正失败")

    def _geometry_on_screen(self, geo):
        try:
            m = re.match(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", geo)
            if not m:
                return False
            x, y = int(m.group(3)), int(m.group(4))
            u32 = ctypes.windll.user32
            vx = u32.GetSystemMetrics(76)
            vy = u32.GetSystemMetrics(77)
            vw = u32.GetSystemMetrics(78)
            vh = u32.GetSystemMetrics(79)
            return (vx - 200 <= x <= vx + vw) and (vy - 100 <= y <= vy + vh)
        except Exception:
            return True

    def _save_ui(self):
        try:
            zoomed = self.root.state() == "zoomed"
        except Exception:
            zoomed = False
        self.store.ui["zoomed"] = zoomed
        if not zoomed:
            try:
                self.store.ui["geometry"] = self.root.geometry()
            except Exception:
                pass
        for key, _h, _w, _a, _s in COLUMNS:
            try:
                self.store.ui["columns"][key] = int(self.tree.column(key, "width"))
            except Exception:
                pass

    def focus_search(self):
        self.e_search.focus_set()
        self.e_search.select_range(0, "end")

    def _on_escape(self, _evt=None):
        if self.q.get().strip():
            self.q.set("")
        else:
            self.focus_search()

    # ---- 关闭行为：首次询问 → 记住 → 可在「文件 → 点 × 时」随时改 ----
    def on_close(self):
        if self._dialog_busy and not self.closing:
            # 编辑框中，避免关窗把未保存的输入吞掉
            if not messagebox.askyesno(APP_TITLE, "还有编辑窗口没关，确定退出？",
                                       parent=self.root):
                return
        try:
            iconic = self.root.state() == "iconic"
        except Exception:
            iconic = False
        act = self.store.ui.get("close_action") or ""
        if iconic:
            act = "exit"            # 已经在任务栏里再收到关闭（如任务栏右键关闭）→ 真退出
        elif act not in ("exit", "minimize"):
            act = self._ask_close_action()
            if act is None:
                return              # 用户取消
        if act == "minimize":
            self.minimize_to_taskbar()
        else:
            self.quit_app()

    def _ask_close_action(self):
        """首次点 × 时问一次：退出还是最小化到任务栏。返回 'exit'/'minimize'/None。"""
        win = tk.Toplevel(self.root)
        win.title("关闭窗口时")
        win.transient(self.root)
        win.resizable(False, False)
        result = {"act": None}
        v = tk.StringVar(value="minimize")
        remember = tk.BooleanVar(value=True)

        body = ttk.Frame(win, padding=(18, 14))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="下次点窗口右上角的 × 时，你希望：",
                  font=(FONT, 10)).pack(anchor="w", pady=(0, 10))
        ttk.Radiobutton(body, text="最小化到任务栏（程序留着，双击桌面图标可唤回）",
                        variable=v, value="minimize").pack(anchor="w", pady=3)
        ttk.Radiobutton(body, text="直接退出程序", variable=v,
                        value="exit").pack(anchor="w", pady=3)
        ttk.Checkbutton(body, text="记住我的选择（以后不再问，可在「文件 → 点 × 时」修改）",
                        variable=remember).pack(anchor="w", pady=(12, 0))

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(12, 0))

        def ok():
            result["act"] = v.get()
            if remember.get():
                self.store.ui["close_action"] = result["act"]
                self.v_close_mode.set(result["act"])
                self.store.save()
            win.destroy()

        ttk.Button(bar, text="取消", width=8, command=win.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(bar, text="确定", width=8, command=ok).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: ok())
        win.update_idletasks()
        try:
            pw, ph = self.root.winfo_width(), self.root.winfo_height()
            px, py = self.root.winfo_rootx(), self.root.winfo_rooty()
            win.geometry("+%d+%d" % (px + (pw - win.winfo_width()) // 2,
                                     py + (ph - win.winfo_height()) // 3))
        except Exception:
            pass
        win.wait_visibility()
        win.grab_set()
        win.wait_window()
        return result["act"]

    def minimize_to_taskbar(self):
        try:
            self.root.iconify()
            logging.info("窗口已最小化到任务栏")
        except Exception:
            logging.exception("最小化失败")
            self.quit_app()

    def quit_app(self):
        self.closing = True
        for job in (self._poll_job, self._debounce_job, self._drop_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        try:
            self._save_ui()
            self.store.save()
        except Exception:
            logging.exception("退出保存失败")
        self.root.destroy()


# ============================ §8 main ============================
def _make_hdrop(paths):
    """构造一个真实的 HDROP（DROPFILES 头 + UTF-16 路径列表），仅供 --selftest-drop 用。

    HDROP 本质就是一块全局内存，所以可以在本进程内造出来再 SendMessage 给窗口。
    """
    files = "".join(p + "\0" for p in paths) + "\0"
    data = files.encode("utf-16-le")
    size = 20 + len(data) + 2
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    h = k32.GlobalAlloc(0x0002, size)          # GMEM_MOVEABLE
    p = k32.GlobalLock(h)
    ctypes.memset(p, 0, size)
    ctypes.memmove(p + 0, struct.pack("<i", 20), 4)   # pFiles 偏移
    ctypes.memmove(p + 16, struct.pack("<i", 1), 4)   # fWide = TRUE
    ctypes.memmove(p + 20, data, len(data))
    k32.GlobalUnlock(h)
    return h


def _run_drop_selftest(root, app, target):
    """隐藏自检：模拟从资源管理器拖入一个目录，验证整条链路不会把进程搞崩。

    用法： 项目速查.exe --selftest-drop "D:\\某个项目目录"
    结果写入数据目录的 selftest_result.txt（崩了就不会有这个文件）。
    """
    out = os.path.join(app.store.dir, "selftest_result.txt")
    try:
        if os.path.isfile(out):
            os.remove(out)
    except Exception:
        pass
    state = {"sent": False}

    def send():
        if not _drop_state["installed"]:
            with open(out, "w", encoding="utf-8") as f:
                f.write("FAIL 拖拽未安装")
            root.destroy()
            return
        h = _make_hdrop([target])
        state["sent"] = True
        _u32.SendMessageW(_drop_state["hwnd"], WM_DROPFILES, wintypes.WPARAM(h), 0)

    def finish():
        ok = app._dialog_busy
        msg = ("OK 已走到「新增记录」弹窗（未崩溃）" if ok
               else "FAIL 没有走到弹窗，sent=%s" % state["sent"])
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass
        logging.info("SELFTEST %s", msg)
        for w in list(root.winfo_children()):
            if isinstance(w, tk.Toplevel):
                try:
                    w.destroy()
                except Exception:
                    pass
        app.closing = True
        try:
            root.destroy()
        except Exception:
            pass

    root.after(1200, send)
    root.after(4500, finish)


def main():
    guard_stdio()
    ddir = data_dir()
    log_file = os.path.join(ddir, "app.log")
    setup_logging(log_file)
    install_excepthook(log_file)
    logging.info("启动，数据目录 %s", ddir)

    selftest = "--selftest-drop" in sys.argv
    if not selftest and not single_instance(APP_TITLE):
        try:
            messagebox.showinfo(APP_TITLE, "程序已经在运行了。")
        except Exception:
            pass
        return

    setup_dpi()
    root = tk.Tk()
    try:
        root.tk.call("tk", "font", "system", tkfont.nametofont("TkDefaultFont").actual("family"))
    except Exception:
        pass
    store = DataStore(ddir)
    store.load()
    app = App(root, store)
    if selftest:
        try:
            i = sys.argv.index("--selftest-drop")
            target = (sys.argv[i + 1] if len(sys.argv) > i + 1 else os.getcwd())
        except Exception:
            target = os.getcwd()
        _run_drop_selftest(root, app, target)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("启动失败")
        guard_stdio()
        try:
            messagebox.showerror(APP_TITLE, "启动失败，详见 app.log\n\n" +
                                 "".join(__import__("traceback").format_exc()))
        except Exception:
            pass
        raise
