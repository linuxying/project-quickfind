# -*- coding: utf-8 -*-
"""解析函数自测：喂各种真实会遇到的脏目录名，逐条对答案。

样本设计原则：保留真实场景的「形状」（全角括号、】后空格、无【】、尾缀合同号、
年月前缀不统一等），但客户名/编号全部使用虚构值。
"""
import importlib.util
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pmindex.pyw")
spec = importlib.util.spec_from_file_location("pmindex", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["pmindex"] = m
spec.loader.exec_module(m)

fail = 0


def chk(desc, got, want):
    global fail
    ok = got == want
    if not ok:
        fail += 1
    print(("  OK  " if ok else "  FAIL") + " " + desc)
    if not ok:
        print("        got  = %r" % (got,))
        print("        want = %r" % (want,))


print("\n=== strip_customer_prefix ===")
chk("202608示例客户甲", m.strip_customer_prefix("202608示例客户甲"), "示例客户甲")
chk("202609_示例客户乙", m.strip_customer_prefix("202609_示例客户乙"), "示例客户乙")
chk("202610-某监测项目-POC",
    m.strip_customer_prefix("202610-某监测项目-POC"), "某监测项目-POC")
chk("2026示例客户丙监测项目",
    m.strip_customer_prefix("2026示例客户丙监测项目"), "示例客户丙监测项目")
chk("无前缀", m.strip_customer_prefix("示例客户丁"), "示例客户丁")
chk("0-合同 不该被剥", m.strip_customer_prefix("0-合同"), "0-合同")

print("\n=== parse_project ===")
chk("标准【】样例",
    m.parse_project("【PRJ-2025-0617-1655】某系统建设与维保服务项目合同"),
    ("PRJ-2025-0617-1655", "某系统建设与维保服务项目合同", ""))
chk("】后带空格 + 半角括号合同号",
    m.parse_project("【PRJ-2026-0116-0160】 某平台软件维保服务采购项目采购合同(ABC202500053)"),
    ("PRJ-2026-0116-0160", "某平台软件维保服务采购项目采购合同", "ABC202500053"))
chk("无【】裸编号",
    m.parse_project("PRJ-2025-0811-2308某安全服务合同"),
    ("PRJ-2025-0811-2308", "某安全服务合同", ""))
chk("合同号带连字符",
    m.parse_project("【PRJ-2024-0314-0590】某加固工具采购项目采购合同---二期(ABC-20230046)"),
    ("PRJ-2024-0314-0590", "某加固工具采购项目采购合同---二期", "ABC-20230046"))
chk("全角括号",
    m.parse_project("【PRJ-2026-0302-0473】采购合同（ABC202500054）"),
    ("PRJ-2026-0302-0473", "采购合同", "ABC202500054"))
chk("尾缀 -HT",
    m.parse_project("【PRJ-2025-0204-0262】某技术服务项目-HT202501080016"),
    ("PRJ-2025-0204-0262", "某技术服务项目", "HT202501080016"))
chk("纯项目名无编号",
    m.parse_project("202609_某威胁感知项目情况"),
    ("", "202609_某威胁感知项目情况", ""))

print("\n=== infer_from_path（模拟两级目录） ===")
samples = [
    r"D:\demo资料库\202601示例客户甲\【PRJ-2025-0617-1655】某系统建设与维保服务项目合同",
    r"D:\demo资料库\202601示例客户乙\【PRJ-2024-1211-3916】某平台软件维保服务采购合同(ABC202400054)",
    r"D:\demo资料库\2026示例客户丙监测项目",
]
for p in samples:
    info = m.infer_from_path(p)
    print("  路径: ...%s" % p[-36:])
    print("    客户=%r 名称=%r 项目编号=%r 合同编号=%r" % (
        info["customer"], info["name"], info["project_no"], info["contract_no"]))
chk("两层目录 → 客户名取上级",
    m.infer_from_path(samples[0])["customer"], "示例客户甲")
chk("合同号从括号里提出",
    m.infer_from_path(samples[1])["contract_no"], "ABC202400054")
chk("单层目录 → 客户名取自身剥前缀",
    m.infer_from_path(samples[2])["customer"], "示例客户丙监测项目")

print("\n=== norm 归一化 ===")
chk("全角括号归一", "(" in m.norm("合同（ABC）"), True)
chk("全角数字归一", "2024" in m.norm("２０２４年"), True)
chk("大小写归一", m.norm("ABC") == m.norm("abc"), True)

print("\n=== 搜索词变体（标点剥离 + 简称展开） ===")
chk("剥两端全角括号",
    "abc202500054" in m.token_variants(m.norm("（ABC202500054）")), True)
chk("简称展开命中全称",
    m.norm("中国农业银行") in m.token_variants(m.norm("农行")), True)

print("\n=== norm_path ===")
chk("去引号与尾部斜杠", m.norm_path('"D:\\demo资料库\\客户甲\\"'),
    r"D:\demo资料库\客户甲")
chk("盘符根保留反斜杠", m.norm_path("D:\\"), "D:\\")

print("\n=== 结果 ===")
print("FAILED: %d" % fail if fail else "ALL PASSED")
sys.exit(1 if fail else 0)
