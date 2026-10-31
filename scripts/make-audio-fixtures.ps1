# Generates synthetic audio test fixtures.
# Speech comes from Windows SAPI (System.Speech); silence, noise and clicks are generated.
# Never use a real person's recording here.
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$BackendFixtures = Join-Path $Root 'backend\tests\fixtures\audio'
$E2EFixtures = Join-Path $Root 'frontend\e2e\fixtures'
$VenvPython = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$Sentence = 'The quick brown fox jumps over the lazy dog near the river bank.'

New-Item -ItemType Directory -Force -Path $BackendFixtures | Out-Null
New-Item -ItemType Directory -Force -Path $E2EFixtures | Out-Null

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("pg-fixtures-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voices = $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo }
    if (-not $voices) { throw 'No SAPI voices installed.' }
    $voice = $voices | Where-Object { $_.Culture.Name -eq 'en-US' } | Select-Object -First 1
    if (-not $voice) { $voice = $voices | Select-Object -First 1 }
    $synth.SelectVoice($voice.Name)
    $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
        16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $speechRaw = Join-Path $work 'speech_raw.wav'
    $synth.SetOutputToWaveFile($speechRaw, $format)
    $synth.Speak($Sentence)
    $synth.SetOutputToNull()
    $synth.Dispose()
    Write-Host "SAPI voice: $($voice.Name) ($($voice.Culture.Name))"

    $helper = Join-Path $work 'assemble.py'
    @'
import array
import json
import random
import sys
import wave
from pathlib import Path

RATE = 16000
raw_path, backend_dir, voice = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]

with wave.open(str(raw_path), "rb") as w:
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
    speech = array.array("h", w.readframes(w.getnframes()))

# Trim SAPI's own leading and trailing digital silence (|x| < 0.001 full scale).
threshold = 0.001 * 32768
loud = [i for i, s in enumerate(speech) if abs(s) >= threshold]
speech = speech[loud[0] : loud[-1] + 1]


def silence(seconds: float) -> array.array:
    return array.array("h", [0]) * int(round(seconds * RATE))


def ms(samples: int) -> int:
    return round(samples * 1000 / RATE)


def write(name: str, samples: array.array) -> None:
    with wave.open(str(backend_dir / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


lead = silence(2.0)
speech_mid = lead + speech + silence(2.0)
write("speech_mid.wav", speech_mid)

# White noise at -50 dBFS RMS: uniform in [-a, a] has RMS a / sqrt(3).
rng = random.Random(20261020)
amplitude = 10 ** (-50 / 20) * 3**0.5 * 32767
noisy = array.array(
    "h",
    (max(-32768, min(32767, round(s + rng.uniform(-amplitude, amplitude)))) for s in speech_mid),
)
write("speech_noisy.wav", noisy)

clicks = silence(3.0)
click_len = int(0.060 * RATE)
click_starts = [int(t * RATE) for t in (0.5, 1.5, 2.5)]
for start in click_starts:
    for i in range(click_len):
        clicks[start + i] = 32767 if (i // 8) % 2 == 0 else -32768
write("click_only.wav", clicks)

write("silence.wav", silence(3.0))

speech_start = len(lead)
speech_end = len(lead) + len(speech)
info = {
    "sample_rate": RATE,
    "sapi_voice": voice,
    "files": {
        "speech_mid.wav": {
            "duration_ms": ms(len(speech_mid)),
            "samples": len(speech_mid),
            "speech": [[ms(speech_start), ms(speech_end)]],
        },
        "speech_noisy.wav": {
            "duration_ms": ms(len(noisy)),
            "samples": len(noisy),
            "speech": [[ms(speech_start), ms(speech_end)]],
            "noise_dbfs": -50,
        },
        "click_only.wav": {
            "duration_ms": ms(len(clicks)),
            "samples": len(clicks),
            "speech": [],
            "clicks": [[ms(s), ms(s + click_len)] for s in click_starts],
        },
        "silence.wav": {"duration_ms": 3000, "samples": 3 * RATE, "speech": []},
    },
}
(backend_dir / "fixtures.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
print(json.dumps(info["files"]["speech_mid.wav"]))
'@ | Set-Content -Path $helper -Encoding UTF8

    $python = $VenvPython
    if (-not (Test-Path $python)) { $python = 'python' }
    & $python -I $helper $speechRaw $BackendFixtures $voice.Name
    if ($LASTEXITCODE -ne 0) { throw "Assembly failed (exit $LASTEXITCODE)." }

    foreach ($name in 'speech_mid.wav', 'click_only.wav') {
        Copy-Item -Force (Join-Path $BackendFixtures $name) (Join-Path $E2EFixtures $name)
    }
    foreach ($file in (Get-ChildItem $BackendFixtures) + (Get-ChildItem $E2EFixtures)) {
        if ($file.Length -ge 1MB) { throw "Fixture over 1 MB: $($file.FullName)" }
        Write-Host ("{0,-40} {1,8} B" -f $file.FullName.Substring($Root.Length + 1), $file.Length)
    }
    Write-Host 'FIXTURES OK'
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
