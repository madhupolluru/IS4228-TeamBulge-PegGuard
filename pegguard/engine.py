# The stress engine: what happens to a fiat-backed stablecoin's reserves during a run.
#
# Day 0: shocks hit (rates, asset prices, a bank failure).
# Day 1..N: holders redeem. The issuer pays $1 per token by selling assets cheapest-first. Big sales pay a
# bigger discount and drag down the price of what is left. Unpaid requests wait in a first-in-first-out queue.
# Output: a daily table, a per-holding table and a summary with a verdict.

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Iterable

import numpy as np
import pandas as pd

from .pricing import reprice_factor
from .reserves import ReserveLine

PAR_TOL = 0.9995  # coverage below this counts as "below par"
MMF_BREAK_THE_BUCK = 0.995  # a stable-NAV money fund only loses value below this
MAX_DISCOUNT = 0.95  # a sale always raises at least 5 cents per dollar
TWIST_SHORT, TWIST_LONG = 0.25, 10.0  # a curve twist pivots from 3 months to 10 years
EPS = 1e-9


@dataclass
class Scenario:
    name: str = "Custom"
    # rates
    rate_shock_bp: float = 0.0
    curve_twist_bp: float = 0.0  # extra shock at 10y vs 3m (+ = steepener)
    # the run
    redemption_pct: float = 20.0  # % of tokens that ask to redeem over the run
    horizon_days: int = 10
    front_load: float = 0.35  # 0 = spread evenly, higher = more on day 1
    daily_requests_pct: tuple = ()  # exact % of tokens per day; overrides the three above
    closed_days: int = 0  # redemptions closed for the first N days (weekend)
    max_wait_days: int = 0  # each day's requests must be paid within this many days
    # bank failure
    bank_freeze_pct: float = 0.0
    failed_bank: str = ""  # "" = every bank
    frozen_recovery_pct: float = 100.0
    frozen_release_day: int = 0  # 0 = stays frozen
    # selling assets
    stress_multiplier: float = 1.0  # scales every fire-sale discount
    price_impact: float = 1.0  # scales the size-dependent part of the discount
    spillover: float = 0.5  # share of the price move that also hits unsold holdings
    liquidity_multiplier: float = 1.0
    class_liquidity: dict = field(default_factory=dict)  # per asset class, for sensitivity analysis
    class_fire_sale: dict = field(default_factory=dict)
    # price shocks
    gold_shock_pct: float = 0.0
    crypto_shock_pct: float = 0.0
    risk_shock_pct: float = 0.0  # equities / other investments
    loan_loss_pct: float = 0.0
    # behaviour
    panic_sensitivity: float = 0.0  # extra redemptions per 1pp below par (0.2 = +20%)
    panic_signal: str = "coverage"  # holders watch true "coverage" or the "market" price
    panic_lag_days: int = 0
    accrue_interest: bool = True
    # secondary market
    market_required_return_pct: float = 20.0  # yearly return a buyer wants while waiting to redeem
    market_frozen_recovery_pct: float = 0.0  # market's guess of frozen cash recovery
    fear_premium_bp: float = 0.0  # extra discount while the coin is in distress

    def schedule(self) -> np.ndarray:
        # share of the run arriving each day; decays by (1 - front_load) per day, sums to 1
        h = max(1, int(self.horizon_days))
        decay = 1.0 - min(max(self.front_load, 0.0), 0.95)
        w = decay ** np.arange(h)
        return w / w.sum()

    def run_path(self) -> np.ndarray:
        # new requests each day as a fraction of starting supply
        if len(self.daily_requests_pct):
            return np.clip(np.asarray(self.daily_requests_pct, dtype=float), 0, None) / 100
        return max(self.redemption_pct, 0.0) / 100 * self.schedule()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StressResult:
    daily: pd.DataFrame
    by_holding: pd.DataFrame
    summary: dict
    scenario: Scenario
    supply0: float
    reserves0: float

    @property
    def verdict(self) -> str:
        return self.summary["verdict"]


# Day-0 valuation helpers

def _price_shock(line: ReserveLine, sc: Scenario) -> float:
    key = line.spec.shock_key
    if key == "gold":
        return max(0.0, 1 + sc.gold_shock_pct / 100)
    if key == "crypto":
        return max(0.0, 1 + sc.crypto_shock_pct / 100)
    if key == "risk":
        return max(0.0, 1 + sc.risk_shock_pct / 100)
    if key == "credit":
        return max(0.0, 1 - abs(sc.loan_loss_pct) / 100)
    return 1.0


def rate_shock_at(maturity_years: float, sc: Scenario) -> float:
    # yield change (decimal) at a maturity: parallel shock plus the twist, rising linearly from 3m to 10y
    w = min(max((maturity_years - TWIST_SHORT) / (TWIST_LONG - TWIST_SHORT), 0.0), 1.0)
    return (sc.rate_shock_bp + sc.curve_twist_bp * w) / 10_000


def _rate_factor(line: ReserveLine, sc: Scenario) -> float:
    kind = line.spec.rate_kind
    if kind == "none":
        return 1.0
    f = reprice_factor("zero" if kind in ("zero", "mmf") else "coupon",
                       line.maturity_years, line.yield_pct / 100, rate_shock_at(line.maturity_years, sc))
    # a stable-NAV money fund keeps $1.00 unless it "breaks the buck"
    if kind == "mmf":
        return 1.0 if f >= MMF_BREAK_THE_BUCK else f
    return f


def _is_frozen(line: ReserveLine, sc: Scenario) -> bool:
    if line.asset_class != "cash_bank" or sc.bank_freeze_pct <= 0:
        return False
    return not sc.failed_bank or line.bank.strip().lower() == sc.failed_bank.strip().lower()


def _liquidity(line: ReserveLine, sc: Scenario) -> float:
    m = max(sc.liquidity_multiplier, 0.0) * float(sc.class_liquidity.get(line.asset_class, 1.0))
    return min(max(line.liquidity * m, 0.0), 1.0)


def _fire_sale_mult(line: ReserveLine, sc: Scenario) -> float:
    return max(sc.stress_multiplier, 0.0) * float(sc.class_fire_sale.get(line.asset_class, 1.0))


class _Market:
    # Fire-sale discount for one asset class. The discount on the next dollar sold is
    # base + impact * sqrt(sold today / market depth), capped at 95%. cost() integrates it exactly,
    # so selling in one go or in pieces gives the same answer.

    def __init__(self, base: float, impact: float, depth: float, spread: float = 0.0):
        self.b = min(max(base, 0.0), MAX_DISCOUNT)
        self.spread = min(max(spread, 0.0), self.b)  # normal bid-ask, paid on every sale but leaves no lasting move
        self.c = impact if impact > 1e-12 and math.isfinite(depth) and depth > 0 else 0.0
        self.d = depth
        # size sold today at which the discount hits the cap
        self.x_cap = (self.d * ((MAX_DISCOUNT - self.b) / self.c) ** 2) if self.c > 0 else math.inf

    def marginal(self, x: float) -> float:
        # discount on the next dollar, after x has been sold today
        if self.c == 0:
            return self.b
        return min(MAX_DISCOUNT, self.b + self.c * math.sqrt(max(x, 0.0) / self.d))

    def _total(self, x: float) -> float:
        # total discount paid selling from 0 to x
        if self.c == 0:
            return self.b * x
        xa = min(x, self.x_cap)
        g = self.b * xa + 2 * self.c / 3 * xa ** 1.5 / math.sqrt(self.d)
        return g + MAX_DISCOUNT * max(x - self.x_cap, 0.0)

    def cost(self, x0: float, s: float) -> float:
        # discount paid selling s more, when x0 was already sold today
        return self._total(x0 + s) - self._total(x0)

    def size_for(self, x0: float, need: float, avail: float) -> float:
        # smallest sale (up to avail) whose proceeds cover `need`
        if avail - self.cost(x0, avail) <= need:
            return avail
        if self.c == 0:
            return need / (1 - self.b)
        # proceeds rise and flatten as s grows, so Newton's method from below converges
        s = need
        for _ in range(60):
            f = s - self.cost(x0, s) - need
            if f >= -1e-12:
                break
            s = min(avail, s - f / max(1 - self.marginal(x0 + s), 1 - MAX_DISCOUNT))
        return s


class _Book:
    # The reserve portfolio during the run: one entry per holding.

    def __init__(self, lines: list[ReserveLine], sc: Scenario):
        self.lines = lines
        self.sc = sc
        n = len(lines)
        self.value = np.zeros(n)  # current value of what is still held (excluding frozen cash)
        self.rate_loss = np.zeros(n)
        self.price_loss = np.zeros(n)
        self.frozen = np.zeros(n)  # face value of frozen bank cash
        self.yields = np.zeros(n)  # post-shock yield, for interest
        self.sold = np.zeros(n)
        self.fire_loss = np.zeros(n)
        self.spill_loss = np.zeros(n)
        self.interest = np.zeros(n)
        self.mark = np.zeros(n)  # price markdown reached so far from fire sales
        self.recovery = min(max(sc.frozen_recovery_pct, 0), 100) / 100
        self.mkt_recovery = min(max(sc.market_frozen_recovery_pct, 0), 100) / 100
        self.released = False
        self.markets = [_Market(l.fire_sale * _fire_sale_mult(l, sc),
                                l.spec.impact_bps / 10_000 * _fire_sale_mult(l, sc) * max(sc.price_impact, 0.0),
                                l.spec.market_depth_bn, spread=l.fire_sale) for l in lines]
        # cheapest and fastest first: by asset class rank, then shortest maturity
        self.order = sorted(range(n), key=lambda i: (lines[i].spec.rank, lines[i].maturity_years))
        self.classes = sorted({l.asset_class for l in lines})
        self._shock()
        # most that can be sold of each holding per day
        self.capacity = np.array([max(self.value[i] * _liquidity(lines[i], sc), 0.0) for i in range(n)])

    def _shock(self) -> None:
        # day 0: rate and price shocks, then freeze bank cash at the failed bank(s)
        for i, l in enumerate(self.lines):
            after_rate = l.amount_bn * _rate_factor(l, self.sc)
            after_price = after_rate * _price_shock(l, self.sc)
            self.rate_loss[i] = l.amount_bn - after_rate
            self.price_loss[i] = after_rate - after_price
            self.value[i] = after_price
            if l.spec.rate_kind != "none" or l.asset_class == "repo":
                self.yields[i] = max(l.yield_pct / 100 + rate_shock_at(l.maturity_years, self.sc), 0.0)
            if _is_frozen(l, self.sc):
                self.frozen[i] = self.value[i] * min(self.sc.bank_freeze_pct, 100) / 100
                self.value[i] -= self.frozen[i]

    def frozen_value(self) -> float:
        # frozen cash counts at the assumed recovery until it is released
        return 0.0 if self.released else float(self.frozen.sum() * self.recovery)

    def frozen_market_value(self) -> float:
        # what the market thinks the frozen cash is worth
        return 0.0 if self.released else float(self.frozen.sum() * self.mkt_recovery)

    def reserves(self) -> float:
        return float(self.value.sum() + self.frozen_value())

    def liquid_next_day(self) -> float:
        return float(np.minimum(self.capacity, self.value).sum())

    def accrue(self) -> float:
        # one day of interest on yielding reserves
        accrued = self.value * self.yields / 365
        self.value += accrued
        self.interest += accrued
        return float(accrued.sum())

    def release_frozen(self) -> None:
        # the recovered part of frozen cash becomes spendable; the rest stays lost
        for i in range(len(self.lines)):
            if self.frozen[i] > 0:
                back = self.frozen[i] * self.recovery
                self.value[i] += back
                self.capacity[i] += back * _liquidity(self.lines[i], self.sc)
        self.released = True

    def sell(self, need: float) -> tuple[float, float, dict]:
        # raise `need` in cash through the waterfall; returns (still needed, discount paid, sold per class)
        loss_today = 0.0
        sold_today = dict.fromkeys(self.classes, 0.0)
        for i in self.order:
            if need <= 1e-12:
                break
            avail = min(self.capacity[i], self.value[i])
            if avail <= 1e-12:
                continue
            cls = self.lines[i].asset_class
            x0 = sold_today[cls]
            amount = self.markets[i].size_for(x0, need, avail)
            loss = self.markets[i].cost(x0, amount)
            self.value[i] -= amount
            self.sold[i] += amount
            self.fire_loss[i] += loss
            sold_today[cls] += amount
            loss_today += loss
            need -= amount - loss
        return need, loss_today, sold_today

    def spill(self, sold_today: dict) -> float:
        # heavy selling lowers the market price, which marks down what was not sold.
        # The markdown is a level (worst so far), so it never compounds day after day.
        share = min(max(self.sc.spillover, 0.0), 1.0)
        if share <= 0:
            return 0.0
        total = 0.0
        for i, l in enumerate(self.lines):
            x = sold_today[l.asset_class]
            if x <= 1e-12:
                continue
            level = share * (self.markets[i].marginal(x) - self.markets[i].spread)
            if level > self.mark[i] + 1e-12 and self.value[i] > 1e-12:
                hit = self.value[i] * (1 - (1 - level) / (1 - self.mark[i]))
                self.value[i] -= hit
                self.spill_loss[i] += hit
                total += hit
            self.mark[i] = max(self.mark[i], level)
        return total

    def table(self) -> pd.DataFrame:
        # what happened to each holding
        return pd.DataFrame({
            "Holding": [l.name for l in self.lines],
            "Asset class": [l.spec.label for l in self.lines],
            "Bank / custodian": [l.bank for l in self.lines],
            "Start ($bn)": [l.amount_bn for l in self.lines],
            "Rate loss ($bn)": self.rate_loss,
            "Price/credit loss ($bn)": self.price_loss,
            "Frozen ($bn)": self.frozen,
            "Sold ($bn)": self.sold,
            "Fire-sale loss ($bn)": self.fire_loss,
            "Spillover loss ($bn)": self.spill_loss,
            "Interest ($bn)": self.interest,
            "End ($bn)": self.value,
            "color": [l.spec.color for l in self.lines],
        })


def _market_price(book: _Book, queue: float, supply: float, closed_left: int, distressed: bool,
                  sc: Scenario) -> float:
    # price for someone who can only redeem at the back of the queue:
    # reserves left after the queue, frozen cash at the market's guess, discounted for the wait
    if supply <= EPS:
        return float("nan")
    reserves = float(book.value.sum()) + book.frozen_market_value()
    behind = supply - queue
    value = min(max((reserves - queue) / behind if behind > EPS else reserves / supply, 0.0), 1.0)
    liquid = book.liquid_next_day()
    if queue > EPS:
        wait = closed_left + (queue / liquid if liquid > EPS else 30.0)
    else:
        wait = closed_left
    r = max(sc.market_required_return_pct, 0.0) / 100
    price = value / (1 + r) ** (wait / 365)
    if distressed or value < PAR_TOL:
        price -= max(sc.fear_premium_bp, 0.0) / 10_000
    return max(price, 0.0)


def _verdict(below_par: bool, late: bool) -> tuple[str, str]:
    if below_par and late:
        return "BREAKS", "Reserves fall below par and redemptions cannot be met on time."
    if below_par:
        return "BELOW PAR", "Every redemption is paid, but reserves end up worth less than the tokens outstanding."
    if late:
        return "LIQUIDITY STRAIN", "Solvent, but the issuer cannot raise cash fast enough to pay everyone within the deadline."
    return "HOLDS", "Every redemption is paid at $1 and reserves stay at or above par."


def run_stress(lines: Iterable[ReserveLine], supply_bn: float, sc: Scenario) -> StressResult:
    lines = [l for l in lines if l.amount_bn > 0]
    supply0 = max(float(supply_bn), 0.0)
    reserves0 = float(sum(l.amount_bn for l in lines))
    book = _Book(lines, sc)  # day-0 shocks happen here

    requests = sc.run_path() * supply0  # new requests per day ($bn)
    horizon = len(requests)
    closed = max(int(sc.closed_days), 0)
    wait = max(int(sc.max_wait_days), 0)
    release_day = max(int(sc.frozen_release_day), 0)

    supply = supply0
    reserves = book.reserves()
    coverage = reserves / supply if supply > 0 else float("nan")
    price = _market_price(book, 0.0, supply, closed, closed > 0 or book.frozen.sum() > EPS, sc)
    rows = [dict(day=0, open=closed == 0, new_requests=0.0, queued_in=0.0, requested=0.0, paid=0.0, unmet=0.0,
                 fire_sale_loss=0.0, spillover_loss=0.0, interest=0.0, supply=supply, reserves=reserves,
                 liquid_next_day=book.liquid_next_day(), coverage=coverage, market_price=price, panic_multiplier=1.0)]
    cov_hist, price_hist = [coverage], [price]

    queue = 0.0
    cohorts: list[list[float]] = []  # [arrival day, amount still waiting, due day], oldest first
    total_paid = total_requested = late_amount = 0.0
    first_late_day = None

    d = 0
    # keep going after the run while requests are still queued and some deadline is still ahead
    while d < horizon or (queue > EPS and d < max((c[2] for c in cohorts), default=0)):
        d += 1
        is_open = d > closed
        accrued = book.accrue() if sc.accrue_interest else 0.0
        if release_day and d == release_day and book.frozen.sum() > 0 and not book.released:
            book.release_frozen()

        # panic: holders react to yesterday's signal (or older, with a lag)
        history = price_hist if sc.panic_signal == "market" else cov_hist
        signal = history[max(0, d - 1 - max(int(sc.panic_lag_days), 0))]
        under = max(0.0, 1.0 - signal) if np.isfinite(signal) else 0.0
        panic = 1.0 + sc.panic_sensitivity * under * 100

        new_req = min(requests[d - 1] * panic, max(supply - queue, 0.0)) if d <= horizon else 0.0
        requested = new_req + queue
        total_requested += new_req
        if new_req > 1e-12:
            # deadlines only start counting once the window reopens
            cohorts.append([d, new_req, max(d, closed + 1) + wait])

        # pay what we can (nothing while closed)
        if is_open:
            still_needed, day_loss, sold_today = book.sell(requested)
            paid = requested - max(still_needed, 0.0)
        else:
            day_loss, sold_today, paid = 0.0, dict.fromkeys(book.classes, 0.0), 0.0
        day_spill = book.spill(sold_today)
        queue = requested - paid

        # pay the oldest requests first, then flag any still waiting at their deadline
        left = paid
        for c in cohorts:
            take = min(c[1], left)
            c[1] -= take
            left -= take
        cohorts = [c for c in cohorts if c[1] > EPS]
        late_today = sum(c[1] for c in cohorts if c[2] == d)
        if late_today > EPS:
            late_amount += late_today
            first_late_day = first_late_day or d

        supply -= paid
        total_paid += paid
        reserves = book.reserves()
        coverage = reserves / supply if supply > EPS else float("nan")
        closed_left = max(closed - d, 0)
        distressed = closed_left > 0 or queue > EPS or (book.frozen.sum() > EPS and not book.released)
        price = _market_price(book, queue, supply, closed_left, distressed, sc)
        cov_hist.append(coverage)
        price_hist.append(price)
        rows.append(dict(day=d, open=is_open, new_requests=new_req, queued_in=requested - new_req,
                         requested=requested, paid=paid, unmet=queue, fire_sale_loss=day_loss,
                         spillover_loss=day_spill, interest=accrued, supply=supply, reserves=reserves,
                         liquid_next_day=book.liquid_next_day(), coverage=coverage, market_price=price,
                         panic_multiplier=panic))

    daily = pd.DataFrame(rows)
    cov = daily["coverage"].replace([np.inf, -np.inf], np.nan)
    min_cov = float(cov.min()) if cov.notna().any() else float("nan")
    final_cov = float(cov.dropna().iloc[-1]) if cov.notna().any() else float("nan")
    mkt = daily["market_price"]
    has_price = mkt.notna().any()
    verdict, headline = _verdict(min_cov < PAR_TOL, first_late_day is not None)
    frozen_total = float(book.frozen.sum())
    writedown = frozen_total * (1 - book.recovery)

    summary = dict(
        verdict=verdict,
        headline=headline,
        start_coverage=reserves0 / supply0 if supply0 > 0 else float("nan"),
        day0_coverage=float(daily.loc[0, "coverage"]),
        min_coverage=min_cov,
        final_coverage=final_cov,
        implied_token_value=min(1.0, final_cov) if np.isfinite(final_cov) else float("nan"),
        min_token_value=min(1.0, min_cov) if np.isfinite(min_cov) else float("nan"),
        min_market_price=float(mkt.min()) if has_price else float("nan"),
        market_low_day=int(daily.loc[mkt.idxmin(), "day"]) if has_price else None,
        rate_loss=float(book.rate_loss.sum()),
        price_loss=float(book.price_loss.sum()),
        frozen=frozen_total,
        frozen_writedown=writedown,
        fire_sale_loss=float(book.fire_loss.sum()),
        spillover_loss=float(book.spill_loss.sum()),
        interest_income=float(book.interest.sum()),
        total_loss=float(book.rate_loss.sum() + book.price_loss.sum() + writedown + book.fire_loss.sum()
                         + book.spill_loss.sum()),
        requested=total_requested,
        paid=total_paid,
        unmet=float(queue),
        first_breach_day=first_late_day,
        late_amount=late_amount,  # $bn still unpaid when their deadline passed
        max_wait_days=wait,
        closed_days=closed,
        frozen_released_day=release_day if book.released else None,
        days_simulated=d,
        # loss pushed onto holders who stayed (Diamond & Dybvig first-mover advantage)
        stayers_loss=max(0.0, (1 - final_cov) * supply) if np.isfinite(final_cov) else 0.0,
        supply_end=supply,
        reserves_end=reserves,
    )
    return StressResult(daily=daily, by_holding=book.table(), summary=summary, scenario=sc,
                        supply0=supply0, reserves0=reserves0)


def redemption_capacity(lines: Iterable[ReserveLine], days: int = 5, stress_multiplier: float = 1.0,
                        sc: Scenario | None = None) -> float:
    # cash ($bn) the issuer could raise within `days`, after day-0 shocks, at the base discount (orderly market)
    sc = sc or Scenario(redemption_pct=0)
    stressed = replace(sc, stress_multiplier=stress_multiplier)
    total = 0.0
    for l in lines:
        v = l.amount_bn * _rate_factor(l, sc) * _price_shock(l, sc)
        if _is_frozen(l, sc):
            v *= 1 - min(sc.bank_freeze_pct, 100) / 100
        sellable = min(v, v * _liquidity(l, sc) * days)
        total += sellable * (1 - min(l.fire_sale * _fire_sale_mult(l, stressed), MAX_DISCOUNT))
    return total
