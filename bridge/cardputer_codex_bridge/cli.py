from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cardputer_codex_bridge.controller.demo import run_demo
from cardputer_codex_bridge.controller.live_demo import run_codex_demo
from cardputer_codex_bridge.device_link import (
    PySerialProvider,
    SerialProvider,
    SyntheticSerialProvider,
    resolve_port,
    run_bringup,
    run_bringup_dry_run,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cardputer-codex-controller")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo", help="合成Cardputerでhost controller MVPを実行")
    subcommands.add_parser("codex-demo", help="認証済みCodex app-serverで安全な実測を実行")
    bringup = subcommands.add_parser(
        "bringup",
        help="Codex非依存のCardputer-Adv M1診断を実行",
    )
    bringup_source = bringup.add_mutually_exclusive_group(required=True)
    bringup_source.add_argument("--synthetic", action="store_true", help="合成診断device")
    bringup_source.add_argument("--port", help="この実行だけに使う明示port")
    bringup_source.add_argument(
        "--port-handle",
        type=Path,
        help="Git管理外の1行local handle file",
    )
    bringup.add_argument(
        "--dry-run",
        action="store_true",
        help="選択だけを検証しserial I/Oを起動しない",
    )
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
    if args.command == "bringup":
        try:
            if args.synthetic:
                selected_port = "synthetic"
            else:
                selected_port = resolve_port(
                    explicit_port=args.port,
                    handle_file=args.port_handle,
                )
            if args.dry_run:
                run_bringup_dry_run(sys.stdout)
                return 0
            bringup_provider: SerialProvider
            bringup_synthetic: SyntheticSerialProvider | None
            if args.synthetic:
                synthetic_bringup_provider = SyntheticSerialProvider()
                bringup_provider = synthetic_bringup_provider
                bringup_synthetic = synthetic_bringup_provider
            else:
                bringup_provider = PySerialProvider()
                bringup_synthetic = None
            run_bringup(
                sys.stdout,
                port=selected_port,
                provider=bringup_provider,
                synthetic_device=bringup_synthetic,
            )
        except Exception as error:
            print(f"bringup FAIL {type(error).__name__}", file=sys.stderr)
            return 1
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
