# Shared state, sidebar, styling and formatting for every page.

from __future__ import annotations

import html
import re
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pegguard.analysis import STANDARD_SCENARIOS  # noqa: E402
from pegguard.charts import VERDICT_COLORS  # noqa: E402
from pegguard.engine import Scenario  # noqa: E402
from pegguard.presets import load_preset, preset_ids, preset_names, redemption_terms  # noqa: E402
from pegguard.reserves import ASSET_CLASSES, EDITOR_COLUMNS, frame_to_lines, lines_to_frame  # noqa: E402

DEFAULT_PRESET = "usdt_2026q2"
DEFAULT_POSITION_M = 30.0  # $m of the token held; drives every P&L figure in the app

# Live Monitor alert thresholds
PEG_ALERT_BP = 25.0  # price this far from $1 is flagged
SUPPLY_ALERT_PCT = -5.0  # supply falling this much in 7 days is flagged

# Scenario widgets: session key -> default
SC_DEFAULTS = {
    "sc_rate": 200.0, "sc_run": 30.0, "sc_days": 5, "sc_front": 0.35,
    "sc_freeze": 0.0, "sc_recovery": 100.0, "sc_release": 0, "sc_stress": 2.0,
    "sc_gold": 0.0, "sc_crypto": 0.0, "sc_risk": 0.0, "sc_loan": 0.0, "sc_panic": 0.0,
    "sc_wait": 0,
    # advanced: market structure, timing and behaviour
    "sc_twist": 0.0, "sc_closed": 0, "sc_failed_bank": "", "sc_impact": 1.0, "sc_spill": 0.5,
    "sc_signal": "coverage", "sc_lag": 0, "sc_mkt_return": 20.0, "sc_mkt_recovery": 0.0, "sc_fear": 0.0,
}
# Slider key -> Scenario field (one table drives loading a crisis, reading the sliders and share links)
SC_FIELDS = {
    "sc_rate": "rate_shock_bp", "sc_run": "redemption_pct", "sc_days": "horizon_days", "sc_front": "front_load",
    "sc_freeze": "bank_freeze_pct", "sc_recovery": "frozen_recovery_pct", "sc_release": "frozen_release_day",
    "sc_stress": "stress_multiplier", "sc_gold": "gold_shock_pct", "sc_crypto": "crypto_shock_pct",
    "sc_risk": "risk_shock_pct", "sc_loan": "loan_loss_pct", "sc_panic": "panic_sensitivity",
    "sc_wait": "max_wait_days", "sc_twist": "curve_twist_bp", "sc_closed": "closed_days",
    "sc_failed_bank": "failed_bank", "sc_impact": "price_impact", "sc_spill": "spillover",
    "sc_signal": "panic_signal", "sc_lag": "panic_lag_days", "sc_mkt_return": "market_required_return_pct",
    "sc_mkt_recovery": "market_frozen_recovery_pct", "sc_fear": "fear_premium_bp",
}
# The redemption deadline is a policy, not part of a crisis: loading a crisis leaves it alone.
WAIT_OPTIONS = {0: "Same day", 1: "T+1", 2: "T+2", 3: "T+3", 5: "T+5 (MAS)"}

# Data basis of each bundled portfolio, so benchmarks are not mistaken for real reserves
BASIS = {"reported": "Reported", "reconstructed": "Reconstructed", "hypothetical": "Hypothetical"}
BASIS_HELP = {
    "reported": "Built from the issuer's own published reserve report.",
    "reconstructed": "Approximate rebuild of a past portfolio: headline figures are public, the split is our estimate.",
    "hypothetical": "Made up for teaching. It does not describe any real issuer's reserves.",
}

CSS_PATH = ROOT / "assets" / "style.css"


# State

def _load_into_state(preset_id: str) -> None:
    p = load_preset(preset_id)
    st.session_state.reserves_df = lines_to_frame(p.lines)
    st.session_state.supply = float(p.supply_bn)
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
    st.session_state.portfolio_meta = {"name": p.name, "description": p.description, "source": p.source,
                                       "as_of": p.as_of, "basis": p.basis, "edited": False}


def _read_share_link() -> None:
    # Apply a shared scenario from the URL (?coin=...&sc_rate=...) once, on the first load of a session.
    qp = st.query_params
    coin = qp.get("coin")
    if coin in preset_ids():
        st.session_state.preset_id = coin
        _load_into_state(coin)
    for k, default in SC_DEFAULTS.items():
        if k in qp:
            try:
                st.session_state[k] = type(default)(qp[k])
            except (TypeError, ValueError):
                pass  # ignore a malformed value rather than failing to load the app
    if "pos" in qp:
        try:
            st.session_state.position_m = max(0.0, float(qp["pos"]))
        except ValueError:
            pass


def share_query() -> dict:
    # The current coin, scenario and position as URL query parameters (only values that differ from default).
    q = {"coin": st.session_state.preset_id}
    for k, default in SC_DEFAULTS.items():
        v = st.session_state.get(k, default)
        if v != default:
            q[k] = str(v)
    if position_m() != DEFAULT_POSITION_M:
        q["pos"] = f"{position_m():g}"
    return q


@lru_cache(maxsize=1)
def _css() -> str:
    return "<style>" + CSS_PATH.read_text(encoding="utf-8") + "</style>"


def init_state() -> None:
    st.markdown(_css(), unsafe_allow_html=True)
    if "preset_id" not in st.session_state:
        st.session_state.preset_id = DEFAULT_PRESET
        _load_into_state(DEFAULT_PRESET)
        _read_share_link()
    pending = st.session_state.pop("pending_preset", None)
    if pending:  # set by other pages; applied here before the sidebar widget exists
        st.session_state.preset_id = pending
        _load_into_state(pending)
    for k, v in SC_DEFAULTS.items():
        # Re-assigning keeps widget values alive when the user switches pages.
        st.session_state[k] = st.session_state.get(k, v)
    st.session_state.setdefault("live_data", False)
    st.session_state.setdefault("position_m", DEFAULT_POSITION_M)
    st.session_state.setdefault("direct_access", True)
    st.session_state.setdefault("mc_n", 300)


def on_preset_change() -> None:
    _load_into_state(st.session_state.preset_id)


def request_preset(preset_id: str) -> None:
    # Switch portfolio from inside a page (takes effect on the next rerun).
    st.session_state.pending_preset = preset_id


def apply_scenario(sc: Scenario) -> None:
    # Load a crisis into the sliders. The redemption deadline is a policy, so it is left alone.
    for k, f in SC_FIELDS.items():
        if k != "sc_wait":
            st.session_state[k] = type(SC_DEFAULTS[k])(getattr(sc, f))


def _scenario_from(values: dict, name: str) -> Scenario:
    return Scenario(name=name, **{f: type(SC_DEFAULTS[k])(values[k]) for k, f in SC_FIELDS.items()})


def current_scenario(name: str = "Your scenario") -> Scenario:
    return _scenario_from({k: st.session_state.get(k, v) for k, v in SC_DEFAULTS.items()}, name)


def reference_scenario(name: str = "Reference") -> Scenario:
    # The app's default scenario settings (ignores the user's sliders), for like-for-like rankings.
    return _scenario_from(SC_DEFAULTS, name)


def matching_crisis() -> str | None:
    # Name of the standard scenario the sliders currently match (ignoring the deadline), else None.
    cur = current_scenario().to_dict()
    for sc in STANDARD_SCENARIOS:
        ref = sc.to_dict()
        keys = [f for f in SC_FIELDS.values() if f != "max_wait_days"]
        if all(cur[k] == ref[k] if isinstance(ref[k], str) else abs(float(cur[k]) - float(ref[k])) < 1e-9
               for k in keys):
            return sc.name
    return None


def wait_label(days: int) -> str:
    return WAIT_OPTIONS.get(int(days), f"T+{int(days)}")


def basis_badge(basis: str) -> str:
    return f'<span class="pg-basis {basis}" title="{BASIS_HELP.get(basis, "")}">{BASIS.get(basis, basis)}</span>'


def basis_notice(meta: dict) -> None:
    # Warn that a benchmark portfolio is not a record of real reserves.
    basis = meta.get("basis", "hypothetical")
    if basis == "hypothetical":
        st.info("**Hypothetical portfolio for teaching.** It shows a mechanism, not any real issuer's reserves, so "
                "its numbers do not describe what any stablecoin actually held.", icon=":material/school:")
    elif basis == "reconstructed":
        st.warning("**Approximate reconstruction.** The headline figures are public, but the split of the "
                   "reserves is our estimate. Treat results as illustrative, not as what actually happened.",
                   icon=":material/history:")


def position_m() -> float:
    return float(st.session_state.get("position_m", DEFAULT_POSITION_M))


def holder_access(preset_id: str | None = None) -> dict:
    # Redemption terms for the coin, combined with whether the user has a direct account with the issuer.
    pid = preset_id or st.session_state.preset_id
    terms = redemption_terms(pid)
    edited = st.session_state.get("portfolio_meta", {}).get("edited") and pid == st.session_state.preset_id
    return {**terms, "direct": bool(terms["direct"] and st.session_state.get("direct_access", True)),
            "note": terms["note"] + (" Terms are for the original coin; you edited its reserves." if edited else "")}


def current_portfolio():
    lines = frame_to_lines(st.session_state.reserves_df)
    return lines, float(st.session_state.supply)


def portfolio_key() -> tuple:
    # hashable fingerprint of the current portfolio, used as a cache key
    df = st.session_state.reserves_df
    return (tuple(map(tuple, df.astype(str).values.tolist())), round(float(st.session_state.supply), 6))


def lines_from_key(pkey: tuple) -> list:
    # turn a portfolio_key() back into reserve lines (cached functions receive the key, not the lines)
    df = pd.DataFrame([list(r) for r in pkey[0]], columns=EDITOR_COLUMNS)
    for col in ["Amount ($bn)", "Maturity (yrs)", "Yield (%)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return frame_to_lines(df)


def show_issues(issues: list) -> bool:
    # show validation messages, return True if any of them is an error
    for level, msg in issues:
        {"error": st.error, "warning": st.warning}.get(level, st.info)(esc(msg), icon=":material/rule:")
    return any(level == "error" for level, _ in issues)


def changed_count(keys) -> int:
    # how many of these sliders are away from their default
    return sum(st.session_state.get(k, SC_DEFAULTS[k]) != SC_DEFAULTS[k] for k in keys)


# Sidebar

def sidebar() -> None:
    with st.sidebar:
        # three groups, each with its own small heading: portfolio, your holding, market data
        st.markdown('<div class="pg-tag first">Portfolio</div>', unsafe_allow_html=True)
        names = preset_names()
        st.selectbox("Reserve portfolio", options=list(names), format_func=names.get, key="preset_id",
                     on_change=on_preset_change, label_visibility="collapsed")
        meta = st.session_state.portfolio_meta
        lines, supply = current_portfolio()
        reserves = sum(l.amount_bn for l in lines)
        buffer = fmt_bp(reserves / supply - 1) if supply else "n/a"
        basis = meta.get("basis", "hypothetical")
        if meta.get("edited"):
            status = "Edited in the Stress Lab"
        elif basis == "reported":
            status = f"Reported · as of {meta.get('as_of', '')}"
        elif basis == "reconstructed":
            status = f"Reconstructed (approx.) · {meta.get('as_of', '')}"
        else:
            status = "Hypothetical teaching portfolio"
        st.markdown(
            f'<div class="pg-side-card">'
            f'<div class="row"><span class="l">Tokens outstanding</span><span class="r">${supply:,.2f}bn</span></div>'
            f'<div class="row"><span class="l">Reserves</span><span class="r">${reserves:,.2f}bn</span></div>'
            f'<div class="row"><span class="l">Buffer above par</span><span class="r">{buffer}</span></div>'
            f'<div class="row"><span class="l">{status}</span></div></div>', unsafe_allow_html=True)
        st.markdown('<div class="pg-tag">Your holding</div>', unsafe_allow_html=True)
        st.number_input("Your position ($m)", min_value=0.0, step=5.0, format="%.1f", key="position_m",
                        help="How much of this stablecoin you hold. Used to turn every stress result into a "
                             "dollar loss and a time to exit.")
        st.toggle("We can redeem directly with the issuer", key="direct_access",
                  help="Off = you hold through an exchange or broker and can only exit by selling on the market. "
                       "On = you have an account with the issuer (subject to its minimum and fee).")
        st.markdown('<div class="pg-tag">Market data</div>', unsafe_allow_html=True)
        st.toggle("Fetch live market data", key="live_data",
                  help="Off: uses the last cached fetch or the bundled snapshot, so the app works without "
                       "internet.")
        st.markdown('<div class="pg-foot">NUS IS4228 · Technical track · Team Bulge<br>'
                    'Educational project, not investment or legal advice.</div>', unsafe_allow_html=True)


# Formatting

def esc(text: str) -> str:
    # Escape dollar signs so Streamlit markdown does not render them as LaTeX maths.
    return str(text).replace("$", "\\$")


def fmt_bn(x: float, dp: int = 2) -> str:
    # $bn amount; amounts under $1bn are shown in $m so small coins and small losses stay readable.
    if x is None or pd.isna(x):
        return "n/a"
    if abs(x) < 5e-7:
        return "$0"
    if abs(x) < 1:
        return f"${x * 1000:,.{max(dp - 1, 1)}f}m"
    return f"${x:,.{dp}f}bn"


def fmt_bp(x: float, signed: bool = True) -> str:
    # A fraction as basis points: 0.0224 -> '+224bp'.
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x * 1e4:+,.0f}bp" if signed else f"{x * 1e4:,.0f}bp"


def fmt_m(x: float, dp: int = 2) -> str:
    # $m amount (position sizes).
    if x is None or pd.isna(x):
        return "n/a"
    return f"${x:,.{dp}f}m"


def fmt_cov(x: float) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:.2%}"


def fmt_token(x: float) -> str:
    return "n/a" if x is None or pd.isna(x) else f"${x:.4f}"


def page_header(eyebrow: str, title: str, subtitle: str = "") -> None:
    sub = f'<div class="pg-sub">{subtitle}</div>' if subtitle else ""
    html = f'<div class="pg-eyebrow">{eyebrow}</div><div class="pg-hero">{title}</div>{sub}'
    # HTML entity so a pair of dollar signs is not rendered as LaTeX maths
    st.markdown(html.replace("$", "&#36;"), unsafe_allow_html=True)


def section(title: str, label: str = "") -> None:
    # Section heading with an optional small uppercase label (e.g. a step number).
    lbl = f'<span class="n">{label}</span>' if label else ""
    st.markdown(f'<div class="pg-section">{lbl}<span class="t">{title}</span></div>', unsafe_allow_html=True)


def verdict_banner(summary: dict) -> None:
    color = VERDICT_COLORS.get(summary["verdict"], "#334155")
    st.markdown(
        f'<div class="pg-banner" style="border-left-color:{color}">'
        f'<div><div class="lbl">Verdict</div><div class="v" style="color:{color}">{summary["verdict"]}</div></div>'
        f'<div class="h">{summary["headline"]}</div></div>',
        unsafe_allow_html=True,
    )


def scenario_chips(sc: Scenario, coin: str) -> None:
    # one line that says which coin and crisis the results below are for
    parts = [coin, f"{sc.rate_shock_bp:+.0f}bp rates" + (f" ({sc.curve_twist_bp:+.0f}bp at 10y)" if sc.curve_twist_bp else "")]
    days = len(sc.daily_requests_pct) or sc.horizon_days
    parts.append(f"{sc.redemption_pct:.0f}% run over {days} days")
    parts.append(f"{wait_label(sc.max_wait_days)} deadline")
    if sc.bank_freeze_pct:
        where = sc.failed_bank or "all banks"
        parts.append(f"{sc.bank_freeze_pct:.0f}% bank cash frozen ({where})")
    for label, v in (("crypto", sc.crypto_shock_pct), ("gold", sc.gold_shock_pct), ("other assets", sc.risk_shock_pct)):
        if v:
            parts.append(f"{label} {v:+.0f}%")
    if sc.loan_loss_pct:
        parts.append(f"loan loss {sc.loan_loss_pct:.0f}%")
    if sc.closed_days:
        parts.append(f"closed {sc.closed_days} day(s)")
    if sc.panic_sensitivity:
        parts.append("panic on")
    parts.append(f"fire-sale x{sc.stress_multiplier:g}")
    html = '<div class="pg-scn"><span class="k">Scenario</span>' + "".join(f"<span>{p}</span>" for p in parts)
    st.markdown((html + "</div>").replace("$", "&#36;"), unsafe_allow_html=True)


def verdict_chip(v: str) -> str:
    color = VERDICT_COLORS.get(v, "#334155")
    return f'<span class="pg-chip" style="color:{color}; background:{color}14; border:1px solid {color}33">{v}</span>'


def narrative(summary: dict, sc: Scenario, supply0: float) -> list[str]:
    s = summary
    out = []
    shocks = []
    if s["rate_loss"] > 1e-4:
        shocks.append(f"the +{sc.rate_shock_bp:.0f}bp rate rise cut {fmt_bn(s['rate_loss'])} off bills and notes")
    if s["price_loss"] > 1e-4:
        shocks.append(f"price and credit shocks cost {fmt_bn(s['price_loss'])}")
    if s["frozen"] > 1e-4:
        stuck = (f"released on day {s['frozen_released_day']}" if s["frozen_released_day"]
                 else "still frozen at the end")
        shocks.append(f"{fmt_bn(s['frozen'])} of bank cash was frozen "
                      f"({sc.frozen_recovery_pct:.0f}% assumed recoverable, {stuck})")
    if shocks:
        out.append("**Day 0:** " + "; ".join(shocks) + f". Coverage moved from {fmt_cov(s['start_coverage'])} "
                   f"to {fmt_cov(s['day0_coverage'])}.")
    else:
        out.append(f"**Day 0:** no valuation shock. Coverage starts at {fmt_cov(s['start_coverage'])}.")
    days = len(sc.daily_requests_pct) or sc.horizon_days
    out.append(f"**The run:** holders asked to redeem {fmt_bn(s['requested'])} over {days} days "
               f"and {fmt_bn(s['paid'])} was paid at $1.")
    if s.get("closed_days"):
        out.append(f"**Window closed:** redemptions could not be paid for the first {s['closed_days']} day(s) (a "
                   "weekend or bank holiday), so requests piled up and deadlines only started once it reopened.")
    if s["days_simulated"] > sc.horizon_days:
        out.append(f"**After the run:** requests were still queued, so the issuer kept paying until day "
                   f"{s['days_simulated']} (the last deadline, {wait_label(s['max_wait_days'])}).")
    if s["frozen_released_day"] and s["frozen"] > 1e-4:
        out.append(f"**Stuck, not lost:** on day {s['frozen_released_day']} the recovered "
                   f"{fmt_bn(s['frozen'] - s['frozen_writedown'])} of frozen cash became spendable again"
                   + (f"; the other {fmt_bn(s['frozen_writedown'])} is a permanent loss."
                      if s["frozen_writedown"] > 1e-4 else "."))
    if s["fire_sale_loss"] > 1e-4:
        out.append(f"**Selling in a hurry:** raising that cash cost {fmt_bn(s['fire_sale_loss'])} in fire-sale "
                   "discounts, more per dollar the more was sold in one day.")
    if s.get("spillover_loss", 0) > 1e-4:
        out.append(f"**Selling moves the price:** the sales pushed market prices down, marking the holdings that "
                   f"were not sold down by another {fmt_bn(s['spillover_loss'])}.")
    if s["first_breach_day"] is not None:
        out.append(f"**Too slow:** from day {s['first_breach_day']} the issuer could not turn assets into cash fast "
                   f"enough to pay within the deadline ({wait_label(s['max_wait_days'])}). "
                   f"{fmt_bn(s['late_amount'])} of requests missed it and {fmt_bn(s['unmet'])} was still waiting "
                   "at the end.")
    if s["final_coverage"] < s["day0_coverage"] - 1e-4 and s["day0_coverage"] < 1:
        out.append(f"**First-mover advantage:** early redeemers got $1 while reserves were below par, so the "
                   f"{fmt_bn(s['supply_end'])} of tokens left are backed by only {fmt_token(s['final_coverage'])} "
                   f"each. That gap is why runs speed up (Diamond & Dybvig 1983).")
    elif s["verdict"] == "HOLDS":
        out.append(f"**Result:** reserves never dropped below par (lowest coverage {fmt_cov(s['min_coverage'])}).")
    mp = s.get("min_market_price")
    if mp is not None and not pd.isna(mp) and mp < 0.9995:
        out.append(f"**On the market:** someone who cannot redeem directly would see the token trade as low as "
                   f"{fmt_token(mp)} (day {s['market_low_day']}): the reserves left behind the queue, valued with "
                   f"frozen cash at the market's {sc.market_frozen_recovery_pct:.0f}% recovery guess, discounted for "
                   "the wait.")
    return out


def steps_list(items: list[str]) -> None:
    # a compact bulleted list; **bold** in the text becomes bold
    body = "".join("<li>" + re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(t, quote=False)) + "</li>"
                   for t in items)
    st.markdown(f'<ul class="pg-steps">{body}</ul>'.replace("$", "&#36;"), unsafe_allow_html=True)


def scenario_library_options() -> dict[str, Scenario]:
    return {sc.name: sc for sc in STANDARD_SCENARIOS}


def asset_class_labels() -> list[str]:
    return [a.label for a in ASSET_CLASSES.values()]
