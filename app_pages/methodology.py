# Methodology: how the model works, every assumption, and its limits.

import math

import pandas as pd
import streamlit as st

from app_pages.common import page_header
from pegguard.reserves import ASSET_CLASSES

page_header("How it works", "Methodology",
            "A transparent, deliberately simple model. Every number below can be changed, and every result "
            "can be reproduced from the command line.")

c = st.columns(3)
cards = [
    ("01", "Reserves", "Load a real issuer's reported reserve mix (USDT, USDC) or build your own: cash, repo, "
                       "money market funds, T-bills, notes, gold, bitcoin, loans."),
    ("02", "Shock and run", "Day 0: rates jump, a bank fails, crypto crashes. Days 1-N: holders redeem and the issuer "
                            "sells assets through a liquidity waterfall, paying fire-sale discounts."),
    ("03", "Verdict", "Coverage ratio, value per token, unmet redemptions and the exact run size where the peg "
                      "breaks, translated into the loss and exit time for your own position."),
]
for col, (k, t, body) in zip(c, cards):
    col.markdown(f'<div class="pg-card"><div class="k">{k}</div><div class="t">{t}</div>'
                 f'<div class="pg-note">{body}</div></div>', unsafe_allow_html=True)
st.write("")

# each topic in its own tab, so the page is not one long wall of text
tabs = st.tabs(["Shocks", "The run", "Outputs", "Assumptions", "Limits", "Sources"])

with tabs[0]:
    st.markdown(r"""
    **Interest rates (Week 3 bond maths).** A shock $\Delta y(T)$ reprices every rate-sensitive holding instantly. It is a
    parallel move plus an optional **curve twist**: $\Delta y(T) = \Delta y_{\text{parallel}} + \text{twist} \times
    w(T)$, where $w$ rises linearly from 0 at 3 months to 1 at 10 years (positive twist = steepener).

    * Bills (maturity $\le$ 1 year) are zero-coupon: $\;P = \dfrac{1}{(1+y)^T}$, so the value factor is $\dfrac{(1+y)^T}{(1+y+\Delta y)^T}$.
    * Notes are coupon bonds bought at par (coupon = yield): $\;P=\sum_{i=1}^{n}\dfrac{c/k}{(1+y/k)^i}+\dfrac{1}{(1+y/k)^n}$, repriced at $y+\Delta y$.
    * Government money market funds keep a stable \$1.00 NAV unless the shadow NAV falls below \$0.995 ("breaking the buck").

    **Other shocks.** Gold, crypto and other investments fall by the chosen %. Secured loans take a credit loss. A bank
    failure freezes a share of the bank cash, either at every bank or only at **one named bank** (name banks in the
    reserve editor); the frozen amount is valued at the assumed recovery rate. Until its
    **release day** it cannot be used to pay redemptions (stuck). From that day the recovered share is ordinary cash again,
    while the unrecovered share stays a permanent loss. This separates cash that is only temporarily stuck from money
    that is gone.
    """)

with tabs[1]:
    st.markdown(r"""
    Base redemption requests follow a front-loaded schedule: day $d$ gets weight $\propto (1-f)^{d-1}$, scaled so the run
    adds up to the chosen % of tokens. Requests not paid on a day are carried over and paid first in, first out.

    **Redemption deadline.** Each day's requests must be paid within an acceptable wait (same day, T+1 ... T+5, the MAS
    standard). A request still waiting when its deadline passes counts as **late**. If requests are still queued when the
    run ends, the engine keeps paying (with no new requests) until the last deadline has passed.

    The issuer pays **\$1 per token** by raising cash through a **liquidity waterfall**, cheapest and fastest first:
    cash, repo, money market fund, T-bills (shortest first), notes, gold, crypto, other investments, loans.
    Each holding can sell at most *daily liquidity × its post-shock value* per day.

    **Fire-sale cost grows with size (square-root price impact).** The discount on the marginal dollar sold of an asset
    class, when $x$ has already been sold that day, is
    $\;m(x) = \min\!\big(0.95,\; s\,[\,b + I\,\sqrt{x / D}\,]\big)$, with $b$ the base discount, $I$ the impact at
    one full day's market depth $D$ and $s$ the stress multiplier. The cost of a sale is the exact integral of $m$, so
    splitting a sale into pieces never changes the answer (a property test checks this). Selling \$1bn of bills barely
    moves the price; selling \$50bn in a day does.

    **Spillover (mark-to-market).** Part of the day's price move (everything above the normal bid-ask spread $b$) also
    marks down the units *not* sold. It is a price **level** (the worst reached so far), so it does not compound day after
    day. This is how the 2022 UK gilt sell-off hit holders who had not sold.

    **Redemption window closed.** The first $N$ days can be closed (a weekend or bank holiday): requests queue, nothing
    is paid, and deadlines start when the window reopens. Interest keeps accruing on yielding reserves every day.

    **Real run paths.** Instead of a run size and front-loading, a scenario can give the exact share of supply redeemed on
    each day. The back-tests use the observed on-chain supply path.

    **Panic feedback (Diamond & Dybvig 1983).** Optional. When the signal holders watch falls below 1, each day's new
    requests are multiplied by $1 + k \times 100 \times (1 - \text{signal})$. The signal is either the true coverage (only
    insiders know it) or the observable market price, seen with an optional **information lag** (attestations are monthly). Because early redeemers are paid in full while
    reserves are short, the shortfall concentrates on the holders who stay. That first-mover advantage is what makes runs
    self-fulfilling.
    """)

with tabs[2]:
    st.markdown(r"""
    * **Coverage** = reserves (marked to market, frozen cash at recovery value) ÷ tokens outstanding, every day.
    * **Value per token** = min(1, coverage): what a token is worth if the issuer were wound down at that point.
    * **Market price** (for holders who cannot redeem): a buyer gets the reserves left after the queue ahead of them,
      $\min\!\big(1, \tfrac{R - Q}{S - Q}\big)$, with frozen cash valued at the *market's* recovery guess until it is
      released, discounted at the buyer's required return for the expected wait (closed days plus queue ÷ daily
      liquidity), less an optional fear premium while the coin is in distress. The back-tests calibrate that premium at
      about 350bp (see Validation).
    * **Verdict:** **HOLDS** (paid within the deadline, coverage ≥ 1) · **LIQUIDITY STRAIN** (solvent, but some
      redemptions miss the deadline)
      · **BELOW PAR** (paid, but coverage < 1) · **BREAKS** (both).
    * **Your position.** *If you hold:* position × worst value per token during the run. *If you redeem on day 1:*
      requests are paid first in, first out, so a day-1 request is paid in full on the first day that cumulative payments
      reach the day-1 requests, net of the issuer's fee, and only if you have an account and are above its minimum
      (Tether: \$100,000 minimum, 0.1% fee). *If you sell:* position × the model's market price. Your own request is
      assumed small next to the run.
    * **What drives it.** A tornado re-runs the scenario with each judgement call halved and raised by half, and a seeded
      Monte Carlo re-runs it a few hundred times with liquidity, fire-sale costs, impact, spillover, run size and recovery
      drawn at random, reporting how often the coin holds.
    * **Treasury Planner.** Several coins through the same five crises at once: loss on the whole treasury, cash
      raisable within a deadline, a safe cap per coin (loss budget ÷ worst haircut) and the diversification benefit.
    * **Three questions.** The Stress Lab answers: is there enough backing (coverage stays ≥ 1)? Were redemptions paid
      within the deadline? What does it mean for your position?
    * **Sensitivity.** The same scenario is re-run at fire-sale stress ×1, ×2, ×3 and ×5 (plus your own setting) and shown
      side by side.
    * **Breaking point** (a Stress Lab tab) re-runs the engine on a 21 × 21 grid of rate shocks (0-500bp) and run sizes (0-100%).
    """)

with tabs[3]:
    df = pd.DataFrame([
        {"Asset class": a.label, "Daily liquidity": a.daily_liquidity, "Base discount (bp)": a.fire_sale_bps,
         "Market depth ($bn/day)": a.market_depth_bn if math.isfinite(a.market_depth_bn) else None,
         "Impact at full depth (bp)": a.impact_bps, "Rate-sensitive": a.rate_kind != "none", "GENIUS": a.genius,
         "MAS": a.mas, "MiCA": a.mica, "HK": a.hk, "Waterfall order": a.rank, "Why": a.note}
        for a in ASSET_CLASSES.values()
    ])
    st.dataframe(df.style.format({"Daily liquidity": "{:.0%}", "Base discount (bp)": "{:.0f}",
                                  "Market depth ($bn/day)": lambda v: "no impact" if pd.isna(v) else f"{v:,.0f}",
                                  "Impact at full depth (bp)": "{:.0f}"}), width="stretch", hide_index=True)
    st.markdown(r"""
    **Where the numbers come from.** They are judgement calls, but anchored:

    * **T-bills:** about \$219bn trades per day (FINRA TRACE, Sep 2026: \$4.6tn in bills over 21 trading days). We assume a
      single seller can tap about \$150bn a day before moving the price, with 25bp of impact at that size.
    * **Notes and bonds:** about \$1.1tn a day in coupons. In the March 2020 "dash for cash", off-the-run 10-year bid-ask
      spreads went from 1-4bp to more than 40bp (ICI; New York Fed), which sets the 50bp base and 100bp impact.
    * **Agency MBS, commercial paper:** thinner markets that froze in March 2020; base 75bp, depth \$50bn and \$10bn.
    * **Gold, crypto, equities, loans:** progressively thinner; loans are close to unsellable in days.

    None of these is precise, which is why the Stress Lab's **What drives it** tab shows how much each one matters and the
    Monte Carlo varies all of them at once.
    """)
    st.caption("Every value is in pegguard/reserves.py and can be overridden per holding.")

with tabs[4]:
    st.markdown("""
    * **A simple market-price model.** The market price is what a buyer would get from the reserves, discounted for the
      wait. It does not model exchanges, arbitrage capacity or order books, and it misses the extra panic at the very
      bottom (about 350bp in the back-tests), which the fear premium can add back.
    * **No issuer capital injections, emergency borrowing or central-bank support** during the run (discretionary).
      Interest income on reserves is included.
    * **Two-factor rate shocks** (parallel plus a twist), no stochastic rates; no correlation model between shocks. Users
      choose the combination, and the Treasury Planner applies the same crisis to every coin at once.
    * **Government MMFs:** the 2023 SEC reforms exempt them from mandatory liquidity fees, and redemption gates were
      removed, so none is modelled. Circle owns nearly all of its reserve fund, so the fund's selling costs are Circle's.
    * **Reserve data** come from issuer reports (quarterly / monthly), so they are point-in-time. Presets show the as-of date.
    * **Benchmarks are not history.** Each portfolio is labelled *Reported* (built from the issuer's own report),
      *Reconstructed* (headline figures are public, the split is our estimate) or *Hypothetical* (made up for teaching).
      Results for the last two illustrate a mechanism; they are not a record of what any coin actually held.
    """)

with tabs[5]:
    st.markdown("""
    * Circle, monthly USDC examination report as of 30 September 2026; BlackRock Circle Reserve Fund factsheet (Sep 2026).
    * BDO Advisory Services, ISAE 3000R report on Tether International's financial figures as of 30 June 2026.
    * Tether consolidated reserves report as of 31 March 2022 (MHA Cayman); tether.to/fees (redemption terms).
    * SVB Financial Group 10-K (2022); Federal Reserve, *Review of the Supervision and Regulation of Silicon Valley Bank*
      (28 Apr 2023).
    * DefiLlama circulating-supply history (USDT, USDC, BUSD); Circle blog (15 Mar 2023); Cointelegraph (USDC low).
    * FINRA TRACE Treasury aggregates (Sep 2026); SIFMA Treasury statistics; ICI and New York Fed on March 2020.
    * EBA final draft RTS on the liquidity of e-money token reserves (June 2024); Davis Polk on the Hong Kong
      Stablecoins Ordinance (Aug 2025); MAS, *MAS finalises stablecoin regulatory framework* (15 Aug 2023).
    * CNBC (11 Mar 2023): *Stablecoin USDC breaks dollar peg after firm reveals it has $3.3 billion in SVB exposure.*
    * Paxos, *The GENIUS Act* (2026 summary of the July 2025 law): permitted reserve assets and 93-day bill limit.
    * Reed Smith (Aug 2023): *Singapore stablecoin regulation: MAS confirms its framework* (100% backing, 5-business-day
      par redemption, monthly attestation).
    * Diamond, D. & Dybvig, P. (1983). Bank runs, deposit insurance, and liquidity. *Journal of Political Economy* 91(3).
    * Live data: DefiLlama stablecoin and coin-price APIs (free, no key).
    """)
