# -*- coding: utf-8 -*-
"""数据层自测：增删改、原子写、损坏恢复、备份轮换。"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pmindex.pyw")
spec = importlib.util.spec_from_file_location("pmindex", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["pmindex"] = m
spec.loader.exec_module(m)

fail = 0


def chk(desc, cond, extra=""):
    global fail
    ok = bool(cond)
    if not ok:
        fail += 1
    print(("  OK  " if ok else "  FAIL") + " " + desc + ((" | " + str(extra)) if (not ok and extra) else ""))


d = tempfile.mkdtemp(prefix="pmidx_")
print("临时数据目录:", d)

# ---- 1. 新增与持久化 ----
s = m.DataStore(d)
s.load()
r1 = s.add(customer="示例客户甲", name="某维保服务项目", project_no="PRJ-2026-1014-2724",
           contract_no="HT202610210814", path=r"D:\demo资料库\202608示例客户甲")
r2 = s.add(customer="示例客户乙", name="某平台软件维保", project_no="PRJ-2024-1211-3916",
           path=r"D:\demo资料库\202608示例客户乙")
r3 = s.add(customer="示例客户甲", name="某合规检测服务", path=r"D:\demo资料库\202608示例客户甲\0-合同")
chk("写入 3 条", len(s.records) == 3)
chk("客户记忆去重", s.customers == ["示例客户甲", "示例客户乙"], s.customers)

s2 = m.DataStore(d)
s2.load()
chk("重开数据仍在", len(s2.records) == 3)
chk("字段完整", s2.get(r1["id"])["contract_no"] == "HT202610210814")
chk("客户记忆持久化", s2.customers == ["示例客户甲", "示例客户乙"], s2.customers)

# ---- 2. by_path 去重（大小写不敏感）----
chk("by_path 命中", s2.by_path(r"d:\demo资料库\202608示例客户甲") is not None)

# ---- 3. 内部缓存键不落盘 ----
s2.records[0]["_hs"] = "不该被写进文件"
s2.save()
raw = open(s2.path, encoding="utf-8").read()
chk("下划线缓存键未落盘", "_hs" not in raw and "不该被写进文件" not in raw)
chk("中文未被转义", "示例客户甲" in raw and "\\u" not in raw)

# ---- 4. 损坏恢复 ----
good = open(s2.path, encoding="utf-8").read()
with open(s2.path, "w", encoding="utf-8") as f:
    f.write(good[: len(good) // 2])          # 截断成非法 JSON
s3 = m.DataStore(d)
s3.load()
bad_files = [f for f in os.listdir(d) if ".bad-" in f]
chk("坏文件被改名保留", len(bad_files) == 1, bad_files)
chk("从备份恢复出数据", len(s3.records) == 3, len(s3.records))
chk("记录了恢复来源", s3.recovered_from != "", s3.recovered_from)

# ---- 5. 备份轮换保留 10 份 ----
for i in range(20):
    s3.records[0]["note"] = "n%d" % i
    s3.save()
    s3._snapshot(force=True)
cnt = len(s3._backup_files())
chk("备份数量上限 10", cnt == 10, cnt)
chk("最新备份可读", json.load(open(s3._backup_files()[0], encoding="utf-8"))["records"] is not None)
newest = json.load(open(s3._backup_files()[0], encoding="utf-8"))
chk("最新备份即当前数据", len(newest["records"]) == len(s3.records), len(newest["records"]))

# ---- 6. 删除 ----
n0 = len(s3.records)
s3.remove(r2["id"])
chk("删除生效", len(s3.records) == n0 - 1)
chk("删除后 by_id 同步", s3.get(r2["id"]) is None)
s4 = m.DataStore(d)
s4.load()
chk("删除持久化", len(s4.records) == n0 - 1)

# ---- 7. 置顶与 touch ----
s4.toggle_pin(r1["id"])
chk("置顶已开", s4.get(r1["id"])["pinned"] is True)
s4.touch(r1["id"])
chk("touch 计数", s4.get(r1["id"])["open_count"] == 1)
chk("last_opened 已写", s4.get(r1["id"])["last_opened"] != "")

# ---- 8. 便携模式与 APPDATA 选择 ----
os.environ.pop("PMINDEX_DATA_DIR", None)
chk("非 frozen 时 app_dir 指向源码所在目录",
    m.app_dir() == os.path.dirname(os.path.abspath(SRC)),
    m.app_dir())

shutil.rmtree(d, ignore_errors=True)
print("\n=== 结果 ===")
print("FAILED: %d" % fail if fail else "ALL PASSED")
sys.exit(1 if fail else 0)
