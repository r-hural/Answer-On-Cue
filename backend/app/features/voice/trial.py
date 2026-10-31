"""Voice trial: a language's sample questions read by its available voices, on one static page.

Usage: python -m app.features.voice.trial [--language xx] [out_dir]
(default: Polish, data/voice-trial)
"""

import contextlib
import html
import re
import sys
import uuid
from pathlib import Path

from app.config import Settings, get_settings
from app.features.languages.profiles import LanguageProfile, get_profile
from app.features.providers.types import ProviderError, VoiceSpec
from app.features.voice import azure_voices, piper_catalog, service
from app.features.voice.catalog import OPENAI_VOICES

SAMPLE_QUESTIONS: tuple[str, ...] = (
    "Omów przyczyny i skutki rewolucji przemysłowej w Europie w XVIII i XIX wieku.",
    "Jakie znaczenie miała bitwa pod Grunwaldem 15 lipca 1410 roku?",
    "Wyjaśnij różnicę między PKB a PNB i podaj przykład.",
    "Co reguluje art. 415 Kodeksu cywilnego?",
    "Opisz cykl Krebsa i wskaż, gdzie zachodzi w komórce.",
    "Ile wynosi 3/4 z 2 400 zł i jak to obliczyłeś?",
    "Czym jest fotosynteza? Podaj jej ogólne równanie.",
    "Porównaj ustrój Rzeczypospolitej Obojga Narodów z monarchią absolutną we Francji.",
    "Wymień trzy zasady dynamiki Newtona i omów jedną z nich na przykładzie.",
    "Co oznacza skrót RODO i jakie prawa daje osobie, której dane dotyczą?",
    "Jaką rolę pełni enzym DNA-polimeraza podczas replikacji?",
    "Dlaczego 1 września 1939 r. uznaje się za początek II wojny światowej?",
)
NO_VOICES_MESSAGE = (
    "No voices available. Run scripts/get-piper-voice.ps1 "
    "or add an Azure Speech key in backend/.env."
)
USAGE = "Usage: python -m app.features.voice.trial [--language xx] [out_dir]"
_UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]")

PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice trial: {language}</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 1.5rem; color: #1f2328; background: #fff; }
  table { border-collapse: collapse; }
  th, td { border: 1px solid #d0d7de; padding: 0.4rem 0.6rem; vertical-align: middle; }
  th { background: #f6f8fa; text-align: left; }
  td.question { max-width: 28rem; }
  audio { width: 14rem; }
  .error { color: #b42318; font-size: 0.9rem; }
</style>
</head>
<body>
<h1>Voice trial: {language}</h1>
<p>Every available {language} voice read every question (rate 1.0). Listen and pay attention to:</p>
<ul>
{guide}
</ul>
<p>You set the chosen voice in <code>backend/.env</code>: <code>AOC_VOICE_PROVIDER</code>
and <code>AOC_VOICE_NAME</code>.</p>
"""
# The listening guide; the examples point at the Polish sample questions, so only Polish shows them.
GUIDE: tuple[tuple[str, str], ...] = (
    ("reading the text exactly, with no skipped or added words", ""),
    ("numbers and fractions", " (e.g. “3/4”, “2 400 zł”)"),
    ("dates and Roman numerals", " (e.g. “15 lipca 1410 roku”, “XVIII wiek”, “II wojna”)"),
    ("abbreviations", " (PKB, PNB, RODO, “art.”, “r.”)"),
    ("technical terms", " (cykl Krebsa, DNA-polimeraza)"),
    ("intonation: rising at the end of a question, natural in commands", ""),
)


def _page_head(profile: LanguageProfile) -> str:
    lines = []
    for index, (text, examples) in enumerate(GUIDE):
        end = "." if index == len(GUIDE) - 1 else ","
        lines.append(f"  <li>{text}{examples if profile.code == 'pl' else ''}{end}</li>")
    # replace, not format: the style sheet has braces.
    head = PAGE_HEAD.replace("{guide}", "\n".join(lines))
    return head.replace("{language}", html.escape(profile.name_en))


def _label(provider: str, voice: str) -> str:
    if provider == "openai":
        return OPENAI_VOICES[voice][0] if voice in OPENAI_VOICES else voice
    if provider == "piper":
        entry = piper_catalog.PINNED.get(voice)
        return piper_catalog.label(entry) if entry is not None else voice
    for voices in azure_voices.PINNED.values():
        for known in voices:
            if known.short_name == voice:
                return known.label
    return voice


def available_voices(settings: Settings, language: str = "pl") -> list[tuple[str, str]]:
    """The language's installed Piper voices, its Azure voices when a key and region are set,
    and one OpenAI voice (the default) when an OpenAI key is set: its readings are paid."""
    return [
        (voice.provider, voice.id)
        for voice in service.list_voices(settings, language)
        if voice.provider != "browser"
        and voice.installed
        and (voice.provider != "openai" or voice.id == settings.openai_voice_default)
    ]


def _render_voice(
    settings: Settings, provider: str, voice: str, folder: Path, questions: tuple[str, ...]
) -> list[str]:
    """One cell of HTML per question; raises ProviderError when the voice is unavailable."""
    engine = service.get_synthesizer(settings, provider)
    engine.engine_version(voice)
    engine = service.recorded(settings, provider, voice, engine)  # Azure is paid: recorded
    cells = []
    for number, question in enumerate(questions, start=1):
        name = f"{number:02d}.wav"
        try:
            result = engine.synthesize(
                question, VoiceSpec(voice=voice, rate=1.0), operation_id=uuid.uuid4().hex
            )
        except ProviderError as error:
            cells.append(f'<td class="error">{html.escape(error.message)}</td>')
            continue
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(result.audio)
        src = html.escape(f"{folder.name}/{name}")
        cells.append(f'<td><audio controls preload="none" src="{src}"></audio></td>')
    return cells


def render_trial(
    settings: Settings,
    out_dir: Path,
    voices: list[tuple[str, str]] | None = None,
    *,
    language: str = "pl",
) -> Path:
    profile = get_profile(language)
    questions = profile.trial_sentences
    if voices is None:
        voices = available_voices(settings, language)
    out_dir.mkdir(parents=True, exist_ok=True)
    columns: list[tuple[str, list[str]]] = []
    for provider, voice in voices:
        folder = out_dir / _UNSAFE.sub("_", f"{provider}-{voice}")
        try:
            cells = _render_voice(settings, provider, voice, folder, questions)
        except ProviderError:
            continue  # not installed, or no key: the voice simply gets no column
        header = f"{html.escape(_label(provider, voice))}<br><small>{html.escape(voice)}</small>"
        columns.append((header, cells))

    parts = [_page_head(profile)]
    if not columns:
        parts.append(f"<p>{html.escape(NO_VOICES_MESSAGE)}</p>\n")
    parts.append("<table>\n<thead><tr><th>No.</th><th>Question</th>")
    parts += [f"<th>{header}</th>" for header, _ in columns]
    parts.append("</tr></thead>\n<tbody>\n")
    for row, question in enumerate(questions):
        parts.append(f'<tr><td>{row + 1}</td><td class="question">{html.escape(question)}</td>')
        parts += [cells[row] for _, cells in columns]
        parts.append("</tr>\n")
    parts.append("</tbody>\n</table>\n</body>\n</html>\n")
    index = out_dir / "index.html"
    index.write_text("".join(parts), encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(AttributeError):
        sys.stdout.reconfigure(errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    language = "pl"
    if "--language" in args:
        at = args.index("--language")
        if at + 1 >= len(args):
            print(USAGE)
            return 2
        language = args[at + 1]
        del args[at : at + 2]
    try:
        get_profile(language)
    except ValueError:
        print(f"Unknown language: {language}. {USAGE}")
        return 2
    settings = get_settings()
    out_dir = Path(args[0]) if args else settings.data_dir / "voice-trial"
    voices = available_voices(settings, language)
    if not voices:
        print(NO_VOICES_MESSAGE)
        return 1
    print(f"Voices: {', '.join(voice for _, voice in voices)}", flush=True)
    index = render_trial(settings, out_dir, voices, language=language)
    print(index.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
