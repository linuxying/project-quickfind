# -*- coding: utf-8 -*-
"""GUI 逻辑自测：过滤、排序、置顶、路径红标、打开动作、差异刷新、关闭行为。

数据写在临时目录里，不依赖已有文件。
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pmindex.pyw")

DDIR = tempfile.mkdtemp(prefix="pmui_")
os.environ["PMINDEX_DATA_DIR"] = DDIR

spec = importlib.util.spec_from_file_location("pmindex", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["pmindex"] = m
spec.loader.exec_module(m)

# 拦掉真实打开动作
opened = []
m.open_target = lambda p: (opened.append(p), True)[1]

import tkinter as tk

fail = 0


def chk(desc, cond, extra=""):
    global fail
    ok = bool(cond)
    if not ok:
        fail += 1
    print(("  OK  " if ok else "  FAIL") + " " + desc
          + ((" | " + str(extra)) if (not ok and extra) else ""))


def pump(sec=0.45):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def alive():
    try:
        return bool(root.winfo_exists())
    except Exception:
        return False


# ---- 造两个真实存在的临时目录，模拟「客户目录 / 项目目录」----
ROOT = os.path.join(DDIR, "demo资料库")
P1 = os.path.join(ROOT, "202601示例客户甲")
P2 = os.path.join(ROOT, "202601示例客户乙")
PROJ = os.path.join(P2, "【PRJ-2024-1211-3916】某平台软件维保服务采购合同(ABC202400054)")
for d in (P1, P2, PROJ):
    os.makedirs(d, exist_ok=True)

store = m.DataStore(DDIR)
store.load()
r1 = store.add(customer="示例客户甲", name="某系统建设与维保服务项目",
               project_no="PRJ-2025-0617-1655", contract_no="HT202501080016", path=P1)
r2 = store.add(customer="示例客户乙", name="某平台软件维保服务",
               project_no="PRJ-2024-1211-3916", contract_no="ABC202400054", path=P2)
r3 = store.add(customer="示例客户丙", name="某监测平台问题修复",
               project_no="PRJ-2023-1018-2118", path=P1)
r4 = store.add(customer="示例客户丁", name="某感知服务升级报文",
               project_no="PRJ-2024-0415-0924", path=os.path.join(DDIR, "不存在的目录"))
store.save()

m.setup_dpi()
root = tk.Tk()
app = m.App(root, store)
pump(0.6)

print("=== 基础渲染 ===")
chk("列表 4 行", len(app.tree.get_children()) == 4, len(app.tree.get_children()))
chk("状态栏含总数", "共 4 条" in app.lbl_status.cget("text"), app.lbl_status.cget("text"))

print("\n=== 过滤（空格分词 AND）===")
app.q.set("示例客户")
pump()
chk("'示例客户' 命中 4 条", len(app.tree.get_children()) == 4, len(app.tree.get_children()))
app.q.set("示例客户乙 维保")
pump()
rows = app.tree.get_children()
chk("'示例客户乙 维保' 命中 1 条", len(rows) == 1, len(rows))
if rows:
    chk("命中的是乙那条", app.tree.item(rows[0], "values")[1] == "示例客户乙",
        app.tree.item(rows[0], "values"))

app.q.set("ABC202400054")
pump()
chk("合同编号半角命中", len(app.tree.get_children()) == 1)
app.q.set("（ＡＢＣ２０２４０００５４）")
pump()
chk("全角合同编号也能命中", len(app.tree.get_children()) == 1,
    [app.tree.item(i, "values")[1] for i in app.tree.get_children()])
app.q.set("prj-2023")
pump()
chk("小写项目编号命中", len(app.tree.get_children()) == 1)
app.q.set("")
pump()
chk("清空后恢复 4 条", len(app.tree.get_children()) == 4)

print("\n=== 差异刷新（不重建行、不丢选中）===")
first_id = app.tree.get_children()[0]
app.tree.selection_set(first_id)
app.q.set("示例客户乙")
pump()
app.q.set("")
pump()
chk("刷新后原 item 仍存在（未整表重建）", app.tree.exists(first_id))
chk("原 item id 仍是记录 id", store.get(first_id) is not None)

print("\n=== 置顶 ===")
app.tree.selection_set(r3["id"])
app.toggle_pin_selected()
pump()
chk("置顶后排第一", app.tree.get_children()[0] == r3["id"], app.tree.get_children()[:1])
chk("★ 列显示", app.tree.item(r3["id"], "values")[0].startswith("★"),
    app.tree.item(r3["id"], "values")[0])

print("\n=== 点列头排序 ===")
app.on_heading_click("customer")
pump()
chk("排序后置顶项仍在最前", app.tree.get_children()[0] == r3["id"])
app.on_heading_click("customer")
pump()
chk("再点一次变降序", app.store.ui["sort"]["desc"] is True)
chk("表头带箭头", "▼" in app.tree.heading("customer")["text"],
    app.tree.heading("customer")["text"])
app.on_heading_click("customer")
app.on_heading_click("customer")
pump()

print("\n=== 路径检查红标 ===")
pump(1.0)
chk("不存在路径被标记", app.path_state.get(r4["id"]) == "missing", app.path_state.get(r4["id"]))
chk("存在路径标记 ok", app.path_state.get(r1["id"]) == "ok", app.path_state.get(r1["id"]))
chk("缺失行的 tag", "missing" in app.tree.item(r4["id"], "tags"),
    app.tree.item(r4["id"], "tags"))
chk("状态栏提示不可访问", "不可访问" in app.lbl_status.cget("text"), app.lbl_status.cget("text"))

print("\n=== 打开动作 ===")
app.tree.selection_set(r1["id"])
app.open_selected()
pump(0.2)
chk("触发打开", opened and opened[-1] == P1, opened[-1:])
chk("记录了最近打开", store.get(r1["id"])["last_opened"] != "")

print("\n=== 搜索框回车 ===")
app.q.set("示例客户丁")
pump()
res = app._on_search_enter(None)
chk("回车返回 break（阻止默认行为）", res == "break", res)
app.q.set("")
pump()

print("\n=== 对话框自动预填 ===")
dlg = m.RecordDialog(root, store.customers, prefill={})
pump(0.2)
dlg.v_path.set(PROJ)
dlg.v_cust.set("")
dlg.v_pno.set("")
dlg.v_cno.set("")
dlg._maybe_fill_impl(True)
chk("预填客户=示例客户乙", dlg.v_cust.get() == "示例客户乙", dlg.v_cust.get())
chk("预填项目编号", dlg.v_pno.get() == "PRJ-2024-1211-3916", dlg.v_pno.get())
chk("预填合同编号", dlg.v_cno.get() == "ABC202400054", dlg.v_cno.get())
chk("预填名称", "某平台软件维保" in dlg.v_name.get(), dlg.v_name.get())
dlg.v_name.set("我已手填的名称")
dlg._maybe_fill_impl(True)
chk("不覆盖已填名称", dlg.v_name.get() == "我已手填的名称", dlg.v_name.get())
dlg.destroy()

print("\n=== 帮助/关于 的路径显示 ===")


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _top(title):
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel) and w.winfo_exists() and w.title() == title:
            return w
    return None


def _text_in(top):
    for w in _walk(top):
        if isinstance(w, tk.Text):
            return w.get("1.0", "end-1c")
    return ""


user = (os.environ.get("USERNAME") or "").strip()
app.show_help()
pump(0.2)
t1 = _top("快捷键与用法")
help_txt = _text_in(t1) if t1 else ""
chk("帮助窗口打开了", bool(t1))
chk("帮助里的路径已占位化",
    bool(user) and user.lower() not in help_txt.lower(),
    [ln for ln in help_txt.splitlines() if user.lower() in ln.lower()][:2])
chk("帮助用占位符显示数据路径",
    ("%APPDATA%" in help_txt) or ("%LOCALAPPDATA%" in help_txt) or ("%USERPROFILE%" in help_txt),
    help_txt[-160:])
if t1:
    t1.destroy()
pump(0.2)

app.show_about()
pump(0.2)
t2 = _top("关于")
about_txt = _text_in(t2) if t2 else ""
chk("关于窗口打开了", bool(t2))
chk("关于里的路径已占位化", bool(user) and user.lower() not in about_txt.lower(), about_txt)
chk("关于里有版本号", "版本" in about_txt, about_txt)
if t2:
    t2.destroy()
pump(0.2)

app.do_backup()
pump(0.2)
chk("备份提示里的路径已占位化",
    bool(user) and user.lower() not in app.lbl_status.cget("text").lower(),
    app.lbl_status.cget("text"))

print("\n=== 关闭行为（点 × 时三选一）===")
# 1) 首次：应弹出询问框；模拟用户按「取消」
ask_seen = []


def auto_cancel_ask():
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel) and w.title() == "关闭窗口时":
            ask_seen.append(True)
            w.destroy()


root.after(300, auto_cancel_ask)
app.on_close()
chk("首次点×弹出询问框", ask_seen == [True], ask_seen)
chk("取消后窗口仍在", alive())

# 2) 选择「最小化到任务栏」
app.store.ui["close_action"] = ""
app._ask_close_action = lambda: "minimize"
app.on_close()
pump(0.3)
chk("最小化到任务栏", root.state() == "iconic", root.state())
root.deiconify()
pump(0.3)

# 3) 记住「直接退出」后，再点 × 直接退出
app.store.ui["close_action"] = "exit"
app.on_close()
pump(0.2)
chk("记住退出后直接关闭窗口", not alive())

s2 = m.DataStore(DDIR)
s2.load()
chk("退出后数据完整", len(s2.records) == 4, len(s2.records))
chk("置顶已持久化", s2.get(r3["id"])["pinned"] is True)
chk("关闭方式已持久化", s2.ui.get("close_action") == "exit", s2.ui.get("close_action"))
chk("窗口位置已持久化", bool(s2.ui.get("geometry")) or s2.ui.get("zoomed"),
    s2.ui.get("geometry"))

shutil.rmtree(DDIR, ignore_errors=True)
print("\n=== 结果 ===")
print("FAILED: %d" % fail if fail else "ALL PASSED")
sys.exit(1 if fail else 0)
