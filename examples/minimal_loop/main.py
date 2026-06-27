from __future__ import annotations

import argparse
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="B2 Agent 记忆管理器 · 分屏交互式 TUI 演示",
    )
    parser.add_argument(
        "--step-delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="每个步骤之间的延迟秒数（默认 1.0；0 表示无延迟，方便调试）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    app = B2DemoApp(step_delay=args.step_delay)
    app.run()
