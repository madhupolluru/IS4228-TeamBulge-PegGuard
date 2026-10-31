# Load the bundled reserve presets (data/presets.json).

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .reserves import Portfolio, ReserveLine

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRESETS_PATH = DATA_DIR / "presets.json"


@lru_cache(maxsize=1)
def _raw() -> list[dict]:
    with open(PRESETS_PATH, encoding="utf-8") as f:
        return json.load(f)["presets"]


def preset_ids() -> list[str]:
    return [p["id"] for p in _raw()]


def preset_names() -> dict[str, str]:
    return {p["id"]: p["name"] for p in _raw()}


def preset_info() -> list[dict]:
    # Metadata for every preset (no reserve lines): id, name, group ('issuer' or 'benchmark'), symbol, as_of, basis.
    return [{"id": p["id"], "name": p["name"], "group": p.get("group", "benchmark"), "symbol": p.get("symbol"),
             "as_of": p.get("as_of", ""), "basis": p.get("basis", "hypothetical")} for p in _raw()]


def load_preset(preset_id: str) -> Portfolio:
    for p in _raw():
        if p["id"] == preset_id:
            return Portfolio(
                name=p["name"],
                supply_bn=float(p["supply_bn"]),
                lines=[ReserveLine(**l) for l in p["lines"]],
                description=p.get("description", ""),
                source=p.get("source", ""),
                as_of=p.get("as_of", ""),
                basis=p.get("basis", "hypothetical"),
            )
    raise KeyError(f"Unknown preset: {preset_id}")


def all_presets() -> list[Portfolio]:
    return [load_preset(i) for i in preset_ids()]


DEFAULT_TERMS = {"direct": True, "fee_bp": 0.0, "min_m": 0.0, "note": "No published terms; free redemption assumed."}


def redemption_terms(preset_id: str) -> dict:
    # How a holder can redeem this coin with the issuer: {"direct", "fee_bp", "min_m", "note"}.
    # `direct` is whether an institutional treasury can open a redemption account at all; `min_m` is the
    # smallest redemption in $m. Presets without published terms get free, unrestricted redemption.
    for p in _raw():
        if p["id"] == preset_id:
            return {**DEFAULT_TERMS, **p.get("redemption", {})}
    raise KeyError(f"Unknown preset: {preset_id}")
