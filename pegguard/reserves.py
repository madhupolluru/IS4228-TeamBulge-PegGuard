# Reserve asset classes and the portfolio data model.
# Each asset class holds the assumptions the engine needs: how much can be sold per day, the fire-sale
# discount, market depth and price impact, how rates and price shocks hit it, which regulators accept it
# and where it sits in the selling order. All of them can be edited in the app.

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class AssetClass:
    key: str
    label: str
    daily_liquidity: float
    fire_sale_bps: float
    rate_kind: str  # none | zero | coupon | mmf
    shock_key: str | None
    genius: bool
    mas: bool
    rank: int
    default_maturity: float
    default_yield: float
    color: str
    note: str = ""
    market_depth_bn: float = math.inf  # inf = no price impact (cash, overnight repo)
    impact_bps: float = 0.0
    mica: bool = False
    hk: bool = False


ASSET_CLASSES: dict[str, AssetClass] = {
    a.key: a
    for a in [
        AssetClass("cash_bank", "Cash at commercial banks", 1.00, 0, "none", None, True, True, 1, 0.0, 0.0,
                   "#0F766E", "Instantly available unless the bank fails (see bank-failure shock).",
                   mica=True, hk=True),
        AssetClass("repo", "Reverse repo (Treasury-collateralised)", 1.00, 0, "none", None, True, True, 2, 0.003, 4.0,
                   "#14B8A6", "Overnight repo matures every day, so it converts to cash at par.", mica=True, hk=True),
        AssetClass("mmf_gov", "Government money market fund", 0.60, 5, "mmf", None, True, True, 3, 0.08, 4.0,
                   "#2563EB", "Stable-NAV fund holding bills and repo. Large redemptions force the fund to sell bills.",
                   market_depth_bn=100.0, impact_bps=10, mica=True, hk=True),
        AssetClass("tbill", "US Treasury bills", 0.50, 10, "zero", None, True, True, 4, 0.25, 4.0,
                   "#60A5FA", "Deepest market in the world, but very large same-day sales still move the price.",
                   market_depth_bn=150.0, impact_bps=25, mica=True, hk=True),
        AssetClass("tnote", "Treasury notes / bonds (> 1y)", 0.25, 50, "coupon", None, False, False, 5, 5.0, 4.0,
                   "#7C3AED", "Rate-sensitive. This is where an SVB-style duration loss comes from.",
                   market_depth_bn=300.0, impact_bps=100),
        AssetClass("agency_mbs", "Agency mortgage-backed securities", 0.15, 75, "coupon", None, False, False, 6,
                   6.0, 2.0, "#8B5CF6", "Long and rate-sensitive; most of SVB's held-to-maturity book.",
                   market_depth_bn=50.0, impact_bps=150),
        AssetClass("commercial_paper", "Commercial paper / corporate debt", 0.15, 75, "zero", None, False, False, 7,
                   0.25, 4.0, "#A855F7", "Short corporate IOUs. The secondary market dries up in a panic (March 2020).",
                   market_depth_bn=10.0, impact_bps=200),
        AssetClass("gold", "Gold", 0.20, 100, "none", "gold", False, False, 8, 0.0, 0.0,
                   "#D97706", "Price risk plus a wider bid-ask in stress.", market_depth_bn=50.0, impact_bps=200),
        AssetClass("crypto", "Bitcoin / crypto", 0.20, 300, "none", "crypto", False, False, 9, 0.0, 0.0,
                   "#EA580C", "Volatile, and falls hardest exactly when stablecoin runs happen.",
                   market_depth_bn=5.0, impact_bps=1000),
        AssetClass("other", "Other investments / equities", 0.10, 500, "none", "risk", False, False, 10, 0.0, 0.0,
                   "#DB2777", "Hard to value and slow to sell.", market_depth_bn=5.0, impact_bps=1000),
        AssetClass("secured_loan", "Secured loans", 0.02, 1000, "none", "credit", False, False, 11, 0.0, 0.0,
                   "#DC2626", "Effectively locked up: can only be recalled or sold at a steep discount.",
                   market_depth_bn=1.0, impact_bps=2000),
    ]
}

LABEL_TO_KEY = {a.label: a.key for a in ASSET_CLASSES.values()}


@dataclass
class ReserveLine:
    name: str
    asset_class: str
    amount_bn: float
    maturity_years: float = 0.0
    yield_pct: float = 0.0
    bank: str = ""  # bank or custodian holding this line (lets a bank failure hit one bank only)
    # Optional per-line overrides (None means use the asset-class default)
    daily_liquidity: float | None = None
    fire_sale_bps: float | None = None

    @property
    def spec(self) -> AssetClass:
        return ASSET_CLASSES[self.asset_class]

    @property
    def liquidity(self) -> float:
        return self.spec.daily_liquidity if self.daily_liquidity is None else float(self.daily_liquidity)

    @property
    def fire_sale(self) -> float:
        bps = self.spec.fire_sale_bps if self.fire_sale_bps is None else float(self.fire_sale_bps)
        return bps / 10_000.0


@dataclass
class Portfolio:
    name: str
    supply_bn: float
    lines: list[ReserveLine] = field(default_factory=list)
    description: str = ""
    source: str = ""
    as_of: str = ""
    basis: str = "hypothetical"  # reported | reconstructed | hypothetical

    @property
    def reserves_bn(self) -> float:
        return float(sum(l.amount_bn for l in self.lines))




EDITOR_COLUMNS = ["Holding", "Asset class", "Amount ($bn)", "Maturity (yrs)", "Yield (%)", "Bank / custodian"]


def lines_to_frame(lines: Iterable[ReserveLine]) -> pd.DataFrame:
    rows = [
        {
            "Holding": l.name,
            "Asset class": ASSET_CLASSES[l.asset_class].label,
            "Amount ($bn)": float(l.amount_bn),
            "Maturity (yrs)": float(l.maturity_years),
            "Yield (%)": float(l.yield_pct),
            "Bank / custodian": l.bank,
        }
        for l in lines
    ]
    return pd.DataFrame(rows, columns=EDITOR_COLUMNS)


def frame_to_lines(df: pd.DataFrame) -> list[ReserveLine]:
    # Convert the app's editable table back into reserve lines, skipping blank or invalid rows.
    out: list[ReserveLine] = []
    for _, r in df.iterrows():
        label = r.get("Asset class")
        key = LABEL_TO_KEY.get(label, label if label in ASSET_CLASSES else None)
        try:
            amt = float(r.get("Amount ($bn)") or 0.0)
        except (TypeError, ValueError):
            amt = 0.0
        if key is None or amt <= 0:
            continue
        spec = ASSET_CLASSES[key]
        mat = r.get("Maturity (yrs)")
        yld = r.get("Yield (%)")
        bank = r.get("Bank / custodian")
        out.append(
            ReserveLine(
                name=str(r.get("Holding") or spec.label),
                asset_class=key,
                amount_bn=amt,
                maturity_years=float(spec.default_maturity if pd.isna(mat) else mat),
                yield_pct=float(spec.default_yield if pd.isna(yld) else yld),
                bank="" if bank is None or pd.isna(bank) else str(bank).strip(),
            )
        )
    return out


def composition(lines: Iterable[ReserveLine]) -> pd.DataFrame:
    # Reserve mix by asset class (amount and share).
    lines = list(lines)
    total = sum(l.amount_bn for l in lines) or 1.0
    agg: dict[str, float] = {}
    for l in lines:
        agg[l.asset_class] = agg.get(l.asset_class, 0.0) + l.amount_bn
    rows = [
        {
            "key": k,
            "Asset class": ASSET_CLASSES[k].label,
            "Amount ($bn)": v,
            "Share": v / total,
            "color": ASSET_CLASSES[k].color,
        }
        for k, v in sorted(agg.items(), key=lambda kv: ASSET_CLASSES[kv[0]].rank)
    ]
    return pd.DataFrame(rows)
