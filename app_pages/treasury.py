# Treasury Planner: how should a treasury split its stablecoin float, and how much of each coin is safe?

import math
from html import escape

import pandas as pd
import streamlit as st

from app_pages.common import esc, fmt_m, page_header, section, steps_list
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
    holdings_label = ", ".join(f"{short[h.preset_id]} {fmt_m(h.amount_m)}" for h in holdings)
    st.markdown(esc(f"**Your holdings:** {holdings_label}"))
    st.caption(f"Cash deadline: {deadline}. Direct redemption accounts: {'enabled' if direct else 'disabled'}.")
    st.write("The issuer's result covers the whole crisis. Your holding shortfall and early-exit cash describe "
             "alternative choices: staying invested or exiting at the start.")

    issuer_results = {
        "HOLDS": "Backing maintained; payments on time",
        "BELOW PAR": "Backing shortfall; payments on time",
        "LIQUIDITY STRAIN": "Backing maintained; some payments late",
        "BREAKS": "Backing shortfall; some payments late",
    }

    def payment_time(day) -> str:
        if pd.isna(day):
            return "Full payment not reached in the simulation"
        day = int(day)
        return f"Day {day} ({'same day, T+0' if day == 1 else f'T+{day - 1}'})"

    html = ['<div style="overflow-x:auto"><table class="pg-table"><tr><th>Coin / crisis</th>'
            '<th>Issuer’s stress result</th><th>Reserve shortfall on your holding</th>'
            '<th>Suggested early exit</th><th>Cash from that exit</th></tr>']
    for r in detail.itertuples():
        if r.route == "redeem":
            action = f"Redeem on day 1; paid on {payment_time(r.cash_day).replace('Day ', 'day ', 1)}"
        elif r.route == "sell":
            action = "Sell immediately"
        else:
            action = "No modelled exit available"
        cash = fmt_m(r.cash_m) if r.route != "unavailable" else "Not available"
        html.append(f'<tr><td class="name">{escape(short[r.preset_id])}'
                    f'<div class="val">{escape(r.scenario)}</div></td>'
                    f'<td>{escape(issuer_results[r.verdict])}</td><td>{fmt_m(r.loss_m)}</td>'
                    f'<td>{escape(action)}</td><td>{cash}</td></tr>')
    html.append("</table></div>")
    st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)
    st.caption("Reserve shortfall = your holding × the worst reserve-backing shortfall in the scenario. "
               "It is not a guaranteed realised loss. Issuer payment deadlines come from each standard crisis; "
               "your cash deadline above determines which early exits are feasible.")
    st.caption("Redemption is requested on day 1 (T+0); day 2 is T+1. The model assumes your request is small "
               "relative to the run. Sale proceeds use the model's immediate price with no exchange fees or "
               "slippage; redemption proceeds include the issuer fee assumed by the model.")

    st.markdown("**Compare exit options**")
    selected = st.selectbox("Coin and crisis", options=list(range(len(detail))),
                            format_func=lambda i: f"{short[detail.iloc[i]['preset_id']]} — {detail.iloc[i]['scenario']}")
    row = detail.iloc[selected]
    redeem_known = bool(row["can_redeem"] and pd.notna(row["redeem_day"])
                        and math.isfinite(row["redeem_day"]) and math.isfinite(row["redeem_cash_m"]))
    sell_known = pd.notna(row["sell_cash_m"]) and math.isfinite(row["sell_cash_m"])
    options = [
        {"Exit option": "Sell immediately", "Modelled proceeds": fmt_m(row["sell_cash_m"]) if sell_known else "Not available",
         "When available": "Immediately (T+0)" if sell_known else "No modelled price",
         "Meets your cash deadline?": "Yes" if sell_known else "No"},
        {"Exit option": "Request redemption on day 1",
         "Modelled proceeds": fmt_m(row["redeem_cash_m"]) if redeem_known else "Not available",
         "When available": payment_time(row["redeem_day"]) if row["can_redeem"] else "No access or below minimum",
         "Meets your cash deadline?": "Yes" if row["redeem_in_time"] else "No"},
    ]
    st.dataframe(pd.DataFrame(options), hide_index=True, width="stretch")
    st.caption(row["exit_reason"])

section("Redemption terms used", "Access")
steps_list([f"**{names[pid]}:** " + redemption_terms(pid)["note"] for pid in agg])
st.caption("All coins face the same crisis at the same time (a common shock), which is the conservative view. "
           "Holdings are assumed small next to each coin's run, so they do not change it.")
