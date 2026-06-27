from __future__ import annotations

import sys
from importlib.util import find_spec

if find_spec("textual") is None:
    sys.stderr.write(
        "\n[demo] 缺少可选依赖 `textual`，TUI 演示需要它来渲染分屏界面。\n"
        "请先安装：\n\n"
        "    uv sync --group demo\n\n"
        "然后重新运行：\n\n"
        "    uv run python examples/minimal_loop/main.py\n\n"
    )
    raise SystemExit(1)

from tui_app import B2DemoApp

if __name__ == "__main__":
    app = B2DemoApp()
    app.run()
