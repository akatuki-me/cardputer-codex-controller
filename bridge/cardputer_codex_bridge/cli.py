from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from cardputer_codex_bridge.controller.demo import run_demo
from cardputer_codex_bridge.controller.live_demo import run_codex_demo


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardputer-codex-controller")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="合成Cardputerでhost controller MVPを実行")
    subcommands.add_parser("codex-demo", help="認証済みCodex app-serverで安全な実測を実行")
    args = parser.parse_args(argv)
    if args.command == "demo":
        run_demo(sys.stdout)
        return 0
    if args.command == "codex-demo":
        try:
            run_codex_demo(sys.stdout)
        except Exception as error:
            print(f"codex_demo FAIL {type(error).__name__}", file=sys.stderr)
            return 1
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
