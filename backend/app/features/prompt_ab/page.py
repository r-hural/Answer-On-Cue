"""The A/B page: one static file, every value escaped, no script and no external asset."""

import html
import json
import re
from pathlib import Path
from typing import Any

# A Polish letter, or a common Polish word, in an English output (the design asks for English
# throughout).
_POLISH_LETTERS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
_POLISH_WORDS = re.compile(
    r"\b(jest|oraz|który|która|które|się|nie|dla|przez|pytanie|odpowiedź)\b", re.IGNORECASE
)

CSS = """
body { font: 15px/1.45 system-ui, sans-serif; margin: 0 auto; max-width: 1400px; padding: 16px;
  color: #1d1d1f; background: #fafafa; }
h1 { font-size: 22px; } h2 { font-size: 18px; margin-top: 32px; } h3 { font-size: 16px; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; }
th, td { border: 1px solid #d0d0d5; padding: 8px; vertical-align: top; text-align: left; }
th { background: #eeeef2; }
pre { white-space: pre-wrap; word-break: break-word; font-size: 12.5px; margin: 0; }
.error { color: #a40000; } .muted { color: #666; } .flag { color: #a40000; font-weight: 600; }
.ok { color: #116611; font-weight: 600; }
details { margin-top: 6px; } summary { cursor: pointer; color: #333; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }
dt { font-weight: 600; } dd { margin: 0; }
"""


def english_findings(output: Any) -> str:
    """'English throughout: yes', or what was found."""
    text = json.dumps(output, ensure_ascii=False)
    found = sorted(set(_POLISH_LETTERS.findall(text)))
    words = sorted({w.lower() for w in _POLISH_WORDS.findall(text)})
    if not found and not words:
        return "English throughout: yes"
    parts = []
    if found:
        parts.append("Polish letters " + " ".join(found))
    if words:
        parts.append("Polish words " + ", ".join(words))
    return "English throughout: no (" + "; ".join(parts) + ")"


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _pre(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    return f"<pre>{_e(text)}</pre>"


def _cell(cell: dict[str, Any] | None, dry_run: bool, lang: str) -> str:
    if cell is None:
        return '<td class="muted">no such case</td>'
    parts = []
    if cell["error"]:
        parts.append(f'<p class="error">{_e(cell["error"])}</p>')
    elif cell["output"] is not None:
        parts.append(_pre(cell["output"]))
    elif dry_run:
        parts.append('<p class="muted">not called (dry run)</p>')
    else:
        parts.append('<p class="muted">not called</p>')
    for note in cell["notes"]:
        css = "flag" if note.startswith(("invalid", "English throughout: no")) else ""
        css = "ok" if note == "English throughout: yes" else css
        parts.append(f'<p class="{css}">{_e(note)}</p>')
    parts.append(
        "<details><summary>Prompt</summary><h3>System</h3>"
        + _pre(cell["system"])
        + "<h3>User</h3>"
        + _pre(cell["user"])
        + "</details>"
    )
    return f'<td lang="{lang}">' + "".join(parts) + "</td>"


def _header(result: dict[str, Any]) -> str:
    versions = result["versions"]
    calls = result["calls"]
    rows = [
        ("Old ref", f"{result['old_ref']} ({result['old_sha']})"),
        ("Old prompts", ", ".join(versions["old"].values())),
        ("New prompts", ", ".join(versions["new"].values())),
        ("Models", ", ".join(f"{k}: {v}" for k, v in result["models"].items())),
        ("Date", result["date"]),
        ("Calls", f"{calls['made']} made / {calls['planned']} planned / {calls['limit']} limit"),
        ("Cost", f"${result['usd']:.4f} (today's prices times each response's usage)"),
        ("Subject", f"{result['subject']['name']} (id {result['subject']['id']})"),
    ]
    if result["dry_run"]:
        rows.append(("Mode", "dry run: no call made"))
    body = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in rows)
    missing = "".join(
        f"<li>{_e(part)}: {_e(reason)}</li>" for part, reason in result["missing"].items()
    )
    if missing:
        missing = f"<h3>Not compared</h3><ul>{missing}</ul>"
    return f"<dl>{body}</dl>{missing}"


def _inputs(info: Any) -> str:
    if info is None:
        return ""
    if isinstance(info, str):
        return f'<p lang="pl">{_e(info)}</p>'
    parts = [f'<p><b>Question:</b> <span lang="pl">{_e(info["question"])}</span></p>']
    parts.append(f'<p><b>Transcript:</b> <span lang="pl">{_e(info["transcript"])}</span></p>')
    return "".join(parts)


def _polish(result: dict[str, Any]) -> str:
    cells = {(c["case"], c["side"]): c for c in result["cells"]}
    cases = list(dict.fromkeys(c["case"] for c in result["cells"] if c["side"] == "old"))
    parts = ["<h2>Polish: old and new prompts on the same input</h2>"]
    for case in cases:
        info = result["inputs"].get(case)
        stored = case.startswith("assess-") and isinstance(info, dict)
        head = "<th>Old</th><th>New</th>" + ("<th>Stored result</th>" if stored else "")
        row = _cell(cells.get((case, "old")), result["dry_run"], "pl") + _cell(
            cells.get((case, "new")), result["dry_run"], "pl"
        )
        if stored:
            reference = {
                "band": info["stored_band"],
                "scores": info["stored_scores"],
                "assessment": info["stored"],
            }
            row += f'<td lang="pl">{_pre(reference)}</td>'
        parts.append(
            f"<h3>{_e(case)}</h3>{_inputs(info)}<table><tr>{head}</tr><tr>{row}</tr></table>"
        )
    return "".join(parts)


def _english(result: dict[str, Any]) -> str:
    parts = ["<h2>English sample: new prompts only</h2>"]
    for cell in (c for c in result["cells"] if c["side"] == "en"):
        parts.append(
            f"<h3>{_e(cell['case'])}</h3><table><tr><th>New</th></tr><tr>"
            + _cell(cell, result["dry_run"], "en")
            + "</tr></table>"
        )
    return "".join(parts)


def write_page(out_dir: Path, result: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Prompt A/B</title><style>{CSS}</style></head><body>"
        "<h1>Prompt A/B</h1>"
        + _header(result)
        + _polish(result)
        + _english(result)
        + "</body></html>"
    )
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
