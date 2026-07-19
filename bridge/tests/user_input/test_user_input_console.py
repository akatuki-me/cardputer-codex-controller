from __future__ import annotations

from typing import cast

import pytest
from cardputer_codex_bridge.app_server import JsonObject, JsonValue, RequestId
from cardputer_codex_bridge.user_input import (
    HostUserInputConsole,
    HostUserInputCoordinator,
    read_tty_secret,
)
from cardputer_codex_bridge.user_input import console as console_module


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


def test_multiple_questions_have_distinct_ids_and_wait_for_all_answers() -> None:
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
    rendered = console.render()

    assert "pending questions: 2" in rendered
    assert "question-000001" in rendered
    assert "question-000002" in rendered
    assert console.execute("answer question-000001 A") is True
    assert responses == []
    assert "status=answer_staged" in console.render()
    assert console.execute("answer question-000002 追加回答") is True
    assert len(responses) == 1
    assert "追加回答" not in console.render()


def test_secret_question_uses_only_injected_no_echo_reader() -> None:
    responses: list[tuple[RequestId, JsonObject]] = []
    coordinator = _coordinator(responses)
    prompts: list[str] = []

    def read_secret(prompt: str) -> str:
        prompts.append(prompt)
        return "hidden-value"

    console = HostUserInputConsole(coordinator, secret_reader=read_secret)
    coordinator.handle_message(
        _request(
            "rpc-secret",
            questions=[
                {
                    "id": "secret-id",
                    "header": "秘密",
                    "question": "非表示で入力してください",
                    "isSecret": True,
                }
            ],
        )
    )

    rendered = console.render()

    assert "answer=secret" in rendered
    assert console.execute("answer question-000001 hidden-value") is False
    assert console.execute("secret question-000001") is True
    assert prompts == ["secret answer: "]
    assert len(responses) == 1
    assert "hidden-value" not in console.render()


def test_secret_command_is_rejected_without_no_echo_reader() -> None:
    coordinator = _coordinator([])
    console = HostUserInputConsole(coordinator)
    coordinator.handle_message(
        _request(
            questions=[
                {
                    "id": "secret-id",
                    "header": "秘密",
                    "question": "非表示で入力してください",
                    "isSecret": True,
                }
            ]
        )
    )

    assert console.execute("secret question-000001") is False


def test_tty_secret_reader_fails_closed_instead_of_echo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def warn_and_fallback(_prompt: str) -> str:
        console_module.warnings.warn(
            "fixture fallback",
            console_module.getpass.GetPassWarning,
        )
        return "must-not-return"

    monkeypatch.setattr(console_module.getpass, "getpass", warn_and_fallback)

    with pytest.raises(EOFError, match="no-echo input is unavailable"):
        read_tty_secret("secret answer: ")


@pytest.mark.parametrize(
    "command",
    [
        "",
        "answer",
        "answer question-000001",
        "answer question-000001   ",
        "respond question-000001 A",
        "secret",
        "secret question-000001 extra",
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
