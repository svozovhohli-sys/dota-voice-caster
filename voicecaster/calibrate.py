"""Interactive calibration: check that the model recognises *your* voice.

For every command the app asks you to say the phrase, records a few seconds
and reports what the recogniser heard and which command it resolved to. If a
command fails, edit its ``words`` in commands.json to something closer.
"""
from __future__ import annotations

import json
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover
    raise ImportError("sounddevice не установлен") from exc

from vosk import KaldiRecognizer, Model, SetLogLevel

from .commands import CommandSet

SetLogLevel(-1)


def _record(seconds: float, sample_rate: int, device: Optional[int]) -> bytes:
    frames = int(seconds * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1,
                   dtype="int16", device=device)
    sd.wait()
    return np.asarray(audio, dtype=np.int16).tobytes()


def run(cmdset: CommandSet, model_path: str, device: Optional[int] = None) -> int:
    settings = cmdset.settings
    sr = settings.sample_rate
    wake = settings.wake_word
    model = Model(model_path)
    grammar = cmdset.grammar_words()
    grammar_json = json.dumps(grammar + ["[unk]"], ensure_ascii=False)

    print("=== Калибровка ===")
    print("Скажите каждую фразу целиком, как будете говорить в игре.")
    print("После нажатия Enter идёт запись 3 секунды.\n")

    passed = 0
    failures = []
    for cmd in cmdset.commands:
        phrase = f"{wake} {cmd.phrases[0]}" if wake else cmd.phrases[0]
        input(f"[Enter] Говорите: «{phrase}» ")
        print("  ● записываю 3 с — говорите!", end="", flush=True)
        pcm = _record(3.0, sr, device)
        rec = KaldiRecognizer(model, sr, grammar_json)
        rec.AcceptWaveform(pcm)
        heard = json.loads(rec.FinalResult()).get("text", "").strip()

        text = heard
        if wake and wake in text:
            text = text.replace(wake, "").strip()
        elif wake:
            text = ""
        got = cmdset.resolve(text) if text else None
        if got is not None and got.name == cmd.name:
            passed += 1
            print(f"\r  ✓ услышано: {heard!r} -> {cmd.name}")
        else:
            failures.append(cmd.name)
            print(f"\r  ✗ услышано: {heard!r} (ожидали {cmd.name})")

    print(f"\nИтог: {passed}/{len(cmdset.commands)} команд распознаны.")
    if failures:
        print("Не распознаны: " + ", ".join(failures))
        print("Совет: откройте commands.json и подберите поле \"words\" поближе")
        print("по звучанию к вашей фразе, затем запустите калибровку снова.")
    return 0 if not failures else 1
