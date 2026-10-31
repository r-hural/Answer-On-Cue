"""The voices the app knows about, with display labels."""

from typing import Literal

Gender = Literal["female", "male"]

PIPER_VOICES: dict[str, tuple[str, Gender]] = {
    "pl_PL-gosia-medium": ("Gosia (Piper, local)", "female"),
    "pl_PL-darkman-medium": ("Darkman (Piper, local)", "male"),
    "pl_PL-mc_speech-medium": ("MC Speech (Piper, local)", "male"),
    "pl_PL-bass-high": ("Bass (Piper, local, high quality)", "male"),
}
AZURE_VOICES: dict[str, tuple[str, Gender]] = {
    "pl-PL-ZofiaNeural": ("Zofia (Azure, neural)", "female"),
    "pl-PL-MarekNeural": ("Marek (Azure, neural)", "male"),
    "pl-PL-AgnieszkaNeural": ("Agnieszka (Azure, neural)", "female"),
}
# Multilingual, no language prefix; genders by ear.
OPENAI_VOICES: dict[str, tuple[str, Gender]] = {
    "marin": ("Marin (OpenAI Realtime, recommended)", "female"),
    "cedar": ("Cedar (OpenAI Realtime, recommended)", "male"),
    "alloy": ("Alloy (OpenAI Realtime)", "female"),
    "ash": ("Ash (OpenAI Realtime)", "male"),
    "ballad": ("Ballad (OpenAI Realtime)", "male"),
    "coral": ("Coral (OpenAI Realtime)", "female"),
    "echo": ("Echo (OpenAI Realtime)", "male"),
    "sage": ("Sage (OpenAI Realtime)", "female"),
    "shimmer": ("Shimmer (OpenAI Realtime)", "female"),
    "verse": ("Verse (OpenAI Realtime)", "male"),
}
BROWSER_VOICE = "browser"
BROWSER_LABEL = "Browser voice (built into the system)"
DEFAULT_PIPER_VOICE = "pl_PL-bass-high"


def looks_like_azure_voice(voice: str) -> bool:
    """Azure names look like pl-PL-ZofiaNeural; Piper ids like pl_PL-bass-high."""
    return voice.endswith("Neural") and "_" not in voice


def looks_like_openai_voice(voice: str) -> bool:
    return voice in OPENAI_VOICES
