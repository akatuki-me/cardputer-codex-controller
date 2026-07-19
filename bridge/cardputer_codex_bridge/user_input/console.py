from __future__ import annotations

from .coordinator import HostUserInputCoordinator


class HostUserInputConsole:
    """質問を表示し、answer commandを短縮IDへ相関する。"""

    def __init__(self, coordinator: HostUserInputCoordinator) -> None:
        self._coordinator = coordinator

    def render(self) -> str:
        pending = self._coordinator.pending
        if not pending:
            return "pending questions: 0\n"
        lines = [f"pending questions: {len(pending)}"]
        for item in pending:
            availability = "available" if item.supported else "unavailable"
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
        return "\n".join(lines) + "\n"

    def execute(self, command: str) -> bool:
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


__all__ = ["HostUserInputConsole"]
