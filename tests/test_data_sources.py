# Parsing of DefiLlama responses and the offline fallbacks.

from pegguard.data_sources import load_stablecoins, parse_price_chart, parse_stablecoins

SAMPLE = {"peggedAssets": [
    {"name": "Tether", "symbol": "USDT", "gecko_id": "tether", "pegType": "peggedUSD", "pegMechanism": "fiat-backed",
     "circulating": {"peggedUSD": 180e9}, "circulatingPrevWeek": {"peggedUSD": 179e9},
     "circulatingPrevMonth": {"peggedUSD": 170e9}, "price": 1.0004},
    {"name": "Euro Coin", "symbol": "EURC", "pegType": "peggedEUR", "circulating": {"peggedEUR": 1e8}},
]}


def test_parse_stablecoins_filters_usd_and_computes_gap():
    df = parse_stablecoins(SAMPLE)
    assert list(df["symbol"]) == ["USDT"]
    assert round(df.loc[0, "peg_gap_bps"], 1) == 4.0


def test_parse_price_chart():
    payload = {"coins": {"coingecko:usd-coin": {"symbol": "USDC", "prices": [
        {"timestamp": 1678500000, "price": 0.88}, {"timestamp": 1678600000, "price": 0.97}]}}}
    df = parse_price_chart(payload)
    assert len(df) == 2 and df["coin"].iloc[0] == "USDC"


def test_offline_fallbacks_always_return_data():
    df, meta = load_stablecoins(live=False)
    assert not df.empty and meta["mode"] in ("cache", "snapshot")
