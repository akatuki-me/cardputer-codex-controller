from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cardputer_codex_bridge.controller.approval_fixture import (
    run_approval_fixture,
    run_approval_fixture_dry_run,
)
from cardputer_codex_bridge.controller.demo import run_demo
from cardputer_codex_bridge.controller.e2e import (
    run_e2e_dry_run,
    run_serial_e2e,
)
from cardputer_codex_bridge.controller.instance_guard import ControllerInstanceGuard
from cardputer_codex_bridge.controller.live_demo import run_codex_demo
from cardputer_codex_bridge.controller.runtime import (
    run_controller,
    run_controller_dry_run,
)
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
    e2e = subcommands.add_parser(
        "e2e",
        help="実Codexと合成または明示選択したserial transportのE2Eを実行",
    )
    source = e2e.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true", help="実portを使わない合成CDC")
    source.add_argument("--port", help="この実行だけに使う明示port")
    source.add_argument(
        "--port-handle",
        type=Path,
        help="Git管理外の1行local handle file",
    )
    e2e.add_argument(
        "--dry-run",
        action="store_true",
        help="選択だけを検証しserial I/OとCodexを起動しない",
    )
    control = subcommands.add_parser(
        "control",
        help="Cardputerとhost consoleから単一Codex threadを操作",
    )
    control.add_argument("--cwd", type=Path, required=True, help="controller-owned threadのcwd")
    control.add_argument("--label", default="slot-1", help="Cardputerに表示するslot名")
    control_source = control.add_mutually_exclusive_group(required=True)
    control_source.add_argument("--synthetic", action="store_true", help="合成CDC")
    control_source.add_argument("--port", help="この実行だけに使う明示port")
    control_source.add_argument(
        "--port-handle",
        type=Path,
        help="Git管理外の1行local handle file",
    )
    control.add_argument(
        "--dry-run",
        action="store_true",
        help="設定だけを検証しserial I/OとCodexを起動しない",
    )
    approval_fixture = subcommands.add_parser(
        "approval-fixture",
        help="Codex非依存の固定fixtureでM3 approval安全境界を受入",
    )
    approval_fixture_source = approval_fixture.add_mutually_exclusive_group(required=True)
    approval_fixture_source.add_argument(
        "--synthetic",
        action="store_true",
        help="実portを使わない合成CDC",
    )
    approval_fixture_source.add_argument("--port", help="この実行だけに使う明示port")
    approval_fixture_source.add_argument(
        "--port-handle",
        type=Path,
        help="Git管理外の1行local handle file",
    )
    approval_fixture.add_argument(
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
    if args.command == "e2e":
        try:
            if args.synthetic:
                selected_port = "synthetic"
                synthetic_provider = SyntheticSerialProvider()
                provider: SerialProvider = synthetic_provider
                synthetic_device: SyntheticSerialProvider | None = synthetic_provider
            else:
                selected_port = resolve_port(
                    explicit_port=args.port,
                    handle_file=args.port_handle,
                )
                provider = PySerialProvider()
                synthetic_device = None
            if args.dry_run:
                run_e2e_dry_run(sys.stdout)
                return 0
            run_serial_e2e(
                sys.stdout,
                port=selected_port,
                provider=provider,
                synthetic_device=synthetic_device,
            )
        except Exception as error:
            print(f"e2e FAIL {type(error).__name__}", file=sys.stderr)
            return 1
        return 0
    if args.command == "control":
        try:
            if args.synthetic:
                selected_port = "synthetic"
            else:
                selected_port = resolve_port(
                    explicit_port=args.port,
                    handle_file=args.port_handle,
                )
            if args.dry_run:
                run_controller_dry_run(sys.stdout, cwd=args.cwd, label=args.label)
                return 0
            with ControllerInstanceGuard():
                control_provider: SerialProvider
                control_synthetic: SyntheticSerialProvider | None
                if args.synthetic:
                    synthetic_control_provider = SyntheticSerialProvider()
                    control_provider = synthetic_control_provider
                    control_synthetic = synthetic_control_provider
                else:
                    control_provider = PySerialProvider()
                    control_synthetic = None
                run_controller(
                    sys.stdout,
                    sys.stdin,
                    cwd=args.cwd,
                    label=args.label,
                    port=selected_port,
                    provider=control_provider,
                    synthetic_device=control_synthetic,
                )
        except Exception as error:
            print(f"control FAIL {type(error).__name__}", file=sys.stderr)
            return 1
        return 0
    if args.command == "approval-fixture":
        try:
            if args.synthetic:
                selected_port = "synthetic"
            else:
                selected_port = resolve_port(
                    explicit_port=args.port,
                    handle_file=args.port_handle,
                )
            if args.dry_run:
                run_approval_fixture_dry_run(sys.stdout)
                return 0
            fixture_provider: SerialProvider
            fixture_synthetic: SyntheticSerialProvider | None
            if args.synthetic:
                synthetic_fixture_provider = SyntheticSerialProvider()
                fixture_provider = synthetic_fixture_provider
                fixture_synthetic = synthetic_fixture_provider
            else:
                fixture_provider = PySerialProvider()
                fixture_synthetic = None
            run_approval_fixture(
                sys.stdout,
                sys.stdin,
                port=selected_port,
                provider=fixture_provider,
                synthetic_device=fixture_synthetic,
            )
        except Exception as error:
            print(f"approval_fixture FAIL {type(error).__name__}", file=sys.stderr)
            return 1
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
