"""Invoker spell presets and macro playback.

Orbs:  Quas = Q, Wex = W, Exort = E.  Invoke = R.  Cast slots = D / F.

A *macro* is just an ordered list of keys, e.g. QQE + R + D = Chaos Meteor.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

ORB_KEYS = {"quas": "q", "wex": "w", "exort": "e"}

# spell key -> (orb combo, human readable name)
INVOKER_SPELLS: Dict[str, Tuple[Tuple[str, str, str], str]] = {
    "cold_snap": (("quas", "quas", "quas"), "Cold Snap / QQQ"),
    "ghost_walk": (("quas", "quas", "wex"), "Ghost Walk / QQW"),
    "ice_wall": (("quas", "quas", "exort"), "Ice Wall / QQE"),
    "emp": (("wex", "wex", "wex"), "EMP / WWW"),
    "tornado": (("wex", "wex", "quas"), "Tornado / WWQ"),
    "alacrity": (("wex", "wex", "exort"), "Alacrity / WWE"),
    "sun_strike": (("exort", "exort", "exort"), "Sun Strike / EEE"),
    "forge_spirit": (("exort", "exort", "quas"), "Forge Spirit / EEQ"),
    "chaos_meteor": (("exort", "exort", "wex"), "Chaos Meteor / EEW"),
    "deafening_blast": (("quas", "wex", "exort"), "Deafening Blast / QWE"),
}


def invoker_macro(spell: str, slot: str = "d") -> List[str]:
    """Keys to invoke + cast ``spell`` on ``slot`` ('d' or 'f')."""
    if spell not in INVOKER_SPELLS:
        raise KeyError(f"unknown spell: {spell!r}")
    orbs = INVOKER_SPELLS[spell][0]
    return [ORB_KEYS[o] for o in orbs] + ["r", slot.lower()]
