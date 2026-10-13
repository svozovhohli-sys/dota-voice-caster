"""Voice command configuration: spoken phrase -> key macro.

Each command has three kinds of strings:

* ``phrases`` — what the user *says* (may contain words the speech model does
  not know). Used for matching after recognition.
* ``words``   — the actual entries put into the recognizer grammar. These MUST
  exist in the model's vocabulary. If omitted, ``phrases`` is used.
* ``keys``    — the keyboard macro that gets sent (``spell`` builds it too).
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .macros import invoker_macro

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize(text: str) -> str:
    text = text.strip().lower().replace("ё", "е")
    text = _PUNCT.sub("", text)
    return re.sub(r"\s+", " ", text)


def _norm_list(items) -> List[str]:
    return [normalize(x) for x in items if str(x).strip()]


@dataclass
class Command:
    name: str
    phrases: List[str]
    words: List[str]
    keys: List[str] = field(default_factory=list)
    steps: Optional[List[dict]] = None
    cooldown: Optional[float] = None
    slot: str = "d"
    spell: Optional[str] = None
    combo: Optional[List[str]] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Command":
        phrases = _norm_list(d.get("phrases", []))
        words = _norm_list(d.get("words", []))
        slot = d.get("slot", "d")
        spell = d.get("spell")
        combo = d.get("combo")
        steps = d.get("steps")
        if not phrases and words:
            phrases = list(words)
        if steps:
            keys = [k for st in steps if isinstance(st, dict)
                    for k in st.get("keys", [])]
        elif combo:
            steps = [{"keys": invoker_macro(sp, slot)} for sp in combo]
            keys = [k for st in steps for k in st["keys"]]
        elif "keys" in d:
            keys = list(d["keys"])
        elif spell:
            keys = invoker_macro(spell, slot)
        else:
            raise ValueError(
                f"команда {d!r} не содержит 'keys', 'spell', 'combo' или 'steps'")
        if not words:
            words = list(phrases)
        name = d.get("name") or (phrases[0] if phrases else "?")
        return cls(
            name=name,
            phrases=phrases,
            words=words,
            keys=keys,
            steps=steps,
            cooldown=d.get("cooldown"),
            slot=slot,
            spell=spell,
            combo=combo,
        )

    def set_slot(self, slot: str) -> None:
        self.slot = slot
        if self.combo:
            self.steps = [{"keys": invoker_macro(sp, slot)} for sp in self.combo]
            self.keys = [k for st in self.steps for k in st["keys"]]
        elif self.spell:
            self.keys = invoker_macro(self.spell, slot)


@dataclass
class Settings:
    model_path: str = "models/vosk-model-small-ru-0.22"
    sample_rate: int = 16000
    device: Optional[int] = None
    cooldown: float = 0.5
    key_delay: float = 0.04
    combo_delay: float = 0.6
    log_file: Optional[str] = "voice_caster.log"
    wake_word: Optional[str] = None
    wake_timeout: float = 4.0
    fuzzy_cutoff: float = 0.82
    # Human-typing challenge required before every skill key press:
    #   "off"    -> disabled
    #   "ssk"    -> type a random N-character password (SSK mode)
    #   "polina" -> press a single random character (Polina mode)
    challenge_mode: str = "off"
    challenge_keys: List[str] = field(
        default_factory=lambda: ["q", "w", "e", "r", "d", "f"])
    ssk_length: int = 8
    challenge_timeout: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = {k: v for k, v in d.items() if k in known}
        # --- migrate legacy password_* settings (renamed to the SSK mode) ---
        if "challenge_mode" not in data and d.get("orb_password"):
            data["challenge_mode"] = "ssk"
        if "ssk_length" not in data and d.get("password_length"):
            data["ssk_length"] = d["password_length"]
        if ("challenge_timeout" not in data
                and d.get("password_timeout") is not None):
            data["challenge_timeout"] = d["password_timeout"]
        if "challenge_keys" not in data:
            legacy = d.get("password_keys")
            if d.get("password_mode") == "cast" and d.get("password_cast_keys"):
                legacy = d.get("password_cast_keys")
            if legacy:
                data["challenge_keys"] = list(legacy)
        return cls(**data)


@dataclass
class CommandSet:
    settings: Settings
    commands: List[Command] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "CommandSet":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        settings = Settings.from_dict(data.get("settings", {}))
        commands = [Command.from_dict(c) for c in data.get("commands", [])]
        return cls(settings=settings, commands=commands)

    def grammar_words(self) -> List[str]:
        words: List[str] = []
        for cmd in self.commands:
            words.extend(cmd.words)
        if self.settings.wake_word:
            words.append(normalize(self.settings.wake_word))
        # de-duplicate, keep order
        seen = set()
        out = []
        for w in words:
            if w not in seen:
                seen.add(w)
                out.append(w)
        return out

    def resolve(self, text: str) -> Optional[Command]:
        norm = normalize(text)
        if not norm:
            return None
        table: Dict[str, Command] = {}
        for cmd in self.commands:
            for ph in {*cmd.phrases, *cmd.words}:
                table[ph] = cmd
        if norm in table:
            return table[norm]
        match = difflib.get_close_matches(
            norm, list(table.keys()), n=1, cutoff=self.settings.fuzzy_cutoff
        )
        return table[match[0]] if match else None


# --- default configuration (see README for the full spell table) --------------
#
# NOTE: the speech model (vosk-model-small-ru) does not know some of the
# "fun" words, so each command carries `words` that sound like the phrase and
# ARE in the model vocabulary. Vosk "snaps" the spoken phrase to the closest
# grammar word. The mapping below was verified with a neural TTS voice.

DEFAULT_CONFIG: dict = {
    "settings": {
        "model_path": "models/vosk-model-small-ru-0.22",
        "sample_rate": 16000,
        "device": None,
        "cooldown": 0.6,
        "key_delay": 0.04,
        "combo_delay": 0.6,
        "log_file": "voice_caster.log",
        "wake_word": "джарвис",
        "wake_timeout": 4.0,
        "fuzzy_cutoff": 0.82,
        "challenge_mode": "off",
        "challenge_keys": ["q", "w", "e", "r", "d", "f"],
        "ssk_length": 8,
        "challenge_timeout": None,
    },
    "commands": [
        {"name": "Cold Snap", "spell": "cold_snap",
         "phrases": ["заморозься"], "words": ["заморозки"]},
        {"name": "Ghost Walk", "spell": "ghost_walk",
         "phrases": ["призрачная походка"]},
        {"name": "Ice Wall", "spell": "ice_wall",
         "phrases": ["стенка"]},
        {"name": "Tornado", "spell": "tornado",
         "phrases": ["взлетите"], "words": ["взлететь"]},
        {"name": "EMP", "spell": "emp",
         "phrases": ["емп"], "words": ["эм"]},
        {"name": "Alacrity", "spell": "alacrity",
         "phrases": ["алакрити"], "words": ["критика"]},
        {"name": "Forge Spirit", "spell": "forge_spirit",
         "phrases": ["друны ко мне", "друны"], "words": ["дроны ко мне"]},
        {"name": "Chaos Meteor", "spell": "chaos_meteor",
         "phrases": ["великий тефтель", "тефтель"],
         "words": ["великий тефтели"]},
        {"name": "Sun Strike", "spell": "sun_strike",
         "phrases": ["солнечный удар"]},
        {"name": "Deafening Blast", "spell": "deafening_blast",
         "phrases": ["бласт"]},
        {"name": "Meteor Blast", "phrases": ["метеор бласт"],
         "combo": ["chaos_meteor", "deafening_blast"]},
        {"name": "Alacrity Ghost", "phrases": ["алакрити походка"],
         "words": ["критика призрачная походка"],
         "combo": ["alacrity", "ghost_walk"]},
    ],
}


def write_default_config(path: str | Path, force: bool = False) -> bool:
    p = Path(path)
    if p.exists() and not force:
        return False
    p.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def set_setting(path: str | Path, key: str, value) -> None:
    """Update a single setting in the config file, preserving the rest."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() \
        else json.loads(json.dumps(DEFAULT_CONFIG))
    data.setdefault("settings", {})[key] = value
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
