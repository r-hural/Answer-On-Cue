from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements"


def test_runtime_requirements_have_no_dev_packages():
    for path in REQUIREMENTS.glob("*.txt"):
        if path.name == "dev.txt":
            continue
        text = path.read_text(encoding="utf-8").lower()
        for name in ("pytest", "ruff", "fpdf2"):
            assert name not in text, f"{path.name} mentions {name}"


def test_fpdf2_is_a_dev_requirement():
    assert "fpdf2" in (REQUIREMENTS / "dev.txt").read_text(encoding="utf-8")
