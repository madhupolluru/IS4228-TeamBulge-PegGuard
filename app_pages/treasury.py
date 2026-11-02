# Treasury Planner: how should a treasury split its stablecoin float, and how much of each coin is safe?

import math
from html import escape

import pandas as pd
import streamlit as st

from app_pages.common import esc, fmt_m, page_header, section, steps_list, verdict_chip
from pegguard import charts
from pegguard.analysis import STANDARD_SCENARIOS
from pegguard.presets import preset_info, redemption_terms
from pegguard.treasury import Holding, safe_caps, stress_treasury, treasury_summary

page_header("Decide", "Treasury Planner",
            "Enter the coins you hold and your limits. Every coin goes through the same five crises at once, and "
            "you see the loss on the whole treasury and a safe amount for each coin.")

info = pd.DataFrame(preset_info())
names = dict(zip(info["id"], info["name"]))
# short labels: the symbol for issuers, the name without its bracketed note for benchmarks
short = {r.id: (r.symbol if isinstance(r.symbol, str) and r.symbol else r.name.split(" (")[0])
         for r in info.itertuples()}
# US$95m treasury; the SGD coin is kept small because its whole supply is only about US$10m
default_mix = {"usdc_2026_09": 60.0, "usdt_2026q2": 30.0, "xsgd_style": 5.0}

if "treasury_mix" not in st.session_state:
    st.session_state.treasury_mix = pd.DataFrame(
        [{"Coin": short[k], "Holding ($m)": v} for k, v in default_mix.items() if k in short])
st.session_state.setdefault("treasury_version", 0)
editor_key = f"treasury_editor_{st.session_state.treasury_version}"


def save_mix(key: str) -> None:
    # Apply the editor's changes to the saved holdings, so they survive a page switch.
    # A new editor key then shows the saved table (same idea as the Stress Lab's reserve editor).
    ed = st.session_state[key]
    df = st.session_state.treasury_mix.copy()
    for i, row in ed.get("edited_rows", {}).items():
        for col, v in row.items():
            df.loc[df.index[int(i)], col] = v
    df = df.drop(df.index[ed.get("deleted_rows", [])])
    if ed.get("added_rows"):
        df = pd.concat([df, pd.DataFrame(ed["added_rows"], columns=df.columns)], ignore_index=True)
    st.session_state.treasury_mix = df.reset_index(drop=True)
    st.session_state.treasury_version += 1


left, right = st.columns([1.3, 1])
with left:
    section("What you hold", "Step 1")
    mix = st.data_editor(
        st.session_state.treasury_mix, num_rows="dynamic", hide_index=True, width="stretch", key=editor_key,
        on_change=save_mix, args=(editor_key,),
        column_config={"Coin": st.column_config.SelectboxColumn(options=list(short.values()), required=True),
                       "Holding ($m)": st.column_config.NumberColumn(min_value=0.0, step=5.0, format="%.1f")})
with right:
    section("Your limits", "Step 2")
    # keyed (defaults in common.TR_DEFAULTS) so the limits survive a page switch
    budget_bp = st.slider("Loss limit (bp of the treasury)", 5, 500, step=5, key="tr_budget",
                          help="Your risk budget. 50bp on a $100m treasury = $0.5m.")
    need_days = st.select_slider("Cash needed within", options=[0, 1, 2, 3, 5], key="tr_days",
                                 format_func=lambda d: "Same day" if d == 0 else f"T+{d}",
                                 help="How quickly you must be able to turn the stablecoins back into cash.")
    direct = st.toggle("Direct redemption accounts", key="tr_direct",
                       help="Off = every exit is a sale on the market at the model's price. Separate from the "
                            "sidebar setting, which applies to your single position in the Stress Lab.")

ids = {v: k for k, v in short.items()}
holdings = [Holding(ids[r["Coin"]], float(r["Holding ($m)"] or 0)) for _, r in pd.DataFrame(mix).iterrows()
            if r.get("Coin") in ids and (r.get("Holding ($m)") or 0) > 0]
if not holdings:
    st.info("Add at least one coin with a holding above zero.")
    st.stop()

# merge duplicate rows of the same coin
agg: dict[str, float] = {}
for h in holdings:
    agg[h.preset_id] = agg.get(h.preset_id, 0.0) + h.amount_m
holdings = [Holding(k, v) for k, v in agg.items()]
total = sum(h.amount_m for h in holdings)
budget_m = total * budget_bp / 1e4


@st.cache_data(show_spinner="Stress-testing the treasury...")
def run_scorecard(hs: tuple, days: int, direct: bool) -> pd.DataFrame:
    return stress_treasury([Holding(p, a) for p, a in hs], STANDARD_SCENARIOS, liquidity_days=days,
                           direct_access=direct)


@st.cache_data(show_spinner=False)
def caps(pids: tuple, total: float, bp: float) -> pd.DataFrame:
    return safe_caps(list(pids), total, bp)


detail = run_scorecard(tuple((h.preset_id, h.amount_m) for h in holdings), need_days, direct)
summ = treasury_summary(detail, total, budget_bp)

st.divider()
section("Result", "Step 3")
k = st.columns(4)
k[0].metric("Treasury", fmt_m(total, 1))
k[1].metric("Worst-crisis loss", fmt_m(summ["worst_loss_m"]), delta=f"{summ['worst_loss_bp']:,.0f}bp",
            delta_color="inverse", help=f"In: {summ['worst_scenario']}")
k[2].metric("Cash within the deadline", f"{summ['cash_share']:.0%}",
            help="In the crisis that leaves the least cash: redeemed in time, or sold on the market")
k[3].metric("Diversification benefit", fmt_m(summ["diversification_m"]),
            help="Sum of each coin's own worst-crisis loss, minus the worst loss of the mix. Positive when the coins "
                 "are hurt by different crises.")
if summ["within_budget"]:
    st.success(esc(f"Within budget: the worst crisis costs {fmt_m(summ['worst_loss_m'])}, under your "
                   f"{fmt_m(budget_m)} limit."), icon=":material/verified:")
else:
    st.error(esc(f"Over budget: {summ['worst_scenario']} costs {fmt_m(summ['worst_loss_m'])} against a "
                 f"{fmt_m(budget_m)} limit. See the safe caps below."), icon=":material/warning:")

c1, c2 = st.columns([1.1, 1])
with c1:
    st.plotly_chart(charts.treasury_losses(summ["by_scenario"], budget_m), width="stretch",
                    config={"displayModeBar": False})
with c2:
    section("Safe cap for each coin")
    cp = caps(tuple(agg), total, float(budget_bp))
    rows = []
    for r in cp.itertuples():
        held = agg[r.preset_id]
        cap_txt = "no limit from these crises" if math.isinf(r.cap_m) else fmt_m(r.cap_m)
        over = (not math.isinf(r.cap_m)) and held > r.cap_m + 1e-9
        rows.append({"Coin": short.get(r.preset_id, names[r.preset_id]), "Held": fmt_m(held), "Safe cap": cap_txt,
                     "Status": "Over cap" if over else "OK"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"Coin": st.column_config.TextColumn(width="medium")})
    st.caption("Cap = your loss budget / the coin's worst haircut across the five crises: the most you could hold "
               "of that coin alone and still stay within budget.")

with st.expander("Coin by crisis detail", icon=":material/table:"):
    deadline = "Same day (T+0)" if need_days == 0 else f"T+{need_days}"
    coin_ids = list(agg)
    if st.session_state.get("scorecard_coin") not in coin_ids:
        st.session_state["scorecard_coin"] = "usdt_2026q2" if "usdt_2026q2" in coin_ids else coin_ids[0]
    coin = st.selectbox("Which coin are you looking at?", options=coin_ids, key="scorecard_coin",
                        format_func=lambda pid: f"{short[pid]} — holding {fmt_m(agg[pid])}")
    coin_detail = detail[detail["preset_id"] == coin].reset_index(drop=True)
    st.write("Two alternative choices: keep holding through the crisis, or start an exit when it begins.")
    issuer_results = {
        "HOLDS": "Backing maintained; payments on time",
        "BELOW PAR": "Backing shortfall; payments on time",
        "LIQUIDITY STRAIN": "Backing maintained; some payments late",
        "BREAKS": "Backing shortfall; some payments late",
    }

    def known(value) -> bool:
        return pd.notna(value) and math.isfinite(value)

    def payment_time(day) -> str:
        if not known(day):
            return "Full payment not reached"
        day = int(day)
        return "T+0 · same day" if day == 1 else f"T+{day - 1}"

    def exit_comparison(r) -> tuple[str, str]:
        both = bool(r.redeem_in_time and known(r.sell_cash_m))
        if both and abs(r.redeem_cash_m - r.sell_cash_m) <= 1e-9:
            return "Same cash", "Both meet your deadline; compare the payment times."
        if both:
            advantage = abs(r.redeem_cash_m - r.sell_cash_m)
            route = "Redeem" if r.route == "redeem" else "Sell on market"
            advantage_dollars = advantage * 1e6
            amount = f"${advantage_dollars:,.0f}" if advantage_dollars >= 1 else f"${advantage_dollars:.2f}"
            return route, f"{amount} more cash"
        if r.route == "redeem":
            return "Redeem", "Only available route by your deadline"
        if r.route == "sell":
            return "Sell on market", "Only available route by your deadline"
        return "No full exit", "Neither route provides full exit proceeds by your deadline"

    def table_cell(main: str, note: str = "") -> str:
        return f'<td><b>{escape(main)}</b><div class="val">{escape(note)}</div></td>'

    hold_tab, exit_tab = st.tabs(["Keep holding", "Exit at the start"])
    with hold_tab:
        st.markdown("**How well is your holding backed at the worst point?**")
        st.caption("A backing gap measures missing reserve support. It is not a market selling price or a realised cash loss.")
        html = ['<div style="overflow-x:auto"><table class="pg-table"><tr><th>Crisis</th>'
                '<th>Issuer’s stress result</th><th>Backing per $1 held</th><th>Backing gap on your holding</th></tr>']
        for r in coin_detail.itertuples():
            backing = f"${r.token_value:.4f}" if known(r.token_value) else "Not available"
            gap = fmt_m(r.loss_m) if known(r.token_value) else "Not available"
            html.append(f'<tr><td class="name">{escape(r.scenario)}</td>'
                        f'<td title="{escape(issuer_results[r.verdict])}">{verdict_chip(r.verdict)}</td>'
                        + table_cell(backing) + table_cell(gap) + '</tr>')
        html.append("</table></div>")
        st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)
        st.caption(esc("Backing is shown up to $1 per $1 held; surplus reserves are excluded. "
                       "Hover over a verdict for its meaning. The verdict covers the issuer's whole crisis."))

    with exit_tab:
        st.markdown("**How much cash could you receive if you act at the start?**")
        st.caption(f"Your cash deadline: {deadline}. Direct redemption accounts: {'enabled' if direct else 'disabled'}.")
        st.write("**Sell on market:** another buyer buys your tokens. "
                 "**Redeem:** return your tokens to the issuer for cash.")
        html = ['<div style="overflow-x:auto"><table class="pg-table"><tr><th>Crisis</th>'
                '<th>Sell on market</th><th>Redeem with issuer</th><th>Most cash by your deadline</th></tr>']
        for r in coin_detail.itertuples():
            sell_cash = fmt_m(r.sell_cash_m) if known(r.sell_cash_m) else "Not available"
            sell_note = "T+0 · immediately" if known(r.sell_cash_m) else "No modelled sale price"
            if not r.can_redeem:
                redeem_cash, redeem_note = "Not available", "No access or below minimum"
            elif not known(r.redeem_day):
                redeem_cash, redeem_note = "Not available", "Full payment not reached"
            elif not known(r.redeem_cash_m):
                redeem_cash, redeem_note = "Not available", "No modelled proceeds"
            else:
                redeem_cash, redeem_note = fmt_m(r.redeem_cash_m), payment_time(r.redeem_day)
                if not r.redeem_in_time:
                    redeem_note += " · after your deadline"
            choice, reason = exit_comparison(r)
            html.append(f'<tr><td class="name">{escape(r.scenario)}</td>'
                        + table_cell(sell_cash, sell_note) + table_cell(redeem_cash, redeem_note)
                        + table_cell(choice, reason) + '</tr>')
        html.append("</table></div>")
        st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)
        st.caption("These are modelled cash amounts. Do not subtract the holding view's backing gap from them. "
                   "T+0 means same day; T+1 means the next simulation day.")
        st.caption("Timing follows simulated issuer liquidity; bank processing and settlement are not separately modelled. "
                   "Redemption fees are included; market trading fees and slippage are excluded.")

    with st.expander("Explain the numbers for one crisis"):
        scenario = st.selectbox("Crisis to explain", options=list(range(len(coin_detail))),
                                format_func=lambda i: coin_detail.iloc[i]["scenario"],
                                key="scorecard_explain_crisis")
        r = next(coin_detail.iloc[[scenario]].itertuples())
        if known(r.token_value):
            st.markdown(esc(f"**If you keep holding:** {fmt_m(r.amount_m)} × the worst backing gap of "
                            f"{r.loss_m / r.amount_m:.2%} = **{fmt_m(r.loss_m)}** of missing reserve backing."))
        else:
            st.markdown("**If you keep holding:** reserve backing is not available for this scenario.")
        st.caption(esc("This uses the lowest reserve backing during the whole crisis, capped at $1 per $1 held."))
        if known(r.sell_cash_m):
            st.markdown(esc(f"**If you sell at the start:** the model values each $1 of your holding at "
                            f"${r.sell_cash_m / r.amount_m:.5f}, giving **{fmt_m(r.sell_cash_m)}**."))
            st.caption("That is the model's starting sale price after the initial shock, not its worst market price.")
        if r.can_redeem and known(r.redeem_day) and known(r.redeem_cash_m):
            st.markdown(esc(f"**If you request redemption at the start:** {fmt_m(r.amount_m)} minus "
                            f"{fmt_m(r.redeem_fee_m)} in assumed fees = **{fmt_m(r.redeem_cash_m)}**, "
                            f"paid {payment_time(r.redeem_day)} in the simulation."))
        else:
            st.markdown("**Redemption:** a full payment is not available under this scenario and your access settings.")
        choice, reason = exit_comparison(r)
        st.markdown(esc(f"**Cash comparison: {choice}.** {reason}"))
        st.caption("Early redemption requests can be paid at $1 even if backing later falls below par. "
                   "The model assumes you request at T+0 and your holding is small relative to the run.")

    with st.expander("Model assumptions and verdict definitions"):
        st.write("The two views are alternative strategies. Missing backing while holding and cash from an early exit "
                 "are not added together. The cash comparison selects the higher proceeds among full exits that meet your deadline.")
        st.write("The model assumes redemption at $1 less the preset fee, subject to access and simulated liquidity. "
                 "USDC's zero fee assumes a free redemption service; T+0 is a simulation result, not a guarantee "
                 "of free same-day bank settlement. Service processing times are not separately modelled.")
        st.write("Market sales use a simulated price at the start, after the initial shock. They assume immediate "
                 "execution and exclude trading fees and slippage. Cash figures use the project's dollar-equivalent holdings.")
        for verdict, meaning in issuer_results.items():
            st.markdown(f"**{verdict}:** {meaning}.")

section("Redemption terms used", "Access")
steps_list([f"**{names[pid]}:** " + redemption_terms(pid)["note"] for pid in agg])
st.caption("All coins face the same crisis at the same time (a common shock), which is the conservative view. "
           "Holdings are assumed small next to each coin's run, so they do not change it.")
