# -*- coding: utf-8 -*-
"""造一份真实感数据，渲染窗口并截图，用于人工核对版面。"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pmindex.pyw")
DDIR = tempfile.mkdtemp(prefix="pmshot_")
os.environ["PMINDEX_DATA_DIR"] = DDIR

spec = importlib.util.spec_from_file_location("pmindex", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["pmindex"] = m
spec.loader.exec_module(m)

import shot  # noqa: E402
import tkinter as tk  # noqa: E402

DATA_ROOT = os.path.join(DDIR, "资料库")   # 演示用假目录，不指向任何真实路径
store = m.DataStore(DDIR)
store.load()

rows = [
    ("示例客户甲", "某系统建设与维保服务项目合同",
     "PRJ-2025-0617-1655", "HT202501080016", "202601示例客户甲", True, "含验收材料"),
    ("示例客户乙", "某平台软件维保服务采购合同",
     "PRJ-2024-1211-3916", "ABC202400054", "202601示例客户乙", False, ""),
    ("示例客户乙", "某加固工具采购项目采购合同---二期",
     "PRJ-2024-0314-0590", "ABC-20230046", "202601示例客户乙", False, "附件在子目录"),
    ("示例客户丙", "2024-2025年安全服务合同",
     "PRJ-2025-0811-2308", "", "202602_示例客户丙", False, ""),
    ("示例客户丁", "监测平台问题修复",
     "PRJ-2023-1018-2118", "", "2026示例客户丁项目", False, "工单 #100001"),
    ("示例客户戊", "移动端安全监控服务项目系统升级",
     "PRJ-2024-0415-0924", "HT202409010011", "202601示例客户戊", False, ""),
    ("示例客户己", "终端安全监测系统建设项目",
     "PRJ-2023-0613-1161", "HT202312310054", "202601示例客户己", False, ""),
    ("示例客户庚", "年度威胁感知服务采购合同",
     "PRJ-2026-0426-1009", "", "202601示例客户庚", False, ""),
    ("示例客户辛", "系统安全服务项目启动会材料",
     "PRJ-2026-0520-1130", "", "202601示例客户辛", False, "已归档"),
]
for cust, name, pno, cno, folder, pin, note in rows:
    p = os.path.join(DATA_ROOT, folder)
    os.makedirs(p, exist_ok=True)      # 真建目录，否则截图里每行都是红色 ✘
    store.add(customer=cust, name=name, project_no=pno, contract_no=cno,
              path=p, note=note, pinned=pin)
# 一条故意失效，用来看红标
store.add(customer="示例客户壬", name="某平台POC（路径已失效）",
          project_no="PRJ-2026-0701-3399", path=r"R:\不存在的网络盘\POC项目")
store.save()

m.setup_dpi()
root = tk.Tk()
app = m.App(root, store)
end = time.time() + 2.2
while time.time() < end:
    root.update()
    time.sleep(0.02)

out1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_main.png")
print("main:", shot.capture_widget(root, out1))

# 过滤态
app.q.set("示例客户乙")
end = time.time() + 0.6
while time.time() < end:
    root.update()
    time.sleep(0.02)
out2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_filter.png")
print("filter:", shot.capture_widget(root, out2))
app.q.set("")
end = time.time() + 0.4
while time.time() < end:
    root.update()
    time.sleep(0.02)

# 新增对话框
dlg = m.RecordDialog(root, store.customers, prefill=m.infer_from_path(
    os.path.join(DATA_ROOT, "202601示例客户甲",
                 "【PRJ-2026-1022-2829】某终端安全监测系统维保服务项目合同-HT202610210814")))
end = time.time() + 1.2
while time.time() < end:
    root.update()
    time.sleep(0.02)
out3 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_dialog.png")
print("dialog:", shot.capture_widget(dlg, out3))
dlg.destroy()

app.on_close()
shutil.rmtree(DDIR, ignore_errors=True)
print("done")
