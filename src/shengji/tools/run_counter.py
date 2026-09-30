"""记牌器主程序入口。

用法：
    uv run python -m shengji.tools.run_counter --trump S2
    uv run python -m shengji.tools.run_counter --trump NT5 --templates templates.json

按 Ctrl+Alt+L 切换交互模式，Ctrl+Alt+P 暂停/恢复，Ctrl+Alt+O 强制重读。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..calib.store import load_calibration
from ..engine.trump import parse_trump
from ..events.pending import PendingQueue
from ..layout.model import LayoutModel
from ..recognition.templates import TemplateLibrary
from ..ui.app import CounterApp
from ..window.win32 import enable_per_monitor_dpi_awareness, find_game_window


def _resolve_layout(args, win):
    """解析出实际使用的布局与变体参数。

    优先级：命令行显式给的值 > 标定文件 > 兜底默认。
    **找不到标定时不装作有标定** —— 明确提示退回的是 spec §11.2 的参考布局。
    """
    cal = load_calibration(args.calib)
    if cal is not None:
        players = args.players if args.players is not None else cal.variant[0]
        decks = args.decks if args.decks is not None else cal.variant[1]
        output_idx = args.output_idx if args.output_idx is not None else cal.output_idx
        model = cal.layout
        print(f"已载入标定 {args.calib}：{players} 人 {decks} 副，显示器 {output_idx}")
    else:
        players = args.players if args.players is not None else 4
        decks = args.decks if args.decks is not None else 2
        output_idx = args.output_idx if args.output_idx is not None else 0
        model = LayoutModel.from_reference()
        print(f"未找到可用标定 {args.calib}，退回参考布局（同 calib.example.json）。"
              f"完成现场标定后把结果写到 {args.calib}。", file=sys.stderr)

    if win is not None:
        model = model.for_client(win.x, win.y, win.w, win.h)
    return model, output_idx, players, decks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="升级记牌器")
    ap.add_argument("--trump", default=None,
                    help="主牌规格，如 S2（♠主级牌2）、NT5（无主级牌5）。"
                         "不给则分牌无法判定，只显示张数。")
    ap.add_argument("--templates", default="templates.json",
                    help="模板库路径（不存在则只报告张数，不认牌）")
    ap.add_argument("--calib", default="calib.json",
                    help="标定文件路径。不存在时退回 spec §11.2 的参考布局"
                         "（等价于仓库里的 calib.example.json），并明确提示。")
    ap.add_argument("--output-idx", type=int, default=None,
                    help="显示器序号（不给则用标定里的值，再不然用 0）")
    ap.add_argument("--players", type=int, default=None,
                    help="人数（不给则用标定里的变体，默认 4）")
    ap.add_argument("--decks", type=int, default=None,
                    help="副数（不给则用标定里的变体，默认 2）")
    ap.add_argument("--pending-dir", default="pending",
                    help="待确认项与证据帧的输出目录")
    ap.add_argument("--no-hotkeys", action="store_true",
                    help="不注册全局热键（例如与其它程序冲突时）")
    ap.add_argument("--interactive", action="store_true",
                    help="启动即为交互模式（关闭鼠标穿透）")
    args = ap.parse_args(argv)

    enable_per_monitor_dpi_awareness()

    trump = None
    if args.trump:
        try:
            trump = parse_trump(args.trump)
        except ValueError as exc:
            print(f"主牌规格非法：{exc}", file=sys.stderr)
            return 2

    library = None
    tp = Path(args.templates)
    if tp.exists():
        library = TemplateLibrary.load(tp)
        if library is None or not library.ready:
            print(f"模板库无法使用（{tp}），将只报告张数不认牌", file=sys.stderr)
            library = None
    else:
        print(f"未找到模板库 {tp}，将只报告张数不认牌。"
              f"建立模板库见 README 的 label_templates 流程。", file=sys.stderr)

    win = find_game_window()
    if win is None:
        print("未找到游戏窗口，仍会启动悬浮窗，但布局可能不对。", file=sys.stderr)
        print("可用以下命令列出窗口标题，改 src/shengji/window/win32.py 的"
              " CANDIDATE_TITLE_KEYWORDS：", file=sys.stderr)
        print('  uv run python -c "from shengji.window.win32 import '
              "find_windows_by_title as f; [print(r.title, r.size) for r in f('')]\"",
              file=sys.stderr)

    model, output_idx, players, decks = _resolve_layout(args, win)

    app = CounterApp(
        model,
        library=library,
        pending=PendingQueue(args.pending_dir),
        output_idx=output_idx,
        trump=trump,
        players=players,
        decks=decks,
        use_hotkeys=not args.no_hotkeys,
    )
    if win is not None:
        print(f"已定位游戏窗口：{win.title} 客户区={win.origin} {win.size}")
    if trump is not None:
        print(f"主牌：{trump.describe()}")
    print(f"采集后端：{app.backend_mode.value}  轮询间隔：{app._interval_ms}ms")
    print("热键：Ctrl+Alt+L 交互 · Ctrl+Alt+O 强制重读 · Ctrl+Alt+P 暂停")

    app.start()
    from PySide6.QtWidgets import QApplication

    inst = QApplication.instance()
    assert inst is not None, "ensure_app 已保证 QApplication 存在"
    return int(inst.exec())


if __name__ == "__main__":
    raise SystemExit(main())
