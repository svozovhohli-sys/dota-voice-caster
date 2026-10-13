"""Offline speech recognition with Vosk.

Captures microphone audio in a background thread, feeds a constrained grammar
recognizer and pushes recognized phrases to a queue. A grammar limited to the
command words gives much better accuracy and far fewer false triggers than
open-vocabulary recognition.
"""
from __future__ import annotations

import json
import os
import queue
import re
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

import numpy as np

try:
    import sounddevice as sd
except ImportError as exc:  # pragma: no cover
    raise ImportError("sounddevice не установлен: pip install -r requirements.txt") from exc

from vosk import KaldiRecognizer, Model, SetLogLevel

SetLogLevel(-1)


@dataclass
class Recognition:
    text: str
    is_final: bool


def _capture_stderr(fn: Callable[[], object]) -> str:
    """Run ``fn`` while capturing the process' C-level stderr (fd 2)."""
    saved = os.dup(2)
    tmp = tempfile.TemporaryFile()
    try:
        os.dup2(tmp.fileno(), 2)
        fn()
    finally:
        os.dup2(saved, 2)
        os.close(saved)
    tmp.seek(0)
    data = tmp.read().decode("utf-8", "replace")
    tmp.close()
    return data


class VoiceListener:
    def __init__(
        self,
        model_path: str,
        grammar: Optional[Iterable[str]] = None,
        sample_rate: int = 16000,
        device: Optional[int] = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._model = Model(model_path)
        self._queue: "queue.Queue[Recognition]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._grammar = self._build_grammar(grammar)

    @staticmethod
    def _build_grammar(phrases: Optional[Iterable[str]]) -> Optional[str]:
        if not phrases:
            return None
        # Vosk grammar: a JSON array of allowed phrases + [unk].
        vocab = sorted({p.strip().lower() for p in phrases if p.strip()})
        vocab.append("[unk]")
        return json.dumps(vocab, ensure_ascii=False)

    def missing_words(self, words: Iterable[str]) -> List[str]:
        """Return grammar words that are absent from the model's vocabulary.

        Vosk logs a warning for every such word; we capture the C-level stderr
        while building a probe recognizer to read those warnings.
        """
        grammar = self._build_grammar(words)
        if grammar is None:
            return []
        try:
            log = _capture_stderr(
                lambda: KaldiRecognizer(self._model, self.sample_rate, grammar)
            )
        except Exception:  # pragma: no cover - best effort
            return []
        return re.findall(r"missing in vocabulary: '([^']+)'", log)

    def _make_recognizer(self) -> KaldiRecognizer:
        if self._grammar is not None:
            return KaldiRecognizer(self._model, self.sample_rate, self._grammar)
        return KaldiRecognizer(self._model, self.sample_rate)

    def _audio_thread(self) -> None:
        rec = self._make_recognizer()
        blocksize = int(self.sample_rate * 0.1)  # 100 ms

        def callback(indata, _frames, _time, _status):
            if self._stop.is_set():
                return
            pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()
            if rec.AcceptWaveform(pcm):
                text = json.loads(rec.Result()).get("text", "")
                self._queue.put(Recognition(text=text, is_final=True))
            else:
                partial = json.loads(rec.PartialResult()).get("partial", "")
                if partial:
                    self._queue.put(Recognition(text=partial, is_final=False))

        with sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=blocksize,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=callback,
        ):
            while not self._stop.is_set():
                sd.sleep(100)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._audio_thread, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def poll(self, timeout: float = 0.1) -> Optional[Recognition]:
        """Return the next recognition event, or None on timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @staticmethod
    def list_devices() -> List[str]:
        devs = sd.query_devices()
        out = []
        for i, d in enumerate(devs):
            if d.get("max_input_channels", 0) > 0:
                out.append(f"[{i}] {d['name']}")
        return out

    def __enter__(self) -> "VoiceListener":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
