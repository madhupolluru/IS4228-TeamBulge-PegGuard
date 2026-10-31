# The model must agree with history on the four real runs it is validated against.


from pegguard.backtest import events, fear_premium_calibration, run_backtests, run_event


def test_every_historical_event_matches():
    bt = run_backtests()
    assert len(bt) == 4
    assert bt["Match"].all(), bt.loc[~bt["Match"], ["Event", "Model verdict"]].to_string()


def test_svb_cannot_pay_on_day_one():
    ev = next(e for e in events() if e.key == "svb_2023")
    assert run_event(ev).summary["first_breach_day"] == 1


def test_usdc_weekend_depegs_on_the_market_but_pays_at_par():
    ev = next(e for e in events() if e.key == "usdc_2023")
    s = run_event(ev).summary
    assert s["verdict"] == "HOLDS"
    assert 0.88 < s["min_market_price"] < 0.93  # observed low about $0.877; worst case 36.7/40 = $0.9175
    assert s["market_low_day"] in (0, 1, 2)  # during the closed weekend


def test_model_misses_a_few_cents_of_panic_and_says_so():
    premium = fear_premium_calibration(run_backtests())
    assert 0 < premium < 600
