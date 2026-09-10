"""支持 `python -m bigqmt.mcp` 直接启动。"""

from bigqmt.mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
