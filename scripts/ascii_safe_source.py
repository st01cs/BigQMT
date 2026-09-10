"""转换 Python 源码编码，规避编辑器/加载器不一致导致的 SyntaxError。

背景：`bigqmt/service/http.py` 需要粘贴进 QMT 策略编辑器运行。若编辑器/剪贴板
把中文源码转成 GBK，而 QMT 按 UTF-8 解码，就会报：

    SyntaxError: (unicode error) 'utf-8' codec can't decode byte 0xc4 in position 0

三种输出模式：

- ``ascii``（默认，最稳）：非 ASCII 字符写成 ``\\uXXXX`` 转义，产物纯 ASCII。
  ASCII 字节在 GBK/UTF-8/ANSI 下完全相同，任何转码都不会破坏内容。
- ``gbk``：保留中文原样，按 GBK 写盘并把首行 coding 声明改成 gbk（声明与字节一致）。
  适合 QMT 编辑器按 ANSI/GBK 保存的场景；无法用 GBK 表示的字符会直接报错。
- ``utf-8``：保留中文原样，确保按 UTF-8（无 BOM）写盘。

用法：
    python scripts/ascii_safe_source.py bigqmt/service/http.py
    python scripts/ascii_safe_source.py bigqmt/service/http.py --encoding gbk
    python scripts/ascii_safe_source.py bigqmt/service/http.py -o dist/http_gbk.py
    python scripts/ascii_safe_source.py --check bigqmt/service/http.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CODING_RE = re.compile(r"^#.*coding[:=]\s*([-\w.]+)")


def to_ascii(source: str) -> str:
    """把非 ASCII 字符替换为 \\uXXXX 转义。"""
    if source.isascii():
        return source
    return "".join(
        ch if ord(ch) < 128 else "\\u%04x" % ord(ch) for ch in source
    )


def with_coding_declaration(source: str, encoding: str) -> str:
    """把首行 coding 声明改成实际写盘编码（声明与字节必须一致）。"""
    lines = source.splitlines(keepends=True)
    if not lines or not CODING_RE.match(lines[0]):
        return source
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    lines[0] = "# -*- coding: %s -*-%s" % (encoding, newline)
    return "".join(lines)


def convert_text(source: str, encoding: str) -> str:
    """按目标编码生成最终写出的文本内容。"""
    if encoding == "ascii":
        return with_coding_declaration(to_ascii(source), "ascii")
    return with_coding_declaration(source, encoding)


def convert_file(src: Path, dst: Path, encoding: str = "ascii") -> int:
    """转换文件；返回写出的字节数。编码不可表示字符时抛 UnicodeEncodeError。"""
    text = src.read_text(encoding="utf-8")
    converted = convert_text(text, encoding)
    try:
        data = converted.encode(encoding)
    except UnicodeEncodeError as exc:
        raise UnicodeEncodeError(
            exc.encoding, exc.object, exc.start, exc.end,
            f"目标编码 {encoding} 无法表示该字符 {exc.object[exc.start]!r}，"
            "请改用 --encoding ascii",
        )
    # 回读校验：确保写出的字节按声明解码后与预期一致
    if data.decode(encoding) != converted:
        raise ValueError("编码回读校验失败")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return len(data)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把源码转成纯 ASCII（用于规避粘贴/保存时的编码问题）"
    )
    parser.add_argument("source", help="输入 .py 文件")
    parser.add_argument(
        "-e",
        "--encoding",
        choices=["ascii", "gbk", "utf-8"],
        default="ascii",
        help="输出编码（默认 ascii，最稳妥）",
    )
    parser.add_argument("-o", "--output", help="输出路径（默认 ./dist/<名字>_<编码>.py）")
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查是否已是纯 ASCII，不写文件",
    )
    args = parser.parse_args(argv)

    src = Path(args.source)
    if not src.is_file():
        print(f"输入文件不存在: {src}", file=sys.stderr)
        return 2

    try:
        text = src.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"输入文件不是 UTF-8：{exc}", file=sys.stderr)
        return 2

    if args.check:
        if text.isascii():
            print(f"[OK] {src} 已是纯 ASCII")
            return 0
        count = sum(1 for ch in text if ord(ch) > 127)
        print(f"[INFO] {src} 含 {count} 个非 ASCII 字符，可转换后使用")
        return 1

    suffix = args.encoding.replace("-", "")
    dst = Path(args.output) if args.output else Path("dist") / f"{src.stem}_{suffix}.py"
    try:
        size = convert_file(src, dst, args.encoding)
    except UnicodeEncodeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"[OK] 已生成 {args.encoding} 版本：{dst.resolve()}（{size} 字节）")
    if args.encoding == "ascii":
        print("     纯 ASCII 产物对任何编码转换都免疫，推荐直接粘贴进 QMT。")
    else:
        print("     请确保 QMT 编辑器/保存过程不会再次转码（否则改用 --encoding ascii）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
