#!/usr/bin/env python3
"""dota-voice-caster — голосовое управление Инвокером.

Примеры:
    python main.py --init                 # создать commands.json с настройками
    python main.py --download-model ru    # скачать оффлайн-модель речи
    python main.py --list-devices         # список микрофонов
    python main.py --dry-run              # показать срабатывания без отправки клавиш
    python main.py                        # боевой режим
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from voicecaster.commands import (
    Command, CommandSet, set_setting, write_default_config,
)

STEP_TYPES = ("keys", "move", "click", "sleep")


def log_event(path, line: str) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
from voicecaster.download_model import download
from voicecaster.input_emulator import InputError, KeyboardEmulator
from voicecaster.listener import VoiceListener
from voicecaster.challenge import ChallengeGate


class DryRunEmulator:
    """Печатает клавиши вместо реальной отправки (для отладки)."""

    backend_name = "dry-run"

    def tap(self, key: str) -> None:
        print(f"    + клавиша: {key}")

    def key_down(self, key: str) -> None:
        print(f"    + key_down: {key}")

    def key_up(self, key: str) -> None:
        print(f"    + key_up: {key}")

    def move_cursor(self, dx: int, dy: int) -> None:
        print(f"    + мышь: +({dx}, {dy})")

    def click(self) -> None:
        print("    + клик ЛКМ")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Голосовое управление спеллами Инвокера (Windows / Linux)."
    )
    p.add_argument("--config", default="commands.json", help="путь к конфигу")
    p.add_argument("--init", action="store_true",
                   help="создать конфиг со значениями по умолчанию и выйти")
    p.add_argument("--download-model", metavar="LANG",
                   help="скачать модель речи (ru, en, ru-big, en-big) и выйти")
    p.add_argument("--list-devices", action="store_true",
                   help="показать доступные микрофоны и выйти")
    p.add_argument("--check", action="store_true",
                   help="проверить слова команд на наличие в модели и выйти")
    p.add_argument("--calibrate", action="store_true",
                   help="проверить распознавание вашим голосом и выйти")
    p.add_argument("--model", help="переопределить путь к модели речи (разово)")
    p.add_argument("--set-model", metavar="PATH",
                   help="записать путь к модели в конфиг и выйти")
    p.add_argument("--device", type=int, help="индекс микрофона (см. --list-devices)")
    p.add_argument("--slot", choices=["d", "f"], help="переопределить слот каста")
    p.add_argument("--dry-run", action="store_true",
                   help="не отправлять клавиши, только печатать")
    p.add_argument("--test-keys", action="store_true",
                   help="проверить эмуляцию: отправить q w e r d")
    p.add_argument("--ssk-mode", action="store_true",
                   help="SSK mode: ввести пароль перед каждым нажатием скилла")
    p.add_argument("--polina-mode", action="store_true",
                   help="Polina mode: нажать один случайный символ перед скиллом")
    p.add_argument("--challenge-keys", metavar="KEYS",
                   help="какие клавиши считаются скиллами (напр. qwerdf)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="показывать промежуточные распознавания")
    return p


def cmd_list_devices() -> None:
    for line in VoiceListener.list_devices():
        print(line)


def resolve_model(config_path: Path, model_path: str) -> Path:
    p = Path(model_path)
    if not p.is_absolute():
        p = (config_path.resolve().parent / p).resolve()
    return p


def cmd_calibrate(config_path: Path, device: int | None) -> int:
    from voicecaster.calibrate import run as run_calibration

    if not config_path.exists():
        write_default_config(config_path)
    cmdset = CommandSet.load(config_path)
    if device is not None:
        cmdset.settings.device = device
    model_path = resolve_model(config_path, cmdset.settings.model_path)
    if not model_path.exists():
        print(f"Модель речи не найдена: {model_path}\n"
              f"Скачайте: python main.py --download-model ru", file=sys.stderr)
        return 2
    return run_calibration(cmdset, str(model_path), cmdset.settings.device)


def cmd_check(config_path: Path) -> int:
    if not config_path.exists():
        write_default_config(config_path)
    cmdset = CommandSet.load(config_path)
    model_path = resolve_model(config_path, cmdset.settings.model_path)
    if not model_path.exists():
        print(f"Модель речи не найдена: {model_path}", file=sys.stderr)
        return 2
    grammar = cmdset.grammar_words()
    listener = VoiceListener(model_path=str(model_path), grammar=grammar)
    missing = listener.missing_words(grammar)
    print(f"Команд: {len(cmdset.commands)}")
    print(f"Слов в грамматике: {len(grammar)}")
    if missing:
        print("НЕТ в словаре модели:")
        for w in missing:
            print(f"  - {w}")
    else:
        print("Все слова есть в словаре модели.")
    print("\nКарта команд:")
    for c in cmdset.commands:
        say = " / ".join(f"«{p}»" for p in c.phrases) or "—"
        if c.steps:
            parts = []
            for st in c.steps:
                if "sleep" in st:
                    parts.append(f"~{st['sleep']:g}с")
                elif "move" in st:
                    dx, dy = st["move"]
                    parts.append(f"↑+{dx},+{dy}")
                elif "click" in st:
                    parts.append("КЛИК")
                else:
                    parts.append(" ".join(st.get("keys", [])).upper())
            action = " + ".join(parts)
        else:
            action = " ".join(c.keys).upper()
        extra = ""
        if {*c.words} != {*c.phrases}:
            extra = f"   (распознаётся как: {', '.join(c.words)})"
        print(f"  {c.name:16} {say:32} -> {action}{extra}")
    return 0


def cmd_test_keys() -> None:
    emu = KeyboardEmulator()
    print(f"Бэкенд ввода: {emu.backend_name}")
    print("Через 3 секунды отправлю q w e r d — переключитесь в блокнот/Dota...")
    for i in range(3, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    for key in ("q", "w", "e", "r", "d"):
        emu.tap(key)
        time.sleep(0.15)
    print("Готово.")


def run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        write_default_config(config_path)
        print(f"Создан конфиг по умолчанию: {config_path}")

    cmdset = CommandSet.load(config_path)
    settings = cmdset.settings
    if args.model:
        settings.model_path = args.model
    if args.device is not None:
        settings.device = args.device
    if args.slot:
        for c in cmdset.commands:
            c.set_slot(args.slot)

    model_path = resolve_model(config_path, settings.model_path)
    if not model_path.exists():
        print(f"Модель речи не найдена: {model_path}\n"
              f"Скачайте её: python main.py --download-model ru", file=sys.stderr)
        return 2

    emu = DryRunEmulator() if args.dry_run else KeyboardEmulator()
    print(f"Бэкенд ввода: {emu.backend_name}")

    if args.challenge_keys:
        settings.challenge_keys = list(args.challenge_keys.lower())

    mode = None
    if args.ssk_mode:
        mode = "ssk"
    elif args.polina_mode:
        mode = "polina"
    elif settings.challenge_mode not in (None, "", "off"):
        mode = settings.challenge_mode

    gate = None
    if mode:
        gate = ChallengeGate(
            mode=mode,
            keys=settings.challenge_keys,
            length=settings.ssk_length,
            timeout=settings.challenge_timeout,
        )
        gate.start()
        keys = " ".join(k.upper() for k in settings.challenge_keys)
        if mode == "polina":
            print(f"Polina mode: 1 случайный символ перед каждой из: {keys}")
        else:
            print(f"SSK mode: пароль из {settings.ssk_length} символов "
                  f"перед каждой из: {keys}")

    log_path = None
    if settings.log_file:
        lp = Path(settings.log_file)
        log_path = str(lp if lp.is_absolute()
                      else (config_path.resolve().parent / lp))

    last_fired: dict[str, float] = {}
    fired_count: dict[str, int] = {}
    no_cmd = 0

    def fire(cmd: Command) -> None:
        now = time.monotonic()
        cd = cmd.cooldown if cmd.cooldown is not None else settings.cooldown
        if now - last_fired.get(cmd.name, 0.0) < cd:
            return
        last_fired[cmd.name] = now
        fired_count[cmd.name] = fired_count.get(cmd.name, 0) + 1
        steps = cmd.steps if cmd.steps is not None else [{"keys": cmd.keys}]
        desc = " + ".join(
            " ".join(st.get("keys", [])).upper()
            for st in steps if "keys" in st) or " ".join(cmd.keys).upper()
        print(f"  -> {cmd.name}: {desc}")

        def send_key(key: str) -> None:
            if gate is not None:
                if gate.prompt_for(key) is False:
                    print(f"  ! челлендж для {key.upper()} не пройден")
                    return
            emu.tap(key)
            time.sleep(settings.key_delay)

        for i, st in enumerate(steps):
            if i and "sleep" not in st:
                time.sleep(settings.combo_delay)
            if "sleep" in st:
                time.sleep(float(st["sleep"]))
            elif "move" in st:
                dx, dy = st["move"]
                emu.move_cursor(dx, dy)
            elif "click" in st:
                for _ in range(int(st["click"])):
                    emu.click()
            else:
                for key in st.get("keys", []):
                    send_key(key)
        log_event(log_path,
                  f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {cmd.name}"
                  f"  keys={desc}")

    grammar = cmdset.grammar_words()
    listener = VoiceListener(
        model_path=str(model_path),
        grammar=grammar,
        sample_rate=settings.sample_rate,
        device=settings.device,
    )

    missing = listener.missing_words(grammar)
    if missing:
        print("ВНИМАНИЕ: этих слов нет в словаре модели, команды не сработают:")
        print("  " + ", ".join(missing))
        print("  Используйте близкое слово в поле 'words' команды.\n")

    listener.start()
    print(f"Слушаю микрофон ({len(cmdset.commands)} команд). Ctrl+C для выхода.")
    if settings.wake_word:
        print(f"Режим wake-word: скажите «{settings.wake_word}», затем команду "
              f"(в течение {settings.wake_timeout:g} с).")

    wake = settings.wake_word.lower() if settings.wake_word else None
    wake_until = 0.0
    last_partial = ""

    try:
        while True:
            rec = listener.poll(timeout=0.1)
            if rec is None:
                continue
            text = rec.text.strip()
            if not text:
                continue

            if not rec.is_final:
                if args.verbose and text != last_partial:
                    print(f"  ...{text}")
                    last_partial = text
                continue

            print(f"[heard] {text}")
            if wake:
                if wake in text:
                    wake_until = time.monotonic() + settings.wake_timeout
                    text = text.replace(wake, "").strip()
                    if not text:
                        continue
                elif time.monotonic() > wake_until:
                    continue
            cmd = cmdset.resolve(text)
            if cmd is None:
                no_cmd += 1
                print("  (нет команды)")
                continue
            fire(cmd)
    except KeyboardInterrupt:
        print("\nВыход.")
    finally:
        listener.stop()
        if gate is not None:
            gate.stop()
    if fired_count or no_cmd:
        print("\n=== Статистика за сессию ===")
        for name in sorted(fired_count):
            print(f"  {name:26} {fired_count[name]}")
        if no_cmd:
            print(f"  (не распознано команд: {no_cmd})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.download_model:
        download(args.download_model)
        return 0
    if args.init:
        created = write_default_config(Path(args.config))
        print("Создан" if created else "Уже существует", args.config)
        return 0
    if args.set_model:
        path = Path(args.config)
        if not path.exists():
            write_default_config(path)
        set_setting(path, "model_path", args.set_model)
        print(f"Модель в конфиге: {args.set_model}")
        return 0
    if args.list_devices:
        cmd_list_devices()
        return 0
    if args.check:
        return cmd_check(Path(args.config))
    if args.calibrate:
        return cmd_calibrate(Path(args.config), args.device)
    if args.test_keys:
        try:
            cmd_test_keys()
        except InputError as exc:
            print(f"Ошибка: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        return run(args)
    except InputError as exc:
        print(f"Ошибка ввода: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
