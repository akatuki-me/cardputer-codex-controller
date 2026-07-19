from pathlib import Path


def test_production_upload_uses_the_verified_rom_loader_path() -> None:
    repository = Path(__file__).resolve().parents[3]
    configuration = (repository / "firmware" / "platformio.ini").read_text(
        encoding="utf-8"
    )
    production = configuration.split("[env:cardputer_adv]", maxsplit=1)[1].split(
        "[env:cardputer_adv_bringup]", maxsplit=1
    )[0]

    assert "upload_speed = 115200" in production
    assert "--no-stub" in production
