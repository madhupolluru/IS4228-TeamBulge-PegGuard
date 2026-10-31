# Stress Lab: pick a stablecoin, pick a crisis, see the answer and how it was derived.
# Tabs: your scenario, what drives it (tornado + Monte Carlo), breaking point (rate shock x run size map).

from dataclasses import replace

import pandas as pd
import streamlit as st

from app_pages.common import (SC_DEFAULTS, WAIT_OPTIONS, apply_scenario, asset_class_labels, basis_badge,
                              basis_notice, changed_count, current_portfolio, current_scenario, esc, fmt_bn, fmt_bp,
                              fmt_cov, fmt_m, fmt_token, holder_access, lines_from_key, matching_crisis, narrative,
                              on_preset_change, page_header, portfolio_key, position_m, scenario_chips,
                              scenario_library_options, section, share_query, show_issues, steps_list, verdict_banner,
                              verdict_chip, wait_label)
from pegguard import charts
from pegguard.analysis import (breaking_point_grid, monte_carlo, position_outcome, stress_sensitivity,
                               survival_frontier, tornado)
from pegguard.charts import BAD, GOOD, WARN
from pegguard.engine import PAR_TOL, Scenario, run_stress
from pegguard.presets import all_presets, preset_names
from pegguard.report import build_memo
from pegguard.reserves import composition
from pegguard.validation import validate_portfolio, validate_scenario

NO_BAR = {"displayModeBar": False}

CRISIS_NOTES = {
    "Calm": "A normal week: 5% of tokens redeemed over 5 days, no valuation shocks.",
    "Rate shock": "Yields jump 300bp overnight, then 20% of holders redeem over 5 days in a stressed market "
                  "(fire-sale discounts x2).",
    "Crypto crash": "Bitcoin -50%, gold -10%, other investments -30% and a 10% loss on secured loans; 30% of "
                    "holders redeem with fire-sale discounts x3.",
    "Bank failure": "30% of the reserves' bank cash is frozen at a failed bank and assumed lost; 25% of holders "
                    "redeem, half of them on day 1.",
    "Full panic": "+150bp, crypto -40%, a 15% loan loss and a 50% run with panic feedback: redemptions speed up "
                  "as reserves fall below par.",
}

# slider groups, used to show "(2 changed)" on the collapsed sections
SHOCK_KEYS = ["sc_freeze", "sc_recovery", "sc_release", "sc_crypto", "sc_gold", "sc_risk", "sc_loan", "sc_front",
              "sc_stress", "sc_panic"]
ADVANCED_KEYS = ["sc_twist", "sc_closed", "sc_failed_bank", "sc_impact", "sc_spill", "sc_signal", "sc_lag",
                 "sc_mkt_return", "sc_mkt_recovery", "sc_fear"]


def short_name(crisis: str) -> str:
    return crisis.split(":")[0]


def on_pick_coin() -> None:
    st.session_state.preset_id = st.session_state.lab_coin
    on_preset_change()


def on_pick_crisis() -> None:
    lib = scenario_library_options()
    if st.session_state.lab_crisis in lib:
        apply_scenario(lib[st.session_state.lab_crisis])


def reset_extra_settings() -> None:
    # runs before the rerun, so it is allowed to change slider values
    for k in SHOCK_KEYS + ADVANCED_KEYS:
        st.session_state[k] = SC_DEFAULTS[k]


def group_label(title: str, keys: list) -> str:
    n = changed_count(keys)
    return f"{title} ({n} changed)" if n else title


# Cached heavy work. Each takes the portfolio key and the scenario as a dict so Streamlit can hash them.

@st.cache_data(show_spinner=False)
def cached_sensitivity(pkey: tuple, sc_dict: dict) -> pd.DataFrame:
    return stress_sensitivity(lines_from_key(pkey), pkey[1], Scenario(**sc_dict))


@st.cache_data(show_spinner="Flexing each assumption...")
def cached_tornado(pkey: tuple, sc_dict: dict) -> pd.DataFrame:
    return tornado(lines_from_key(pkey), pkey[1], Scenario(**sc_dict))


@st.cache_data(show_spinner="Running the scenario under many plausible assumption sets...")
def cached_monte_carlo(pkey: tuple, sc_dict: dict, n: int) -> tuple[pd.DataFrame, dict]:
    return monte_carlo(lines_from_key(pkey), pkey[1], Scenario(**sc_dict), n=n)


@st.cache_data(show_spinner="Running 441 stress tests...")
def cached_grid(pkey: tuple, sc_dict: dict) -> pd.DataFrame:
    return breaking_point_grid(lines_from_key(pkey), pkey[1], Scenario(**sc_dict))


@st.cache_data(show_spinner=False)
def compare_presets(bp: int, sc_dict: dict) -> pd.DataFrame:
    # largest run each bundled portfolio survives at one rate shock
    rows = []
    for p in all_presets():
        base = replace(Scenario(**sc_dict), rate_shock_bp=float(bp))
        g = breaking_point_grid(p.lines, p.supply_bn, base, rate_shocks_bp=[bp], run_sizes_pct=range(0, 105, 5))
        v = survival_frontier(g)["max_run_holds"].iloc[0]
        rows.append({"Portfolio": p.name.split(" (")[0], "Largest run survived (%)": 0.0 if pd.isna(v) else v})
    return pd.DataFrame(rows).sort_values("Largest run survived (%)", ascending=False)


page_header("Start here", "Stress Lab",
            "Pick a stablecoin, pick a crisis, and see whether every holder still gets $1 on time, what it means "
            "for your position, and exactly how the result was derived.")

meta = st.session_state.portfolio_meta
lines, supply = current_portfolio()
reserves = sum(l.amount_bn for l in lines)

# Step 1: the stablecoin

left, right = st.columns([1.35, 1])
with left:
    section("Choose a stablecoin", "Step 1")
    names = preset_names()
    st.session_state.lab_coin = st.session_state.preset_id  # keep in step with the sidebar
    st.selectbox("Stablecoin", options=list(names), format_func=names.get, key="lab_coin", on_change=on_pick_coin,
                 label_visibility="collapsed")
    st.markdown(f'<div class="pg-note">{basis_badge(meta.get("basis", "hypothetical"))} '
                f'{meta.get("description", "")}</div>'.replace("$", "&#36;"), unsafe_allow_html=True)
    basis_notice(meta)

    with st.expander("Edit reserves", icon=":material/edit:"):
        # a form, so the engine only re-runs when the user presses Apply
        with st.form(f"reserve_form_{st.session_state.editor_version}", border=False):
            edited = st.data_editor(
                st.session_state.reserves_df, num_rows="dynamic", width="stretch", hide_index=True,
                key=f"editor_{st.session_state.editor_version}",
                column_config={
                    "Holding": st.column_config.TextColumn(width="medium"),
                    "Asset class": st.column_config.SelectboxColumn(options=asset_class_labels(), required=True,
                                                                    width="medium"),
                    "Amount ($bn)": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
                    "Maturity (yrs)": st.column_config.NumberColumn(
                        min_value=0.0, max_value=30.0, format="%.3f",
                        help="0 for cash; 0.25 = 3-month bill; 10 = 10-year note"),
                    "Yield (%)": st.column_config.NumberColumn(min_value=-1.0, max_value=20.0, format="%.2f"),
                    "Bank / custodian": st.column_config.TextColumn(
                        width="medium", help="Name the bank holding each cash line so a bank failure can hit one "
                                             "bank only, and to check concentration limits"),
                },
            )
            new_supply = st.number_input("Tokens outstanding ($bn)", min_value=0.0001,
                                         value=float(st.session_state.supply), step=1.0, format="%.4f")
            if st.form_submit_button("Apply changes", type="primary", icon=":material/check:"):
                st.session_state.reserves_df = pd.DataFrame(edited).reset_index(drop=True)
                st.session_state.supply = float(new_supply)
                st.session_state.editor_version += 1
                st.session_state.portfolio_meta = {**meta, "edited": True}
                st.rerun()
        st.caption(esc(f"Source: {meta.get('source', '')}"))

    # stop here if the edited portfolio cannot be run
    if show_issues(validate_portfolio(lines, supply)):
        st.stop()

with right:
    st.plotly_chart(charts.composition_donut(composition(lines), f"{fmt_bn(reserves, 1)}<br>reserves"),
                    width="stretch", config=NO_BAR)

# Step 2: the crisis

st.divider()
section("Choose a crisis", "Step 2")
lib = scenario_library_options()
st.session_state.lab_crisis = matching_crisis() or "Custom"
st.segmented_control("Crisis", options=list(lib) + ["Custom"], format_func=short_name, key="lab_crisis",
                     on_change=on_pick_crisis, label_visibility="collapsed")
picked = st.session_state.lab_crisis
st.caption(esc(CRISIS_NOTES.get(short_name(picked), "") if picked in lib
               else "Custom: your own settings. Pick a crisis above to start from a standard one."))

# the four main levers
c1, c2, c3, c4 = st.columns(4)
c1.slider("Rate shock (bp)", -100.0, 500.0, step=25.0, key="sc_rate", format="%.0f",
          help="Instant rise in yields. Reprices bills and notes (Week 3 bond maths).")
c2.slider("Run size (% of tokens)", 0.0, 100.0, step=5.0, key="sc_run", format="%.0f%%",
          help="Share of all tokens whose holders ask to redeem during the run.")
c3.slider("Run length (days)", 1, 20, key="sc_days", help="Business days over which new redemption requests arrive.")
c4.select_slider("Redemption deadline", options=list(WAIT_OPTIONS), format_func=wait_label, key="sc_wait",
                 help="How long a holder may wait for their $1 before the request counts as late. MAS requires par "
                      "redemption within 5 business days.")

with st.expander(group_label("More shocks: bank failure, price falls, fire sales, panic", SHOCK_KEYS),
                 icon=":material/tune:"):
    a1, a2, a3 = st.columns(3)
    with a1:
        st.slider("Bank cash frozen in a bank failure (%)", 0.0, 100.0, step=5.0, format="%.0f", key="sc_freeze",
                  help="Share of the reserves' bank deposits stuck at a failed bank (USDC had $3.3bn at SVB).")
        st.slider("Expected recovery on frozen cash (%)", 0.0, 100.0, step=5.0, format="%.0f", key="sc_recovery",
                  help="The unrecovered share is a permanent loss.")
        st.select_slider("Frozen cash usable again on", options=list(range(0, 21)),
                         format_func=lambda d: "Never" if d == 0 else f"Day {d}", key="sc_release",
                         help="When the recovered share can pay redemptions again. The SVB deposits were released "
                              "after a weekend.")
    with a2:
        st.slider("Bitcoin / crypto price change (%)", -90.0, 0.0, step=5.0, format="%.0f", key="sc_crypto")
        st.slider("Gold price change (%)", -50.0, 10.0, step=5.0, format="%.0f", key="sc_gold")
        st.slider("Equities / other investments change (%)", -80.0, 10.0, step=5.0, format="%.0f", key="sc_risk")
        st.slider("Credit loss on secured loans (%)", 0.0, 100.0, step=5.0, format="%.0f", key="sc_loan")
    with a3:
        st.slider("How front-loaded the run is", 0.0, 0.9, step=0.05, key="sc_front",
                  help="0 = spread evenly; 0.5 = each day's requests are half the previous day's.")
        st.slider("Fire-sale stress multiplier", 0.0, 10.0, step=0.5, key="sc_stress",
                  help="Scales every fire-sale discount. 1 = orderly market, 3+ = crisis.")
        st.slider("Panic feedback", 0.0, 1.0, step=0.05, key="sc_panic",
                  help="Extra redemptions for each 1pp that reserves fall below par. 0.2 = +20% per 1pp.")

# only offer banks that actually appear in the portfolio
banks = sorted({l.bank for l in lines if l.asset_class == "cash_bank" and l.bank})
if st.session_state.sc_failed_bank not in [""] + banks:
    st.session_state.sc_failed_bank = ""

with st.expander(group_label("Advanced: market structure, timing and behaviour", ADVANCED_KEYS),
                 icon=":material/stacked_line_chart:"):
    b1, b2, b3 = st.columns(3)
    with b1:
        st.slider("Yield-curve twist at 10y (bp)", -200.0, 200.0, step=25.0, format="%.0f", key="sc_twist",
                  help="Added to the rate shock at 10 years, fading to zero at 3 months. Positive = steepener.")
        st.select_slider("Redemption window closed for", options=[0, 1, 2, 3, 4, 5],
                         format_func=lambda d: "Open" if d == 0 else f"First {d} day(s)", key="sc_closed",
                         help="A weekend or bank holiday: requests queue but nothing is paid. USDC/SVB happened over "
                              "a weekend.")
        st.selectbox("Bank that fails", options=[""] + banks, key="sc_failed_bank",
                     format_func=lambda b: "Every bank (spread evenly)" if b == "" else b,
                     help="With a named bank, the freeze % applies only to cash at that bank. Name banks in the "
                          "reserve editor.")
    with b2:
        st.slider("Price impact of large sales", 0.0, 3.0, step=0.25, key="sc_impact",
                  help="How much the discount grows when a lot is sold in one day. 0 = every sale pays the same "
                       "discount.")
        st.slider("Price drop on unsold holdings (spillover)", 0.0, 1.0, step=0.1, key="sc_spill",
                  help="Share of the day's price impact that also marks down what was not sold (UK gilts 2022).")
        st.select_slider("Holders react to", options=["coverage", "market"], key="sc_signal",
                         format_func={"coverage": "True reserves", "market": "Market price"}.get,
                         help="What drives panic: the true reserves (only insiders know) or the market price.")
        st.select_slider("Information lag (days)", options=[0, 1, 2, 3, 5], key="sc_lag",
                         help="Holders see the signal this many days late, like a monthly attestation.")
    with b3:
        st.slider("Return a market buyer demands (% a year)", 0.0, 100.0, step=5.0, format="%.0f", key="sc_mkt_return",
                  help="Discount for having to wait to redeem a token bought on the market.")
        st.slider("Market's guess of recovery on frozen cash (%)", 0.0, 100.0, step=5.0, format="%.0f", key="sc_mkt_recovery",
                  help="Until frozen cash is released, the market values it at this recovery.")
        st.slider("Fear premium (bp)", 0.0, 1000.0, step=25.0, format="%.0f", key="sc_fear",
                  help="Extra market discount while the coin is in distress. The back-tests suggest about 350bp.")
    if changed_count(SHOCK_KEYS + ADVANCED_KEYS):
        st.button("Reset all extra settings", icon=":material/restart_alt:", on_click=reset_extra_settings)

# Run the engine

sc = current_scenario()
show_issues(validate_scenario(sc, lines))
result = run_stress(lines, supply, sc)
s = result.summary
pos = position_m()
access = holder_access()
out = position_outcome(s, result.daily, pos, access)

# keep the address bar in sync, so copying it shares this exact scenario
try:
    st.query_params.from_dict(share_query())
except Exception:
    pass

sc_key = {**sc.to_dict(), "name": "lab"}  # same name every time, so the cache is reused
pkey = portfolio_key()
sens = cached_sensitivity(pkey, sc_key)
tor = cached_tornado(pkey, sc_key)
mc_df, mc = cached_monte_carlo(pkey, sc_key, int(st.session_state.get("mc_n", 300)))

deadline = wait_label(s["max_wait_days"])
deadline_txt = "the same-day deadline" if s["max_wait_days"] == 0 else f"the {deadline} deadline"


def card(num: int, question: str, answer: str, color: str, detail: str) -> str:
    return (f'<div class="pg-q" style="border-top-color:{color}"><div class="n">Question {num}</div>'
            f'<div class="t">{question}</div><div class="a" style="color:{color}">{answer}</div>'
            f'<div class="d">{detail}</div></div>')


def backing_card() -> str:
    if s["min_coverage"] >= PAR_TOL:
        return card(1, "Is there enough backing?", "Yes", GOOD,
                    f"Reserves never fell below $1 per token. Lowest coverage {fmt_cov(s['min_coverage'])}, a "
                    f"cushion of {fmt_bp(s['min_coverage'] - 1)}.")
    drivers = {"the rate shock": s["rate_loss"], "price and credit shocks": s["price_loss"],
               "frozen cash written off": s["frozen_writedown"], "fire-sale discounts": s["fire_sale_loss"],
               "price drops on unsold holdings": s["spillover_loss"]}
    top = max(drivers, key=drivers.get)
    run_effect = (" Paying early redeemers $1 while reserves were short pushed the shortfall onto those who stay."
                  if s["day0_coverage"] < 1 and s["min_coverage"] < s["day0_coverage"] - 0.01 else "")
    return card(1, "Is there enough backing?", "No", BAD,
                f"Reserves fell to {fmt_token(s['min_token_value'])} per token ({fmt_bp(s['min_coverage'] - 1)} vs "
                f"par). Losses of {fmt_bn(s['total_loss'])}, mostly {top}.{run_effect}")


def timing_card() -> str:
    if s["requested"] <= 1e-9:
        return card(2, "Were redemptions paid on time?", "No requests", GOOD, "Nobody redeemed in this scenario.")
    if s["first_breach_day"] is None:
        return card(2, "Were redemptions paid on time?", "Yes", GOOD,
                    f"All {fmt_bn(s['paid'])} of requests were paid within {deadline_txt}.")
    share = s["late_amount"] / s["requested"]
    still = f" {fmt_bn(s['unmet'])} was still unpaid at the end." if s["unmet"] > 1e-6 else ""
    return card(2, "Were redemptions paid on time?", "No", BAD if share > 0.05 else WARN,
                f"{fmt_bn(s['late_amount'])} ({share:.0%} of requests) waited past {deadline_txt}, starting on day "
                f"{s['first_breach_day']}.{still}")


def position_card() -> str:
    if not out["can_redeem"]:
        minimum = f", or below the {fmt_m(access['min_m'])} minimum" if access["min_m"] else ""
        redeem = f"not open to you (no direct account{minimum})."
    elif out["exit_day"] is not None:
        on_time = "within the deadline" if out["exit_on_time"] else f"after {deadline_txt}"
        fee = f", less a {fmt_m(out['redeem_fee'])} fee." if out["redeem_fee"] > 0 else "."
        redeem = f"paid in full on day {out['exit_day']}, {on_time}{fee}"
    else:
        redeem = f"only {out['exit_share_paid']:.0%} paid by day {out['last_day']}."
    sell = (f"{fmt_m(out['sell_now_value'])} at {fmt_token(out['market_price_now'])} now; "
            f"{fmt_m(out['sell_low_value'])} at the low.")
    loss = out["hold_loss"]
    color = BAD if loss > 1e-6 else (GOOD if out["exit_on_time"] else WARN)
    return card(3, f"What does it mean for your {fmt_m(pos, 1)}?", f"-{fmt_m(loss)}" if loss > 1e-6 else "No loss",
                color, f"<b>Hold:</b> worth {fmt_m(out['hold_value'])} at the low point ({out['haircut_bp']:,.0f}bp "
                       f"haircut).<br><b>Redeem on day 1:</b> {redeem}<br><b>Sell on the market:</b> {sell}")


def sensitivity_table() -> str:
    rows = ['<table class="pg-table"><tr><th>Fire-sale stress</th><th>Verdict</th><th>Worst value / token</th>'
            '<th>Fire-sale losses</th><th>Paid late</th><th>Your loss if you hold</th></tr>']
    for _, r in sens.iterrows():
        cls = ' class="cur"' if r["current"] else ""
        label = f"x{r['stress']:g}" + (" (yours)" if r["current"] else "")
        your_loss = pos * max(0.0, 1 - r["min_token_value"])
        rows.append(f'<tr{cls}><td class="name">{label}</td><td>{verdict_chip(r["verdict"])}</td>'
                    f'<td>{fmt_token(r["min_token_value"])}</td><td>{fmt_bn(r["fire_sale_loss"])}</td>'
                    f'<td>{fmt_bn(r["late_amount"])}</td><td>{fmt_m(your_loss)}</td></tr>')
    return "".join(rows) + "</table>"


# Step 3: results

st.divider()
section("Results", "Step 3")
scenario_chips(sc, meta["name"] + (" (edited)" if meta.get("edited") else ""))
tab_run, tab_why, tab_bp = st.tabs([":material/insights: Your scenario", ":material/troubleshoot: What drives it",
                                    ":material/grid_on: Breaking point"])

with tab_run:
    verdict_banner(s)
    st.markdown(('<div class="pg-qs">' + backing_card() + timing_card() + position_card() + "</div>")
                .replace("$", "&#36;"), unsafe_allow_html=True)

    # four headline numbers (the cards above already cover the rest)
    m = st.columns(4)
    m[0].metric("Lowest coverage", fmt_cov(s["min_coverage"]),
                delta=f"{(s['min_coverage'] - s['start_coverage']) * 1e4:+,.0f}bp vs start")
    m[1].metric("Lowest market price", fmt_token(s["min_market_price"]),
                delta=None if s["min_market_price"] >= 0.99995 else f"day {s['market_low_day']}", delta_color="off",
                help="What a buyer who cannot redeem directly would pay (see the advanced settings)")
    m[2].metric("Total losses", fmt_bn(s["total_loss"]),
                help=f"{s['total_loss'] / reserves * 1e4:,.0f}bp of starting reserves" if reserves else None)
    m[3].metric("Paid late", fmt_bn(s["late_amount"]),
                delta=None if s["first_breach_day"] is None else f"from day {s['first_breach_day']}",
                delta_color="inverse",
                help=f"Requests still waiting at their deadline ({deadline}). Unmet at the end: {fmt_bn(s['unmet'])}")

    st.write("")
    section("How this result was derived")
    steps_list(narrative(s, sc, supply))

    # charts in tabs, one at a time, instead of a long stack
    st.write("")
    g = st.tabs(["Value per token", "Holdings", "Reserves vs tokens", "Redemptions", "Losses"])
    g[0].plotly_chart(charts.coverage_path(result.daily), width="stretch", config=NO_BAR)
    g[1].plotly_chart(charts.holdings_used(result.by_holding), width="stretch", config=NO_BAR)
    g[2].plotly_chart(charts.reserves_vs_tokens(result.daily), width="stretch", config=NO_BAR)
    g[3].plotly_chart(charts.redemptions_bars(result.daily), width="stretch", config=NO_BAR)
    g[4].plotly_chart(charts.loss_breakdown(s), width="stretch", config=NO_BAR)

    st.write("")
    section("Same scenario at different fire-sale stress", "Sensitivity")
    st.caption("Each row re-runs your scenario with only the fire-sale stress changed (1 = orderly market, "
               "3+ = crisis). Your setting is highlighted.")
    st.markdown(sensitivity_table().replace("$", "&#36;"), unsafe_allow_html=True)

    st.write("")
    section("Take it to your risk committee", "Export")
    memo = build_memo(meta, sc, s, out, sensitivity=sens, tornado=tor, mc_stats=mc)
    summary_row = pd.DataFrame([{
        "portfolio": meta["name"], "data_basis": meta.get("basis", ""), **sc.to_dict(),
        **{k: v for k, v in s.items() if k != "headline"},
        "position_m": pos, "position_loss_m": out["hold_loss"], "position_exit_day": out["exit_day"],
        "position_best_route": out["best_route"], "position_sell_now_m": out["sell_now_value"],
    }])
    x = st.columns(4)
    x[0].download_button("Memo (HTML)", memo["html"], file_name="pegguard_risk_memo.html", mime="text/html",
                         icon=":material/description:", type="primary", width="stretch",
                         help="Open it in a browser and print to PDF")
    x[1].download_button("Memo (Markdown)", memo["markdown"], file_name="pegguard_risk_memo.md",
                         mime="text/markdown", icon=":material/article:", width="stretch")
    x[2].download_button("Day-by-day CSV", result.daily.to_csv(index=False), file_name="pegguard_stress_run.csv",
                         mime="text/csv", icon=":material/download:", width="stretch")
    x[3].download_button("Summary CSV", summary_row.to_csv(index=False), file_name="pegguard_scenario_summary.csv",
                         mime="text/csv", icon=":material/download:", width="stretch",
                         help="Settings, results and your position in one row")
    st.caption("To share this exact scenario, copy the address bar: it holds the coin, every slider and your position.")

    with st.expander("Day-by-day table", icon=":material/table:"):
        show = result.daily.rename(columns={
            "day": "Day", "open": "Window open", "new_requests": "New requests", "queued_in": "Carried over",
            "requested": "Requested", "paid": "Paid", "unmet": "Unmet", "fire_sale_loss": "Fire-sale loss",
            "spillover_loss": "Spillover loss", "interest": "Interest", "supply": "Tokens", "reserves": "Reserves",
            "liquid_next_day": "Liquid next day", "coverage": "Coverage", "market_price": "Market price",
            "panic_multiplier": "Panic x"})
        number_cols = [c for c in show.columns if c not in ("Day", "Window open")]
        st.dataframe(show.style.format({c: "{:,.3f}" for c in number_cols}), width="stretch", hide_index=True)

with tab_why:
    st.caption("Liquidity, fire-sale costs and run behaviour are judgement calls. This tab shows how much the answer "
               "depends on them, so you know which assumption to defend, or to check, before acting on it.")
    flips = tor[tor["flips"]]["Assumption"].tolist()
    if tor["swing"].max() < 1e-6 and not flips:
        # nothing moves: an empty chart would look broken, so say it plainly
        st.success(esc(f"Robust: pushing each assumption up or down on its own leaves the worst value per token at "
                       f"{fmt_token(s['min_token_value'])} and the verdict unchanged."), icon=":material/verified:")
    else:
        w1, w2 = st.columns([1.35, 1], gap="large")
        w1.plotly_chart(charts.tornado_chart(tor, s["min_token_value"]), width="stretch", config=NO_BAR)
        with w2:
            section("Reading it")
            top = tor.iloc[0]
            st.markdown(esc(
                f"Each bar re-runs your scenario with **one** assumption pushed to a low case (grey) and a high case "
                f"(green). The answer is most sensitive to **{top['Assumption'].lower()}**: from "
                f"{fmt_token(top['low'])} to {fmt_token(top['high'])} per token."))
            if flips:
                st.warning(esc("Changing any one of these on its own changes the verdict: " + "; ".join(flips) + "."),
                           icon=":material/swap_horiz:")
            else:
                st.success("No single assumption, pushed this far, changes the verdict.", icon=":material/verified:")

    st.write("")
    section("How often does it break?", "Monte Carlo")
    st.columns([1, 2])[0].select_slider("Number of runs", options=[100, 200, 300, 500, 1000], key="mc_n")
    k = st.columns(4)
    k[0].metric("Holds", f"{mc['p_holds']:.0%}", help="Share of runs where every redemption is paid on time at par")
    k[1].metric("Reserves below par", f"{mc['p_below_par']:.0%}")
    k[2].metric("Some redemptions late", f"{mc['p_late']:.0%}")
    k[3].metric("Worst value, 5th percentile", fmt_token(mc["value_p5"]), help=f"Median {fmt_token(mc['value_median'])}")
    st.plotly_chart(charts.mc_histogram(mc_df), width="stretch", config=NO_BAR)
    st.caption(f"{mc['n']} runs of your scenario. Your shocks stay fixed; what varies is what nobody knows precisely: "
               "each asset's daily liquidity, fire-sale stress, price impact, spillover, run size and (if a bank "
               "failed) recovery. Fixed random seed, so the result is reproducible.")

with tab_bp:
    # the grid ignores the two axis sliders, so moving them reuses the cached grid
    grid_sc = replace(sc, name="grid", rate_shock_bp=0.0, redemption_pct=0.0).to_dict()
    st.caption(esc(f"Every cell is a full stress run of your scenario with only the rate shock and run size changed. "
                   f"Red means reserves fell below $1 per token; \"late\" means some redemptions missed the deadline "
                   f"({deadline}). Dashed line: largest run paid on time. Solid line: largest run paid on time and at "
                   f"par. The marker is your scenario."))
    grid = cached_grid(pkey, grid_sc)
    frontier = survival_frontier(grid)
    st.plotly_chart(charts.breaking_heatmap(grid, frontier, marker=(sc.rate_shock_bp, sc.redemption_pct)),
                    width="stretch", config=NO_BAR)

    b1, b2 = st.columns([1, 1.2])
    with b1:
        section("Survival frontier")
        f = frontier.rename(columns={"rate_shock_bp": "Shock (bp)", "max_run_paid_on_time": "Paid on time",
                                     "max_run_at_par": "At par", "max_run_holds": "Fully holds"}).iloc[::2].copy()
        for col in ["Paid on time", "At par", "Fully holds"]:
            f[col] = f[col].map(lambda v: "never" if pd.isna(v) else f"{v:.0f}%")
        f["Shock (bp)"] = f["Shock (bp)"].map(lambda v: f"+{v:.0f}")
        st.dataframe(f, width="stretch", hide_index=True, height=420)
        st.caption("Largest run (% of tokens) that is paid on time / leaves reserves at par / does both. 'never' "
                   "means reserves are already below par at that rate shock before anyone redeems.")
    with b2:
        section("Compare portfolios")
        bp_pick = st.select_slider("Rate shock for the comparison (bp)", options=list(range(0, 525, 25)),
                                   value=int(min(max(sc.rate_shock_bp, 0), 500) // 25 * 25), key="bp_compare")
        st.plotly_chart(charts.preset_frontier_bars(compare_presets(bp_pick, grid_sc)), width="stretch",
                        config=NO_BAR)
        st.caption("Uses each portfolio's bundled reserves (not your edits) and your other scenario settings.")

st.divider()
st.page_link("app_pages/treasury.py", label="Next: decide how much of each coin to hold in the Treasury Planner",
             icon=":material/arrow_forward:")
