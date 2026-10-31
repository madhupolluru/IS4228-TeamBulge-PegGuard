# Market data for the Live Monitor, with an offline fallback so the app works without internet.
# Each dataset tries: live DefiLlama API (no key) -> last good fetch in data/cache/ -> snapshot in data/snapshots/.

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
PRICE_CHART_URL = "https://coins.llama.fi/chart/{coins}"
USER_AGENT = {"User-Agent": "PegGuard/1.0 (NUS IS4228 student project)"}

# Coins shown in the price-history chart: symbol -> CoinGecko id (used by DefiLlama's price API)
TRACKED = {"USDT": "tether", "USDC": "usd-coin", "DAI": "dai", "USDe": "ethena-usde",
           "PYUSD": "paypal-usd", "FDUSD": "first-digital-usd"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get_json(url: str, timeout: float = 8.0) -> dict:
    if requests is None:
        raise RuntimeError("requests is not installed")
    r = requests.get(url, timeout=timeout, headers=USER_AGENT)
    r.raise_for_status()
    return r.json()


def _write_cache(name: str, payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_DIR / name, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _read_json(path: Path) -> dict | None:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


# Stablecoin supply table

def _num(x) -> float | None:
    # DefiLlama nests amounts as {"peggedUSD": 123.4}; accept either shape.
    if isinstance(x, dict):
        x = x.get("peggedUSD")
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def parse_stablecoins(payload: dict) -> pd.DataFrame:
    rows = []
    for a in payload.get("peggedAssets", []):
        if a.get("pegType", "peggedUSD") != "peggedUSD":
            continue
        rows.append(
            {
                "name": a.get("name"),
                "symbol": a.get("symbol"),
                "gecko_id": a.get("gecko_id"),
                "mechanism": a.get("pegMechanism", ""),
                "supply_usd": _num(a.get("circulating")),
                "prev_day_usd": _num(a.get("circulatingPrevDay")),
                "prev_week_usd": _num(a.get("circulatingPrevWeek")),
                "prev_month_usd": _num(a.get("circulatingPrevMonth")),
                "price": _num(a.get("price")),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.dropna(subset=["supply_usd"]).sort_values("supply_usd", ascending=False).reset_index(drop=True)
    df["peg_gap_bps"] = (df["price"] - 1.0) * 10_000
    df["chg_7d_pct"] = (df["supply_usd"] / df["prev_week_usd"] - 1) * 100
    df["chg_30d_pct"] = (df["supply_usd"] / df["prev_month_usd"] - 1) * 100
    return df


def load_stablecoins(live: bool = True) -> tuple[pd.DataFrame, dict]:
    # Return (table, meta). meta = {"mode": live|cache|snapshot, "as_of": str, "note": str}.
    if live:
        try:
            payload = _get_json(STABLECOINS_URL)
            payload["_fetched_at"] = _now_iso()
            df = parse_stablecoins(payload)
            if not df.empty:
                _write_cache("stablecoins.json", payload)
                return df, {"mode": "live", "as_of": payload["_fetched_at"], "note": "Live from DefiLlama"}
        except Exception as e:  # network, rate limit, schema change
            live_error = str(e)[:120]
        else:
            live_error = "empty response"
    else:
        live_error = None

    cached = _read_json(CACHE_DIR / "stablecoins.json")
    if cached:
        df = parse_stablecoins(cached)
        if not df.empty:
            note = "Cached copy of the last live fetch"
            if live_error:
                note += f" (live fetch failed: {live_error})"
            return df, {"mode": "cache", "as_of": cached.get("_fetched_at", "unknown"), "note": note}

    snap = _read_json(SNAPSHOT_DIR / "stablecoins.json") or {"peggedAssets": []}
    df = parse_stablecoins(snap)
    note = snap.get("_note", "Bundled snapshot")
    if live_error:
        note += f" (live fetch failed: {live_error})"
    return df, {"mode": "snapshot", "as_of": snap.get("_fetched_at", "unknown"), "note": note}


# Price history

def parse_price_chart(payload: dict) -> pd.DataFrame:
    rows = []
    for key, v in (payload.get("coins") or {}).items():
        sym = v.get("symbol") or key.split(":")[-1]
        for p in v.get("prices", []):
            rows.append({"time": pd.to_datetime(p["timestamp"], unit="s", utc=True), "coin": sym.upper()
                         if sym.lower() != "usde" else "USDe", "price": float(p["price"])})
    return pd.DataFrame(rows)


def load_price_history(days: int = 90, live: bool = True,
                       coins: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict]:
    coins = coins or TRACKED
    ids = ",".join(f"coingecko:{g}" for g in coins.values())
    start = int(time.time()) - days * 86_400
    url = PRICE_CHART_URL.format(coins=ids) + f"?start={start}&span={days}&period=1d&searchWidth=600"
    err = None
    if live:
        try:
            payload = _get_json(url)
            payload["_fetched_at"] = _now_iso()
            df = parse_price_chart(payload)
            if not df.empty:
                _write_cache("price_history.json", payload)
                return df, {"mode": "live", "as_of": payload["_fetched_at"], "note": "Live from DefiLlama"}
            err = "empty response"
        except Exception as e:
            err = str(e)[:120]
    cached = _read_json(CACHE_DIR / "price_history.json")
    if cached:
        df = parse_price_chart(cached)
        if not df.empty:
            return df, {"mode": "cache", "as_of": cached.get("_fetched_at", "unknown"),
                        "note": "Cached copy of the last live fetch" + (f" (live fetch failed: {err})" if err else "")}
    return pd.DataFrame(columns=["time", "coin", "price"]), {
        "mode": "none", "as_of": "", "note": "No price history available offline yet. Run scripts/refresh_snapshot.py "
                                            "once while online to cache it." + (f" Live fetch failed: {err}" if err else "")}
