"""Voice-activity detection: speech/silence intervals of a 16 kHz mono 16-bit recording."""

import array
import math
from collections.abc import Callable, Sequence

from app.features.recording.schemas import VadResult

SAMPLE_RATE = 16000
FRAME_MS = 30
AGGRESSIVENESS = 2
MIN_RUN_MS = 90
MAX_GAP_MS = 300
MARGIN_DB = 12.0
NOISE_PERCENTILE = 20
CORE_FRAME_MS = 10
SILENCE_DB = -120.0

Run = tuple[int, int]  # [start, end) in frames


def level_db(samples: Sequence[int]) -> float:
    if not samples:
        return SILENCE_DB
    mean_square = sum(s * s for s in samples) / len(samples)
    if mean_square <= 0:
        return SILENCE_DB
    return max(SILENCE_DB, 10 * math.log10(mean_square / (32768.0 * 32768.0)))


def percentile(values: Sequence[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return SILENCE_DB
    index = min(len(ordered) - 1, max(0, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def _frames(samples: array.array, frame_ms: int) -> list[array.array]:
    size = SAMPLE_RATE * frame_ms // 1000
    return [samples[i : i + size] for i in range(0, len(samples) - size + 1, size)]


def runs_of(decisions: Sequence[bool]) -> list[Run]:
    runs: list[Run] = []
    start: int | None = None
    for i, active in enumerate([*decisions, False]):
        if active and start is None:
            start = i
        elif not active and start is not None:
            runs.append((start, i))
            start = None
    return runs


def smooth(
    decisions: Sequence[bool], frame_ms: int, keep: Callable[[Run], bool] | None = None
) -> list[tuple[int, int]]:
    """Frame decisions -> merged speech intervals in ms.

    Drops speech runs shorter than MIN_RUN_MS, then (optionally) runs `keep` rejects,
    then merges gaps shorter than MAX_GAP_MS.
    """
    runs = [r for r in runs_of(decisions) if (r[1] - r[0]) * frame_ms >= MIN_RUN_MS]
    if keep is not None:
        runs = [r for r in runs if keep(r)]
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        start_ms, end_ms = start * frame_ms, end * frame_ms
        if merged and start_ms - merged[-1][1] < MAX_GAP_MS:
            merged[-1] = (merged[-1][0], end_ms)
        else:
            merged.append((start_ms, end_ms))
    return merged


def _energy_decisions(samples: array.array, frame_ms: int) -> list[bool]:
    levels = [level_db(f) for f in _frames(samples, frame_ms)]
    threshold = percentile(levels, NOISE_PERCENTILE) + MARGIN_DB
    return [level > threshold for level in levels]


def _core_filter(samples: array.array) -> Callable[[Run], bool]:
    """Keep a run only if it holds at least MIN_RUN_MS of energetic 10 ms sub-frames.

    webrtcvad holds its decision for a few frames after a burst, so a 60 ms click comes
    back as a 150 ms run; the fine-grained energy core tells the two apart.
    """
    loud = _energy_decisions(samples, CORE_FRAME_MS)
    per_frame = FRAME_MS // CORE_FRAME_MS

    def keep(run: Run) -> bool:
        core = loud[run[0] * per_frame : run[1] * per_frame]
        return sum(core) * CORE_FRAME_MS >= MIN_RUN_MS

    return keep


def _webrtc_decisions(pcm: bytes) -> tuple[str, list[bool]]:
    import webrtcvad

    vad = webrtcvad.Vad(AGGRESSIVENESS)
    size = SAMPLE_RATE * FRAME_MS // 1000 * 2
    decisions = [
        vad.is_speech(pcm[i : i + size], SAMPLE_RATE) for i in range(0, len(pcm) - size + 1, size)
    ]
    return f"webrtcvad-{AGGRESSIVENESS}/{FRAME_MS}ms", decisions


def analyze_pcm(pcm: bytes) -> VadResult:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    duration_ms = round(len(samples) * 1000 / SAMPLE_RATE)
    try:
        engine, decisions = _webrtc_decisions(pcm)
    except ImportError:
        engine, decisions = "energy-v1", _energy_decisions(samples, FRAME_MS)
    intervals = [
        (start, min(end, duration_ms))
        for start, end in smooth(decisions, FRAME_MS, keep=_core_filter(samples))
    ]
    speech_ms = sum(end - start for start, end in intervals)
    return VadResult(
        engine=engine,
        speech_intervals=intervals,
        speech_ms=speech_ms,
        leading_silence_ms=intervals[0][0] if intervals else duration_ms,
        trailing_silence_ms=duration_ms - intervals[-1][1] if intervals else duration_ms,
        duration_ms=duration_ms,
    )
