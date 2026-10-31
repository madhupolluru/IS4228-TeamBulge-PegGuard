# Dashboard: coin tiles, the live market, a sortable risk ranking and the five-crisis scorecard.

from dataclasses import replace

import pandas as pd
import streamlit as st

from app_pages.common import (BASIS, PEG_ALERT_BP, basis_badge, fmt_bn, fmt_bp, fmt_m, page_header, position_m,
                              reference_scenario, request_preset, section, verdict_chip)
from app_pages.live_monitor import market_table
from app_pages.live_monitor import render as live_monitor
from pegguard.analysis import (STANDARD_SCENARIOS, breaking_point_grid, regulatory_check, risk_profile, run_library,
                               survival_frontier)
from pegguard.presets import load_preset, preset_info

REF = reference_scenario()


@st.cache_data(show_spinner="Stress-testing every portfolio...")
def preset_metrics() -> pd.DataFrame:
    # one row per bundled portfolio: reserve strength, liquidity and stress results (not position-dependent)
    rows = []
    for info in preset_info():
        p = load_preset(info["id"])
        reserves = sum(l.amount_bn for l in p.lines)
        rp = risk_profile(p.lines, p.supply_bn)
        rc = regulatory_check(p.lines, p.supply_bn)
        lib = run_library(p.lines, p.supply_bn)
        worst = lib.loc[lib["Value per token"].idxmin()]
        g = breaking_point_grid(p.lines, p.supply_bn, replace(REF, redemption_pct=0.0),
                                rate_shocks_bp=[REF.rate_shock_bp], run_sizes_pct=range(0, 105, 5))
        run_ok = survival_frontier(g)["max_run_holds"].iloc[0]
        rows.append({
            **info,
            "supply_bn": p.supply_bn,
            "buffer": reserves / p.supply_bn - 1,
            "cash_1d": rp["day1_capacity_share"],
            "cash_5d": rp["day5_capacity_share"],
            "rate_bp_per_100": rp["loss_per_100bp_pct"] * 100,
            "largest_run": 0.0 if pd.isna(run_ok) else float(run_ok),
            "worst_value": float(worst["Value per token"]),
            "worst_scenario": worst["Scenario"],
            "genius": rc["GENIUS"]["eligible_backing"],
            "verdicts": dict(zip(lib["Scenario"], lib["Verdict"])),
            "values": dict(zip(lib["Scenario"], lib["Value per token"])),
        })
    return pd.DataFrame(rows)


metrics = preset_metrics()
pos = position_m()
live = bool(st.session_state.get("live_data", False))
market, _ = market_table(live)
issuers = metrics[metrics["group"] == "issuer"]
# latest real report date (hypothetical portfolios have no date)
report_dates = pd.to_datetime(issuers["as_of"], errors="coerce").dropna()
latest = report_dates.max().strftime("%d %b %Y") if len(report_dates) else "n/a"

page_header(
    "Dashboard",
    "Stablecoin risk dashboard",
    f"The summary after the Stress Lab: peg, reserve strength and stress results for the stablecoins you hold, "
    f"side by side. Latest reserve report: {latest}. Every dollar figure is for your {fmt_m(pos, 1)} "
    "position (set it in the sidebar).")


# coin tiles

def _peg_chip(gap: float) -> str:
    if pd.isna(gap):
        return ""  # no live price: leave the space for the coin's name and date
    color = "#13804A" if abs(gap) <= 10 else ("#B7791F" if abs(gap) <= PEG_ALERT_BP else "#C53030")
    return (f'<span class="pg-chip" style="color:{color}; background:{color}14; border:1px solid {color}33">'
            f'{gap:+.0f}bp</span>')


def _stat(label: str, value: str, tone: str = "") -> str:
    return f'<div><div class="l">{label}</div><div class="v {tone}">{value}</div></div>'


def coin_tile(row: pd.Series) -> str:
    m = market[market["symbol"] == row["symbol"]]
    price = m["price"].iloc[0] if len(m) else float("nan")
    gap = m["peg_gap_bps"].iloc[0] if len(m) else float("nan")
    supply = m["supply_usd"].iloc[0] / 1e9 if len(m) else row["supply_bn"]
    chg = m["chg_7d_pct"].iloc[0] if len(m) else float("nan")
    supply_txt = fmt_bn(supply, 1).replace("$", "&#36;") + (
        "" if pd.isna(chg) else f" <span class='pg-note'>{chg:+.1f}% 7d</span>")
    haircut = max(0.0, 1 - row["worst_value"])
    price_txt = "" if pd.isna(price) else f"&#36;{price:.4f}"
    if row["basis"] == "hypothetical":
        as_of = "illustrative reserves"
    else:
        as_of = "as of " + pd.to_datetime(row["as_of"]).strftime("%d %b %Y")
    return (
        f'<div class="pg-coin"><div class="top"><div><div class="sym">{row["symbol"]}</div>'
        f'<div class="nm">{basis_badge(row["basis"])}</div><div class="nm">{as_of}</div></div>'
        f'<div><div class="px">{price_txt}</div><div style="text-align:right">{_peg_chip(gap)}</div></div></div>'
        f'<div class="grid">'
        + _stat("Supply", supply_txt)
        + _stat("Buffer", fmt_bp(row["buffer"]), "good" if row["buffer"] >= 0 else "bad")
        + _stat("Survives run", f"{row['largest_run']:.0f}%")
        + _stat("Your loss", fmt_m(pos * haircut).replace("$", "&#36;"), "bad" if haircut > 1e-4 else "")
        + "</div></div>"
    )


cols = st.columns(len(issuers) + 1)
for col, (_, row) in zip(cols, issuers.iterrows()):
    col.markdown(coin_tile(row), unsafe_allow_html=True)
    if col.button(f"Stress-test {row['symbol']}", key=f"stress_{row['id']}", icon=":material/science:",
                  width="stretch"):
        request_preset(row["id"])
        st.switch_page("app_pages/stress_lab.py")
with cols[-1]:
    total = market["supply_usd"].sum() / 1e9 if len(market) else float("nan")
    worst = issuers.loc[issuers["worst_value"].idxmin()]
    st.markdown(
        '<div class="pg-coin"><div class="top"><div><div class="sym">Market</div>'
        '<div class="nm">USD stablecoins tracked</div></div></div><div class="grid">'
        + _stat("Total supply", f"&#36;{total:,.1f}bn")
        + _stat("Coins", f"{len(market)}")
        + _stat("Weakest", worst["symbol"], "bad" if worst["worst_value"] < 0.9999 else "")
        + _stat("Worst", f"&#36;{worst['worst_value']:.3f}")
        + "</div></div>", unsafe_allow_html=True)
st.caption(f"Buffer: reserves above tokens outstanding. Survives run: largest share of tokens that can redeem over {REF.horizon_days} days at a "
           f"+{REF.rate_shock_bp:.0f}bp rate shock (fire-sale stress x{REF.stress_multiplier:.0f}) and still be paid "
           "on time at par. Your loss: your position marked at the lowest reserves per token across the five "
           "standard scenarios below.")

st.divider()
live_monitor()

# risk ranking
st.divider()
section("Risk ranking", "Compare")
st.caption("Click a column header to sort. Issuers first, then benchmark portfolios for context. Data: Reported = "
           "from the issuer's own reserve report; Reconstructed = approximate rebuild of a past portfolio; "
           "Hypothetical = made up for teaching, not any real issuer's reserves. Use the toolbar to download as CSV.")
rank = pd.DataFrame({
    "Portfolio": metrics["name"],
    "Type": metrics["group"].map({"issuer": "Issuer", "benchmark": "Benchmark"}),
    "Data": metrics["basis"].map(BASIS),
    "Buffer (bp)": metrics["buffer"] * 1e4,
    "Cash in 1 day (%)": metrics["cash_1d"] * 100,
    "Cash in 5 days (%)": metrics["cash_5d"] * 100,
    "Loss per +100bp (bp)": metrics["rate_bp_per_100"],
    "Survives run (%)": metrics["largest_run"],
    "Worst value / token": metrics["worst_value"],
    "Worst haircut (bp)": (1 - metrics["worst_value"]).clip(lower=0) * 1e4,
    "Your worst-case loss ($m)": (1 - metrics["worst_value"]).clip(lower=0) * pos,
    "Worst scenario": metrics["worst_scenario"],
    "GENIUS eligible (%)": metrics["genius"] * 100,
})
st.dataframe(
    rank, width="stretch", hide_index=True,
    column_config={
        "Portfolio": st.column_config.TextColumn(width="medium", pinned=True),
        "Data": st.column_config.TextColumn(width="small",
                                            help="Reported, Reconstructed (approximate) or Hypothetical (teaching)"),
        "Buffer (bp)": st.column_config.NumberColumn(format="%+.0f",
                                                     help="Reserves above tokens outstanding, before any shock"),
        "Cash in 1 day (%)": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100,
                                                             help="Share of tokens that could be paid tomorrow"),
        "Cash in 5 days (%)": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100,
                                                              help="MAS: par redemption within 5 business days"),
        "Loss per +100bp (bp)": st.column_config.NumberColumn(format="%.0f",
                                                              help="Reserve value lost if yields rise 1pp"),
        "Survives run (%)": st.column_config.NumberColumn(
            format="%.0f", help=f"Largest run paid on time at par, at +{REF.rate_shock_bp:.0f}bp"),
        "Worst value / token": st.column_config.NumberColumn(format="$%.3f"),
        "Worst haircut (bp)": st.column_config.NumberColumn(format="%.0f"),
        "Your worst-case loss ($m)": st.column_config.NumberColumn(format="%.2f"),
        "GENIUS eligible (%)": st.column_config.NumberColumn(format="%.0f"),
    },
)

# scorecard
st.divider()
section("Every portfolio under the same five crises", "Scorecard")
st.caption("Same shocks, same assumptions, so portfolios can be compared like for like. Walk through one coin "
           "and one crisis in the Stress Lab first; this grid is the summary.")
with st.expander("Show the scorecard", icon=":material/table_view:"):
    html = ['<table class="pg-table"><tr><th>Portfolio</th>']
    for sc in STANDARD_SCENARIOS:
        html.append(f"<th>{sc.name}</th>")
    html.append("</tr>")
    ncol = len(STANDARD_SCENARIOS) + 1
    groups = (("issuer", "Issuers · reported reserves, except where marked"),
              ("benchmark", "Teaching benchmarks · hypothetical or reconstructed, not actual reserves"))
    for group, label in groups:
        html.append(f'<tr class="grp"><td colspan="{ncol}">{label}</td></tr>')
        for _, row in metrics[metrics["group"] == group].iterrows():
            cls = ' class="bench"' if group == "benchmark" else ""
            html.append(f'<tr{cls}><td class="name">{row["name"]}{basis_badge(row["basis"])}</td>')
            for sc in STANDARD_SCENARIOS:
                val = row["values"][sc.name]
                html.append(f'<td>{verdict_chip(row["verdicts"][sc.name])}'
                            f'<div class="val">&#36;{val:.3f} / token</div></td>')
            html.append("</tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)
    st.caption("Value per token = lowest reserves per token outstanding during the scenario. "
               "HOLDS: paid on time and at par · LIQUIDITY STRAIN: solvent but too slow · "
               "BELOW PAR: paid, but reserves < tokens · BREAKS: both. Benchmark results illustrate a mechanism; "
               "they are not a record of what any coin actually held or did.")
