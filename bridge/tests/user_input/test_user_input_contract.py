from __future__ import annotations

from typing import cast

import pytest
from cardputer_codex_bridge.app_server import JsonObject, JsonValue, RequestId
from cardputer_codex_bridge.user_input import (
    MAX_REQUEST_ANSWER_BYTES,
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


def test_multiple_questions_are_staged_and_sent_once_when_complete() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = UserInputContract(
        lambda request_id, result: sent.append((request_id, result))
    )
    contract.handle_message(
        _request(
            questions=[
                {
                    "id": "first-private-id",
                    "header": "方針",
                    "question": "方針を選んでください",
                    "options": [{"label": "A", "description": "最小構成"}],
                    "isOther": False,
                },
                {
                    "id": "second-private-id",
                    "header": "補足",
                    "question": "補足を入力してください",
                    "isOther": True,
                },
            ]
        )
    )

    assert contract.respond("rpc-question", "first-private-id", "A") is False
    assert sent == []
    pending = contract.get("rpc-question")
    assert pending is not None
    assert pending.answered_question_ids == frozenset({"first-private-id"})

    assert (
        contract.respond("rpc-question", "second-private-id", "任意の補足")
        is True
    )

    assert sent == [
        (
            "rpc-question",
            {
                "answers": {
                    "first-private-id": {"answers": ["A"]},
                    "second-private-id": {"answers": ["任意の補足"]},
                }
            },
        )
    ]
    pending = contract.get("rpc-question")
    assert pending is not None
    assert pending.status is UserInputStatus.RESPONSE_SENT


def test_secret_question_requires_secret_surface() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = UserInputContract(
        lambda request_id, result: sent.append((request_id, result))
    )
    contract.handle_message(
        _request(
            questions=[
                {
                    "id": "private-secret-id",
                    "header": "秘密",
                    "question": "非表示で入力してください",
                    "isSecret": True,
                }
            ]
        )
    )

    with pytest.raises(UserInputStateError):
        contract.respond("rpc-question", "private-secret-id", "hidden-value")

    assert (
        contract.respond(
            "rpc-question",
            "private-secret-id",
            "hidden-value",
            secret_surface=True,
        )
        is True
    )
    assert sent[0][1] == {
        "answers": {"private-secret-id": {"answers": ["hidden-value"]}}
    }


def test_choice_without_other_requires_an_exact_option_label() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)
    contract.handle_message(
        _request(
            questions=[
                {
                    "id": "schema-question-id",
                    "header": "方針",
                    "question": "どの方針で進めますか？",
                    "isOther": False,
                    "options": [
                        {"label": "A", "description": "最小構成"},
                        {"label": "B", "description": "拡張構成"},
                    ],
                }
            ]
        )
    )

    with pytest.raises(UserInputStateError):
        contract.respond("rpc-question", "schema-question-id", "unknown")

    assert contract.respond("rpc-question", "schema-question-id", "A") is True


def test_request_answer_budget_is_enforced_before_response() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)
    questions: list[JsonValue] = [
        {"id": f"q-{index}", "header": "入力", "question": "入力してください"}
        for index in range(5)
    ]
    contract.handle_message(_request(questions=questions))
    chunk = "x" * (MAX_REQUEST_ANSWER_BYTES // 4)

    for index in range(4):
        assert contract.respond("rpc-question", f"q-{index}", chunk) is False
    with pytest.raises(UserInputStateError):
        contract.respond("rpc-question", "q-4", "x")


def test_discard_turn_removes_partial_answers_and_rejects_late_input() -> None:
    sent: list[tuple[RequestId, JsonObject]] = []
    contract = UserInputContract(
        lambda request_id, result: sent.append((request_id, result))
    )
    contract.handle_message(
        _request(
            questions=[
                {"id": "q-1", "header": "一", "question": "一つ目"},
                {"id": "q-2", "header": "二", "question": "二つ目"},
            ]
        )
    )
    assert contract.respond("rpc-question", "q-1", "first") is False

    discarded = contract.discard_turn("thread-1", "turn-1")

    assert tuple(request.request_id for request in discarded) == ("rpc-question",)
    assert contract.owns("rpc-question") is True
    pending = contract.get("rpc-question")
    assert pending is not None
    assert pending.status is UserInputStatus.DISCARDED
    assert pending.answered_question_ids == frozenset()
    assert sent == []
    with pytest.raises(UserInputStateError):
        contract.respond("rpc-question", "q-2", "late")

    resolved = contract.handle_message(_resolved())
    assert isinstance(resolved, UserInputResolved)
    assert resolved.response_sent is False
    assert contract.pending == ()


def test_discard_turn_keeps_response_sent_until_matching_resolved() -> None:
    contract = UserInputContract(lambda _request_id, _result: None)
    contract.handle_message(_request())
    assert contract.respond("rpc-question", "schema-question-id", "A") is True

    assert contract.discard_turn("thread-1", "turn-1") == ()
    pending = contract.get("rpc-question")
    assert pending is not None
    assert pending.status is UserInputStatus.RESPONSE_SENT

    resolved = contract.handle_message(_resolved())
    assert isinstance(resolved, UserInputResolved)
    assert resolved.response_sent is True


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
    contract.handle_message(
        _request(
            questions=[
                {"id": "q-1", "header": "一", "question": "一つ目"},
                {"id": "q-2", "header": "二", "question": "二つ目"},
            ]
        )
    )
    assert contract.respond("rpc-question", "q-1", "first") is False

    resolved = contract.handle_message(_resolved())

    assert isinstance(resolved, UserInputResolved)
    assert resolved.response_sent is False
    assert contract.pending == ()
    with pytest.raises(UserInputRequestIdError):
        contract.respond("rpc-question", "q-2", "late")


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
