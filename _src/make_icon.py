# -*- coding: utf-8 -*-
"""生成 app.ico：墨色圆角方块 + 米色文件夹折角，纯标准库（手写 PNG 编码）。

用法： python make_icon.py
"""
import os
import struct
import zlib

INK = (35, 43, 51, 255)        # 墨色底 #232B33
CREAM = (237, 230, 214, 255)   # 米色文件夹 #EDE6D6
RED = (176, 58, 46, 255)       # 印章红点缀 #B03A2E

SIZES = (16, 24, 32, 48, 64, 128, 256)


def blend(dst, src):
    """src 覆盖 dst（src 可带 alpha）。"""
    sa = src[3] / 255.0
    if sa >= 1.0:
        return src
    da = dst[3] / 255.0
    oa = sa + da * (1 - sa)
    if oa <= 0:
        return (0, 0, 0, 0)
    return (
        int((src[0] * sa + dst[0] * da * (1 - sa)) / oa),
        int((src[1] * sa + dst[1] * da * (1 - sa)) / oa),
        int((src[2] * sa + dst[2] * da * (1 - sa)) / oa),
        int(oa * 255),
    )


def rounded_mask(x, y, n, pad, radius):
    """圆角矩形内部判定（带 1px 羽化，避免小尺寸锯齿）。"""
    x0, y0, x1, y1 = pad, pad, n - pad, n - pad
    if x < x0 - 1 or y < y0 - 1 or x > x1 + 1 or y > y1 + 1:
        return 0.0
    # 到圆角区四角的距离
    cx = min(max(x, x0 + radius), x1 - radius)
    cy = min(max(y, y0 + radius), y1 - radius)
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    if d <= radius - 1:
        return 1.0
    if d >= radius + 0.5:
        return 0.0
    return max(0.0, min(1.0, radius + 0.5 - d))


def folder_mask(x, y, n):
    """文件夹形状：躯干 + 左上标签。返回 0/1。"""
    bx0, bx1 = n * 0.16, n * 0.84
    by0, by1 = n * 0.34, n * 0.76
    if bx0 <= x <= bx1 and by0 <= y <= by1:
        return 1.0
    # 标签（左上角凸起）
    tx0, tx1 = n * 0.16, n * 0.50
    ty0, ty1 = n * 0.24, n * 0.34
    if tx0 <= x <= tx1 and ty0 <= y <= ty1:
        return 1.0
    return 0.0


def render(n):
    """返回 RGBA 像素字节串（逐行）。"""
    px = []
    pad = 0.0 if n <= 24 else n * 0.03
    radius = max(2.0, n * 0.22)
    for y in range(n):
        row = []
        for x in range(n):
            a = rounded_mask(x + 0.5, y + 0.5, n, pad, radius)
            if a <= 0:
                row.append((0, 0, 0, 0))
                continue
            c = (INK[0], INK[1], INK[2], int(INK[3] * a))
            fx = folder_mask(x + 0.5, y + 0.5, n)
            if fx > 0:
                c = blend(c, (CREAM[0], CREAM[1], CREAM[2], int(CREAM[3] * fx)))
            # 印章红：文件夹右上角一个小方块
            if n * 0.66 <= x <= n * 0.84 and n * 0.44 <= y <= n * 0.56:
                c = RED
            row.append(c)
        px.append(row)
    return px


def encode_png(rows, n):
    raw = bytearray()
    for y in range(n):
        raw.append(0)  # filter type 0
        for x in range(n):
            r, g, b, a = rows[y][x]
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    blobs = []
    for n in SIZES:
        blobs.append((n, encode_png(render(n), n)))

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for n, png in blobs:
        dim = 0 if n >= 256 else n
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png

    with open(out, "wb") as f:
        f.write(header + entries + data)
    print("ok", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
