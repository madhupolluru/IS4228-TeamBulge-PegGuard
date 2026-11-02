"""The scorecard must display the cash and timing of the exit it actually selects."""

import math

import pytest
from streamlit.testing.v1 import AppTest

from pegguard.analysis import STANDARD_SCENARIOS
from pegguard.treasury import Holding, select_exit, stress_treasury, treasury_summary


def test_calm_usdt_selects_sale_and_its_proceeds():
    detail = stress_treasury([Holding("usdt_2026q2", 30)], [STANDARD_SCENARIOS[0]])
    row = detail.iloc[0]
    assert row["redeem_cash_m"] == pytest.approx(29.97)
    assert row["route"] == "sell"
    assert row["cash_m"] == pytest.approx(30)
    assert row["cash_day"] == 0
    assert row["loss_m"] == 0
    assert treasury_summary(detail, 30, 50)["cash_m"] == pytest.approx(30)


@pytest.mark.parametrize("days,route,cash,day", [(0, "sell", 29.1594464, 0), (1, "redeem", 29.97, 2)])
def test_panic_exit_respects_cash_deadline(days, route, cash, day):
    row = stress_treasury([Holding("usdt_2026q2", 30)], [STANDARD_SCENARIOS[-1]],
                          liquidity_days=days).iloc[0]
    assert row["verdict"] == "BREAKS"
    assert row["loss_m"] == pytest.approx(16.4256689)
    assert row["route"] == route
    assert row["cash_m"] == pytest.approx(cash)
    assert row["cash_day"] == day


@pytest.mark.parametrize("amount,direct", [(30, False), (0.05, True)])
def test_no_account_or_below_minimum_cannot_select_redemption(amount, direct):
    row = stress_treasury([Holding("usdt_2026q2", amount)], [STANDARD_SCENARIOS[-1]],
                          direct_access=direct).iloc[0]
    assert not row["can_redeem"]
    assert not row["redeem_in_time"]
    assert row["route"] == "sell"
    assert row["cash_m"] == pytest.approx(row["sell_cash_m"])


def test_equal_proceeds_keep_redemption_and_issuer_shortfall_is_separate():
    calm, bank = stress_treasury([Holding("usdc_2026_09", 65)],
                                [STANDARD_SCENARIOS[0], STANDARD_SCENARIOS[3]]).to_dict("records")
    assert calm["route"] == "redeem" and calm["cash_m"] == 65
    assert bank["verdict"] == "BELOW PAR" and bank["loss_m"] > 3
    assert bank["route"] == "redeem" and bank["cash_m"] == 65 and bank["cash_day"] == 1


def _outcome(**changes):
    return {"position": 30, "redeem_fee": 0.03, "exit_day": 2, "can_redeem": True,
            "sell_now_value": 29, "redeem_value": 29.97, **changes}


@pytest.mark.parametrize("exit_day", [None, float("nan")])
def test_partial_or_unknown_redemption_is_not_a_full_exit(exit_day):
    choice = select_exit(_outcome(exit_day=exit_day, redeem_value=15), 5)
    assert choice["route"] == "sell" and choice["cash_m"] == 29


@pytest.mark.parametrize("sale", [None, float("nan"), float("inf")])
def test_missing_sale_price_uses_timely_redemption_or_no_exit(sale):
    out = _outcome(sell_now_value=sale)
    assert select_exit(out, 1)["route"] == "redeem"
    unavailable = select_exit(out, 0)
    assert unavailable["route"] == "unavailable"
    assert unavailable["cash_m"] == 0 and unavailable["cash_day"] is None


def test_closed_days_are_not_removed_from_cash_deadline():
    # A day-3 payment is T+2 even when the issuer was closed before then.
    out = _outcome(exit_day=3, closed_days=2)
    assert select_exit(out, 1)["route"] == "sell"
    assert select_exit(out, 2)["route"] == "redeem"


def test_zero_sale_price_is_available_but_not_invented_as_par():
    choice = select_exit(_outcome(can_redeem=False, sell_now_value=0), 2)
    assert choice["route"] == "sell" and choice["cash_m"] == 0
    assert math.isfinite(choice["cash_m"])


def test_scorecard_renders_matching_sale_proceeds_and_deadline_context():
    at = AppTest.from_file("app.py", default_timeout=300).run()
    at.switch_page("app_pages/treasury.py").run()
    assert not at.exception

    def table(header):
        return next(m.value for m in at.markdown if f"{header}</th>" in m.value)

    def row_for(html, crisis):
        return next(row for row in html.split("<tr>") if crisis in row)

    assert [tab.label for tab in at.tabs] == ["Keep holding", "Exit at the start"]
    held = row_for(table("Backing gap on your holding"), "Crypto crash")
    assert ">BELOW PAR</span>" in held and "&#36;1.17m" in held and "&#36;0.9611" in held
    exits = table("Most cash by your deadline")
    calm_usdt = row_for(exits, "Calm: normal redemptions")
    assert "Sell on market" in calm_usdt and "&#36;30.00m" in calm_usdt and "&#36;29.97m" in calm_usdt
    crypto_usdt = row_for(exits, "Crypto crash")
    assert "&#36;29.23m" in crypto_usdt and "&#36;29.97m" in crypto_usdt and ">Redeem</b>" in crypto_usdt
    assert "&#36;1.17m" not in exits
    at.select_slider(key="tr_days").set_value(0).run()
    assert not at.exception
    panic_usdt = row_for(table("Most cash by your deadline"), "Full panic")
    assert "Sell on market" in panic_usdt and "&#36;29.16m" in panic_usdt
    assert "T+1 · after your deadline" in panic_usdt
    assert any("Your cash deadline: Same day (T+0)" in c.value for c in at.caption)
    at.selectbox(key="scorecard_coin").select("usdc_2026_09").run()
    assert not at.exception
    assert "Same cash" in row_for(table("Most cash by your deadline"), "Calm")
    at.toggle(key="tr_direct").set_value(False).run()
    assert not at.exception
    no_access = table("Most cash by your deadline")
    assert "No access or below minimum" in no_access and "Same cash" not in no_access
