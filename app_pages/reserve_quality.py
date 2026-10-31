# Issuer Profile: data freshness, rate risk, liquidity ladder, bank concentration, redemption terms
# and simplified GENIUS / MAS / MiCA / Hong Kong reserve checks.

from datetime import date

import pandas as pd
import streamlit as st

from app_pages.common import (basis_notice, current_portfolio, esc, fmt_bn, fmt_bp, holder_access, page_header,
                              section)
from pegguard import charts
from pegguard.analysis import REGIMES, bank_concentration, regulatory_check, risk_profile
from pegguard.pricing import modified_duration

page_header("Due diligence", "Issuer Profile",
            "Four questions to ask before holding a stablecoin: how fresh is the reserve data, how rate-sensitive "
            "are the reserves, how fast can they become cash, and are they the assets regulators allow?")

STALE_DAYS = 92  # older than about one quarter of attestations


def data_age(as_of: str, edited: bool) -> tuple[str, str | None]:
    # Days since the reserve snapshot, with a warning when it is older than a quarter.
    if edited:
        return "Edited", None
    try:
        days = (date.today() - date.fromisoformat(as_of)).days
    except ValueError:
        return "n/a", None  # hypothetical portfolios have no report date
    return f"{days:,} days", ("stale" if days > STALE_DAYS else None)

lines, supply = current_portfolio()
reserves = sum(l.amount_bn for l in lines)
rp = risk_profile(lines, supply)

meta = st.session_state.portfolio_meta
basis_notice(meta)
age, stale = data_age(meta.get("as_of", ""), meta.get("edited", False))
k = st.columns(5)
k[0].metric("Reserve data age", age, delta=stale, delta_color="inverse" if stale else "off",
            help=f"Days since the reserve report this portfolio is built from (as of {meta.get('as_of', '')}). "
                 "Check for a newer attestation before relying on the numbers.")
k[1].metric("Buffer above par", fmt_bp(reserves / supply - 1) if supply else "n/a",
            help=f"Reserves minus tokens = {fmt_bn(reserves - supply, 2)}: the loss the issuer can absorb before "
                 "the peg is at risk")
k[2].metric("Loss per +100bp", f"{rp['loss_per_100bp_pct'] * 100:,.0f}bp",
            help=f"About {fmt_bn(rp['dv01_bn'] * 100)} of reserve value lost if all yields rise 1 percentage point")
k[3].metric("Modified duration", f"{rp['duration']:.2f} yrs",
            help=f"Weighted average maturity of rate-sensitive assets: {rp['wam_days']:,.0f} days")
k[4].metric("Cash in 5 days", f"{rp['day5_capacity_share']:.0%}",
            help="Share of tokens that could be paid within 5 business days (the MAS redemption window)")

st.divider()
c1, c2 = st.columns([1.1, 1], gap="large")
with c1:
    st.plotly_chart(charts.liquidity_ladder(rp["ladder"]), width="stretch", config={"displayModeBar": False})
    st.caption("Assumes orderly markets (no shocks, stress x1). The MAS framework requires par redemption within "
               "5 business days of a request.")
with c2:
    section("Rate risk by holding")
    # only holdings whose value moves with interest rates
    rows = []
    for l in lines:
        kind = l.spec.rate_kind
        if kind == "none":
            continue
        d = modified_duration("zero" if kind in ("zero", "mmf") else "coupon", l.maturity_years, l.yield_pct / 100)
        rows.append({"Holding": l.name, "$bn": l.amount_bn, "Duration": d, "+100bp loss": l.amount_bn * d / 100})
    if rows:
        st.dataframe(pd.DataFrame(rows).style.format({"$bn": "{:,.2f}", "Duration": "{:.2f}y",
                                                      "+100bp loss": "${:,.3f}bn"}),
                     width="stretch", hide_index=True, height=35 * (len(rows) + 1) + 3,
                     column_config={"Holding": st.column_config.TextColumn(width="medium")})
        st.caption("Cash, gold, crypto, equities and loans do not reprice with interest rates and are left out.")
    else:
        st.caption("None of these reserves reprice with interest rates.")

st.divider()
conc = bank_concentration(lines)
access = holder_access()
# each column has its own heading so the two line up
q1, q2 = st.columns([1, 1], gap="large")
with q1:
    section("Where the cash sits", "Concentration")
    if conc.empty:
        st.caption("No bank cash in this portfolio.")
    else:
        st.dataframe(conc.style.format({"Amount ($bn)": "{:,.3f}", "Share of reserves": "{:.1%}"}),
                     hide_index=True, width="stretch", height=35 * (len(conc) + 1) + 3)
        if (conc["Bank"] == "Unspecified").all():
            st.caption("The issuer does not name its banks. Add names in the Stress Lab's reserve editor to test a "
                       "single-bank failure and the MiCA per-bank limit.")
        else:
            top = conc.iloc[0]
            st.caption(esc(f"Largest single bank: {top['Bank']} with {top['Share of reserves']:.1%} of reserves. "
                           "USDC had 8% of its reserves at Silicon Valley Bank in March 2023."))
with q2:
    section("Redemption access", "Access")
    minimum = f"${access['min_m']:g}m" if access["min_m"] else "none published"
    note = access["note"].replace("$", "&#36;")
    st.markdown(f'<div class="pg-note">{note}</div>', unsafe_allow_html=True)
    st.markdown(esc(f"**Minimum:** {minimum} · **Fee:** {access['fee_bp']:g}bp · **Your direct account:** "
                    f"{'yes' if access['direct'] else 'no'}"))

st.divider()
section("Reserve eligibility (simplified)", "Regulation")
significant = st.toggle("Treat as a 'significant' token under MiCA", value=False,
                        help="Significant e-money tokens must keep at least 60% of reserves in bank deposits, not 30%.")
rc = regulatory_check(lines, supply, significant=significant)
st.write("")

# one row per regime: eligible share, pass or fail, and the reason for a fail
CHIP = {True: ("Passes", "#13804A"), False: ("Fails", "#C53030")}
html = ['<table class="pg-table"><tr><th>Regime</th><th>Eligible reserves / tokens</th><th>Result</th>'
        '<th>Why</th></tr>']
for key in REGIMES:
    res = rc[key]
    label, color = CHIP[res["passes"]]
    failed = [f"Needs: {c['Check'].lower()}. Has: {c['Detail']}" for c in res["checks"] if c["Passes"] is False]
    untested = [c["Detail"] for c in res["checks"] if c["Passes"] is None]
    why = "<br>".join(failed) or ("All checks pass" + (f". {untested[0]}" if untested else ""))
    html.append(f'<tr><td class="name">{res["title"]}</td><td>{res["eligible_backing"]:.1%}</td>'
                f'<td><span class="pg-chip" style="color:{color}; background:{color}14; border:1px solid {color}33">'
                f'{label}</span></td><td class="why">{why}</td></tr>')
html.append("</table>")
st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)

with st.expander("Rules and holding-by-holding detail", icon=":material/list:"):
    tabs = st.tabs([rc[k]["title"] for k in REGIMES])
    for tab, key in zip(tabs, REGIMES):
        res = rc[key]
        det = res["detail"].copy()
        det["Eligible"] = det["Eligible"].map({True: "Yes", False: "No"})
        with tab:
            st.caption(res["rule"])
            st.dataframe(det.rename(columns={"Amount ($bn)": "$bn"}).style.format({"$bn": "{:,.3f}"}),
                         width="stretch", hide_index=True,
                         column_config={"Holding": st.column_config.TextColumn(width="medium"),
                                        "Reason": st.column_config.TextColumn(width="medium")})
st.caption("Educational simplification of the rules, not legal advice. MiCA's per-bank cap (25%) is the EBA draft "
           "standard's limit for large banks; smaller banks have lower caps.")
