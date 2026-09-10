"""把 Python 源码转换成纯 ASCII，规避编辑器/加载器的编码问题。

背景：`bigqmt/service/http.py` 需要粘贴进 QMT 策略编辑器运行。若编辑器/剪贴板
把中文源码转成 GBK，而 QMT 按 UTF-8 解码，就会报：

    SyntaxError: (unicode error) 'utf-8' codec can't decode byte 0xc4 in position 0

纯 ASCII 源码与编码声明无关，可彻底规避该问题：
字符串/文档字符串里的非 ASCII 字符写成 ``\\uXXXX`` 转义（运行时等价），
注释里的非 ASCII 字符同样转义（只影响可读性）。

用法：
    python scripts/ascii_safe_source.py bigqmt/service/http.py
    python scripts/ascii_safe_source.py bigqmt/service/http.py -o dist/http_ascii.py
    python scripts/ascii_safe_source.py --check bigqmt/service/http.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def to_ascii(source: str) -> str:
    """把非 ASCII 字符替换为 \\uXXXX 转义。"""
    if source.isascii():
        return source
    return "".join(
        ch if ord(ch) < 128 else "\\u%04x" % ord(ch) for ch in source
    )


def convert_file(src: Path, dst: Path) -> int:
    text = src.read_text(encoding="utf-8")
    converted = to_ascii(text)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # 用 ASCII 编码写出：任何编码转换都不会再破坏内容
    dst.write_text(converted, encoding="ascii", newline="\n")
    return len(converted)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把源码转成纯 ASCII（用于规避粘贴/保存时的编码问题）"
    )
    parser.add_argument("source", help="输入 .py 文件")
    parser.add_argument("-o", "--output", help="输出路径（默认 ./dist/<名字>_ascii.py）")
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

    dst = Path(args.output) if args.output else Path("dist") / f"{src.stem}_ascii.py"
    size = convert_file(src, dst)
    print(f"[OK] 已生成纯 ASCII 版本：{dst.resolve()}（{size} 字符）")
    print("     可直接粘贴进 QMT 策略编辑器，保存后运行；不会再触发编码错误。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
