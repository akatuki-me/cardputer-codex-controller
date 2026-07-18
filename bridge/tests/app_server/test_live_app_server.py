from __future__ import annotations

import os

import pytest
from cardputer_codex_bridge.app_server import (
    AppServerClient,
    ClientInfo,
    codex_app_server_command,
)


@pytest.mark.skipif(
    os.environ.get("CARDPUTER_CODEX_LIVE_TEST") != "1",
    reason="認証済みCodex CLIを使うlocal acceptance test",
)
def test_live_app_server_01445_initializes_and_exits_cleanly() -> None:
    client = AppServerClient(command=codex_app_server_command())
    client.start()
    try:
        result = client.initialize(
            ClientInfo(
                name="cardputer-codex-controller",
                title="Cardputer Codex Controller",
                version="0.0.0",
            )
        )
    finally:
        shutdown = client.close()

    assert result.codex_version == "0.144.5"
    assert result.platform_family
    assert result.platform_os
    assert shutdown.forced is False
    assert shutdown.exit_code == 0
