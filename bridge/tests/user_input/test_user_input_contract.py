from __future__ import annotations

from typing import cast

import pytest
from cardputer_codex_bridge.app_server import JsonObject, JsonValue, RequestId
from cardputer_codex_bridge.user_input import (
    UserInputContract,
    UserInputProtocolError,
    UserInputRequest,
    UserInputRequestIdError,
    UserInputResolved,
    UserInputStateError,
    UserInputStatus,
)


def _request(
    request_id: RequestId = "rpc-question",
    *,
    questions: list[JsonValue] | None = None,
) -> JsonObject:
    return {
        "id": request_id,
        "method": "item/tool/requestUserInput",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "item-1",
            "autoResolutionMs": 60000,
            "questions": questions
            if questions is not None
            else [
                {
                    "id": "schema-question-id",
                    "header": "方針",
                    "question": "どの方針で進めますか？",
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


def _resolved(
    request_id: RequestId = "rpc-question",
    *,
    thread_id: str = "thread-1",
) -> JsonObject:
    return {
        "method": "serverRequest/resolved",
        "params": {"requestId": request_id, "threadId": thread_id},
    }


def test_response_uses_schema_question_id_and_waits_for_resolved() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = UserInputContract(
        lambda request_id, result: sent.append((request_id, result))
    )

    request = contract.handle_message(_request())

    assert isinstance(request, UserInputRequest)
    assert request.auto_resolution_ms == 60000
    assert request.questions[0].question_id == "schema-question-id"
    assert request.questions[0].options[0].label == "A"
    assert contract.get("rpc-question") is not None
    assert contract.get("rpc-question").status is UserInputStatus.AWAITING_ANSWER  # type: ignore[union-attr]

    contract.respond("rpc-question", "schema-question-id", "A で進める")

    assert sent == [
        (
            "rpc-question",
            {
                "answers": {
                    "schema-question-id": {"answers": ["A で進める"]}
                }
            },
        )
    ]
    pending = contract.get("rpc-question")
    assert pending is not None
    assert pending.status is UserInputStatus.RESPONSE_SENT

    resolved = contract.handle_message(_resolved())

    assert isinstance(resolved, UserInputResolved)
    assert resolved.response_sent is True
    assert contract.get("rpc-question") is None


def test_wrong_question_double_response_and_unknown_id_are_rejected() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = UserInputContract(
        lambda request_id, result: sent.append((request_id, result))
    )
    contract.handle_message(_request())

    with pytest.raises(UserInputRequestIdError):
        contract.respond("rpc-other", "schema-question-id", "A")
    with pytest.raises(UserInputStateError):
        contract.respond("rpc-question", "question-other", "A")

    contract.respond("rpc-question", "schema-question-id", "A")
    with pytest.raises(UserInputStateError):
        contract.respond("rpc-question", "schema-question-id", "B")

    assert len(sent) == 1


def test_auto_resolution_before_answer_is_recorded_without_response() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)
    contract.handle_message(_request())

    resolved = contract.handle_message(_resolved())

    assert isinstance(resolved, UserInputResolved)
    assert resolved.response_sent is False
    assert contract.pending == ()


def test_resolution_requires_matching_request_and_thread() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)
    contract.handle_message(_request())

    with pytest.raises(UserInputRequestIdError):
        contract.handle_message(_resolved("rpc-other"))
    with pytest.raises(UserInputRequestIdError):
        contract.handle_message(_resolved(thread_id="thread-other"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda message: message.pop("id"),
        lambda message: message.update(params=[]),
        lambda message: cast(dict[str, object], message["params"]).update(
            questions="invalid"
        ),
        lambda message: cast(dict[str, object], message["params"]).update(
            autoResolutionMs=-1
        ),
        lambda message: cast(dict[str, object], message["params"]).update(
            autoResolutionMs=1 << 64
        ),
        lambda message: cast(dict[str, object], message["params"])["questions"][0].update(  # type: ignore[index,union-attr]
            isSecret="false"
        ),
        lambda message: cast(dict[str, object], message["params"])["questions"][0].update(  # type: ignore[index,union-attr]
            options=[{"label": "A"}]
        ),
    ],
)
def test_malformed_request_is_rejected(
    mutate: object,
) -> None:
    message = _request()
    cast(object, mutate)(message)  # type: ignore[operator]
    contract = UserInputContract(lambda _request_id, _result: None)

    with pytest.raises(UserInputProtocolError):
        contract.handle_message(message)


def test_duplicate_request_and_question_ids_are_rejected() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)
    contract.handle_message(_request())
    with pytest.raises(UserInputRequestIdError):
        contract.handle_message(_request())

    duplicate_questions = [
        {
            "id": "same",
            "header": "A",
            "question": "First",
            "options": None,
        },
        {
            "id": "same",
            "header": "B",
            "question": "Second",
            "options": None,
        },
    ]
    with pytest.raises(UserInputProtocolError):
        UserInputContract(lambda _request_id, _result: None).handle_message(
            _request("rpc-duplicate-question", questions=duplicate_questions)
        )


def test_unknown_message_is_left_for_another_consumer() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)

    assert contract.handle_message({"method": "turn/completed", "params": {}}) is None
    assert contract.pending == ()
