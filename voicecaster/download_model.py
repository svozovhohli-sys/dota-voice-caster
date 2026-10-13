"""Download and unpack a Vosk speech model.

Usage:
    python -m voicecaster.download_model ru     # Russian small (~45 MB)
    python -m voicecaster.download_model en     # English small (~40 MB)
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import requests

_HF = "https://huggingface.co/localstack/vosk-models/resolve/main"

# name -> list of mirror URLs, tried in order (reliable HF mirror first)
MODELS = {
    "ru": ("vosk-model-small-ru-0.22", [
        f"{_HF}/vosk-model-small-ru-0.22.zip",
        "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
    ]),
    "en": ("vosk-model-small-en-us-0.15", [
        f"{_HF}/vosk-model-small-en-us-0.15.zip",
        "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
    ]),
    "ru-big": ("vosk-model-ru-0.42", [
        "https://alphacephei.com/vosk/models/vosk-model-ru-0.42.zip",
    ]),
    "en-big": ("vosk-model-en-us-0.22", [
        "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
    ]),
}


_READ_TIMEOUT = 30        # sec without data before we give up on an attempt
_MAX_ATTEMPTS = 20        # resume attempts per mirror


def _head_total(url: str) -> int:
    r = requests.head(url, allow_redirects=True, timeout=(15, _READ_TIMEOUT))
    r.raise_for_status()
    return int(r.headers.get("content-length", 0))


def _download_resumable(url: str, archive: Path) -> None:
    """Download ``url`` to ``archive``, resuming with HTTP ``Range`` on failure.

    Whatever bytes already arrived are kept, and the rest is re-requested with
    a ``Range`` header. This survives flaky or slow connections (and lets a
    large model download finish after an interruption).
    """
    total = _head_total(url)
    attempts = 0
    while True:
        done = archive.stat().st_size if archive.exists() else 0
        if total and done >= total:
            break
        if attempts >= _MAX_ATTEMPTS:
            raise requests.RequestException(
                f"получено {done} из {total or '?'} байт "
                f"после {attempts} попыток")
        attempts += 1
        headers = {"Range": f"bytes={done}-"} if done else {}
        try:
            with requests.get(url, headers=headers, stream=True,
                              timeout=(15, _READ_TIMEOUT)) as r:
                if r.status_code == 416 and total:  # already have it all
                    break
                if r.status_code == 200 and done:
                    done = 0                      # server ignored Range
                    mode = "wb"
                elif r.status_code in (200, 206):
                    mode = "ab" if done else "wb"
                else:
                    r.raise_for_status()
                last = done
                with open(archive, mode) as fh:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if total and done * 100 // total != last * 100 // total:
                            sys.stdout.write(
                                f"\r  {done * 100 // total:3d}%  "
                                f"({done >> 20} / {total >> 20} MB)")
                            sys.stdout.flush()
                        last = done
        except requests.RequestException as exc:
            if total:
                sys.stdout.write(
                    f"\r  ...обрыв на {done >> 20} MB, продолжу"
                    f" ({type(exc).__name__})\n")
                sys.stdout.flush()
            if not total:
                raise
            continue
        if not total:
            break
    if total:
        sys.stdout.write("\n")
    if total and archive.stat().st_size != total:
        raise requests.RequestException(
            f"получено {archive.stat().st_size} из {total} байт (обрыв)")
    # Integrity check: a truncated download is not a valid zip.
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise requests.RequestException("повреждённый архив")


def download(lang: str, dest_dir: str | Path = "models") -> Path:
    if lang not in MODELS:
        raise SystemExit(f"Неизвестный язык {lang!r}. Доступно: {', '.join(MODELS)}")
    name, urls = MODELS[lang]
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / name
    if target.exists():
        print(f"Модель уже есть: {target}")
        return target

    archive = dest / f"{name}.zip"
    last_error: Exception | None = None
    for url in urls:
        print(f"Скачиваю {url}")
        try:
            _download_resumable(url, archive)
            break
        except (requests.RequestException, zipfile.BadZipFile) as exc:
            last_error = exc
            archive.unlink(missing_ok=True)
            print(f"  не удалось ({exc}); пробую зеркало...")
    else:
        raise SystemExit(
            f"Не удалось скачать модель ни с одного зеркала: {last_error}\n"
            f"Скачайте вручную: {urls[0]} и распакуйте в {dest}/"
        )

    print("\nРаспаковываю...")
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    archive.unlink(missing_ok=True)
    print(f"Готово: {target}")
    return target


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
    download(lang)
