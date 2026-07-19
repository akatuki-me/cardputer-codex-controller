from pathlib import Path


def test_approval_confirmation_uses_cardputer_enter_state() -> None:
    repository = Path(__file__).resolve().parents[3]
    source = (repository / "firmware" / "src" / "main.cpp").read_text(
        encoding="utf-8"
    )

    assert "M5Cardputer.Keyboard.keysState().enter" in source
    assert "M5Cardputer.Keyboard.isKeyPressed('\\n')" not in source
