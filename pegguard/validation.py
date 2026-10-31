# Sanity checks on a user-edited portfolio and scenario, so a typo cannot silently produce a confident answer.
# Each check returns (level, message) with level "error" (the result would be meaningless), "warning" (probably a
# mistake) or "info" (worth knowing). The engine still runs on warnings; the app refuses to run on errors.

from __future__ import annotations

from collections import Counter

from .engine import Scenario
from .reserves import ReserveLine

Issue = tuple[str, str]


def validate_portfolio(lines: list[ReserveLine], supply_bn: float) -> list[Issue]:
    out: list[Issue] = []
    if supply_bn is None or supply_bn <= 0:
        out.append(("error", "Tokens outstanding must be above zero."))
    if not lines:
        out.append(("error", "The portfolio has no holdings with a positive amount."))
        return out
    reserves = sum(l.amount_bn for l in lines)
    if supply_bn and supply_bn > 0:
        ratio = reserves / supply_bn
        if ratio < 0.9:
            out.append(("warning", f"Reserves are only {ratio:.0%} of tokens before any shock. Check the amounts "
                                   "and the tokens outstanding use the same unit ($bn)."))
        elif ratio > 1.5:
            out.append(("warning", f"Reserves are {ratio:.0%} of tokens. Fiat-backed coins usually hold 100-105%; "
                                   "check for a unit mistake ($m vs $bn)."))
    for l in lines:
        name = f"'{l.name}'"
        if l.maturity_years < 0:
            out.append(("error", f"{name} has a negative maturity."))
        if l.maturity_years > 30:
            out.append(("warning", f"{name} matures in {l.maturity_years:.0f} years; Treasuries go out to 30."))
        if l.yield_pct > 15 or l.yield_pct < -1:
            out.append(("warning", f"{name} has a yield of {l.yield_pct:.1f}%. Did you mean "
                                   f"{l.yield_pct / 100:.2f}%? Yields are entered in percent."))
        if l.asset_class == "tbill" and l.maturity_years > 1:
            out.append(("warning", f"{name} is a bill maturing after 1 year; bills are 1 year or less. Use "
                                   "'Treasury notes / bonds' for longer debt."))
        if l.asset_class in ("tnote", "agency_mbs") and l.maturity_years <= 1:
            out.append(("warning", f"{name} is a note/MBS with maturity of 1 year or less, so it will be priced "
                                   "like a bill."))
        if l.asset_class in ("tbill", "tnote", "agency_mbs", "commercial_paper") and l.maturity_years == 0:
            out.append(("info", f"{name} has zero maturity, so a rate shock will not change its value."))
        if l.asset_class == "cash_bank" and l.maturity_years > 0:
            out.append(("info", f"{name} is bank cash; its maturity is ignored."))
    dupes = [n for n, c in Counter(l.name for l in lines).items() if c > 1]
    if dupes:
        out.append(("info", "Several holdings share a name: " + ", ".join(dupes) + ". Results by holding may be "
                            "hard to read."))
    return out


def validate_scenario(sc: Scenario, lines: list[ReserveLine]) -> list[Issue]:
    out: list[Issue] = []
    banks = {l.bank.strip().lower() for l in lines if l.asset_class == "cash_bank" and l.bank.strip()}
    if sc.failed_bank and sc.bank_freeze_pct > 0 and sc.failed_bank.strip().lower() not in banks:
        out.append(("warning", f"No bank cash is held at '{sc.failed_bank}', so the bank failure freezes nothing. "
                               "Name the bank on a cash line in the reserve editor."))
    if sc.bank_freeze_pct > 0 and not any(l.asset_class == "cash_bank" for l in lines):
        out.append(("info", "This portfolio holds no bank cash, so the bank-failure shock has no effect."))
    if sc.frozen_release_day and sc.bank_freeze_pct <= 0:
        out.append(("info", "A release day is set but no bank cash is frozen."))
    if sc.redemption_pct > 0 and sc.horizon_days < 1:
        out.append(("error", "The run must last at least one day."))
    if sc.closed_days and sc.closed_days >= sc.horizon_days + sc.max_wait_days + 5:
        out.append(("info", "The redemption window stays closed for longer than the whole run: almost every "
                            "request will be late."))
    return out
