from __future__ import annotations

from typing import cast

import pytest
from cardputer_codex_bridge.app_server import JsonObject, JsonValue, RequestId
from cardputer_codex_bridge.user_input import (
    HostUserInputConsole,
    HostUserInputCoordinator,
)


def _request(
    request_id: RequestId = "private-rpc-id",
    *,
    questions: list[JsonValue] | None = None,
) -> JsonObject:
    return {
        "id": request_id,
        "method": "item/tool/requestUserInput",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "private-item-id",
            "questions": questions
            if questions is not None
            else [
                {
                    "id": "private-question-id",
                    "header": "方針",
                    "question": "どの方針で進めますか？\u001b[31m",
                    "isOther": True,
                    "isSecret": False,
                    "options": [
                        {"label": "A", "description": "最小構成"},
                        {"label": "B", "description": "拡張構成"},
                    ],
                }
            ],
        },
    }


def _coordinator(
    responses: list[tuple[RequestId, JsonObject]],
) -> HostUserInputCoordinator:
    return HostUserInputCoordinator(
        send_response=lambda request_id, result: responses.append(
            (request_id, result)
        )
    )


def test_console_renders_local_id_and_answers_without_leaking_internal_ids() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = _coordinator(responses)
    console = HostUserInputConsole(coordinator)
    coordinator.handle_message(_request())

    rendered = console.render()

    assert "pending questions: 1" in rendered
    assert "question-000001" in rendered
    assert "方針" in rendered
    assert "A: 最小構成" in rendered
    assert "\\u001b" in rendered
    assert "\u001b[31m" not in rendered
    assert "private-rpc-id" not in rendered
    assert "private-question-id" not in rendered
    assert "private-item-id" not in rendered

    assert console.execute("answer question-000001 A  を選択") is True

    assert responses == [
        (
            "private-rpc-id",
            {
                "answers": {
                    "private-question-id": {"answers": ["A  を選択"]}
                }
            },
        )
    ]
    assert "A  を選択" not in console.render()
    assert "status=response_sent" in console.render()


def test_multiple_and_secret_questions_are_visible_but_not_answerable() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = _coordinator(responses)
    console = HostUserInputConsole(coordinator)
    first = cast(dict[str, JsonValue], _request()["params"])["questions"]
    assert isinstance(first, list)
    coordinator.handle_message(
        _request(
            "rpc-multiple",
            questions=[*first, {"id": "q2", "header": "追加", "question": "追加質問"}],
        )
    )
    coordinator.handle_message(
        _request(
            "rpc-secret",
            questions=[
                {
                    "id": "secret-id",
                    "header": "秘密",
                    "question": "秘密情報を入力してください",
                    "isSecret": True,
                }
            ],
        )
    )

    rendered = console.render()

    assert "multiple_questions" in rendered
    assert "secret_input" in rendered
    assert console.execute("answer question-000001 A") is False
    assert console.execute("answer question-000002 secret-value") is False
    assert "secret-value" not in rendered
    assert responses == []


@pytest.mark.parametrize(
    "command",
    [
        "",
        "answer",
        "answer question-000001",
        "answer question-000001   ",
        "respond question-000001 A",
    ],
)
def test_console_rejects_malformed_or_empty_commands(command: str) -> None:
    console = HostUserInputConsole(_coordinator([]))

    with pytest.raises(ValueError):
        console.execute(command)


def test_unknown_double_and_oversize_answers_are_rejected() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = _coordinator(responses)
    console = HostUserInputConsole(coordinator)
    coordinator.handle_message(_request())

    assert console.execute("answer question-999999 A") is False
    assert console.execute("answer question-000001 A") is True
    assert console.execute("answer question-000001 B") is False

    coordinator.handle_message(_request("rpc-second"))
    assert console.execute("answer question-000002 " + "x" * 4097) is False
    assert len(responses) == 1
