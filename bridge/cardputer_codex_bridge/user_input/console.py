from __future__ import annotations

import getpass
import warnings
from collections.abc import Callable

from .coordinator import HostUserInputCoordinator

type SecretReader = Callable[[str], str]


def read_tty_secret(prompt: str) -> str:
    """echoを無効化できない端末では入力せずfail closedする。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except getpass.GetPassWarning as error:
        raise EOFError("no-echo input is unavailable") from error


class HostUserInputConsole:
    """質問を表示し、echo/no-echo commandを短縮IDへ相関する。"""

    def __init__(
        self,
        coordinator: HostUserInputCoordinator,
        *,
        secret_reader: SecretReader | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._secret_reader = secret_reader

    def render(self) -> str:
        pending = self._coordinator.pending
        if not pending:
            return "pending questions: 0\n"
        lines = [f"pending questions: {len(pending)}"]
        for item in pending:
            availability = "secret" if item.is_secret else "available"
            lines.append(
                f"[{item.input_id}] status={item.status} answer={availability}"
            )
            if not item.supported:
                lines.append(f"  reason: {item.unsupported_reason}")
                lines.append(f"  questions: {item.question_count}")
                continue
            lines.extend(_prefixed_lines("  header: ", item.header))
            lines.extend(_prefixed_lines("  question: ", item.question))
            if item.options:
                lines.append("  options:")
                for option in item.options:
                    option_text = f"{option.label}: {option.description}"
                    lines.extend(_prefixed_lines("    - ", option_text))
            if item.is_other:
                lines.append("  other: allowed")
            if item.is_secret:
                lines.append("  input: no-echo only")
        return "\n".join(lines) + "\n"

    def execute(self, command: str) -> bool:
        secret_parts = command.split()
        if secret_parts and secret_parts[0] == "secret":
            if len(secret_parts) != 2:
                raise ValueError("command must be: secret <id>")
            input_id = secret_parts[1]
            if (
                self._secret_reader is None
                or not self._coordinator.accepts_secret(input_id)
            ):
                return False
            try:
                answer = self._secret_reader("secret answer: ")
            except EOFError:
                return False
            return self._coordinator.respond_host(
                input_id,
                answer,
                secret_surface=True,
            )
        parts = command.split(maxsplit=2)
        if len(parts) != 3 or parts[0] != "answer" or not parts[2].strip():
            raise ValueError("command must be: answer <id> <text>")
        return self._coordinator.respond_host(parts[1], parts[2])


def _prefixed_lines(prefix: str, value: str) -> list[str]:
    escaped = _escape_control_characters(value)
    parts = escaped.splitlines() or [""]
    return [
        prefix + part if index == 0 else " " * len(prefix) + part
        for index, part in enumerate(parts)
    ]


def _escape_control_characters(value: str) -> str:
    result: list[str] = []
    for character in value:
        if character == "\n" or character.isprintable():
            result.append(character)
        else:
            result.append(f"\\u{ord(character):04x}")
    return "".join(result)


__all__ = ["HostUserInputConsole", "SecretReader", "read_tty_secret"]
