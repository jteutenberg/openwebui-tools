from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from pytest import approx

from household_investments import (
    CACHE_TTL,
    Holding,
    Quote,
    _closes_from_download,
    change_pct,
    day_pct,
    format_gbp,
    format_gain,
    load_holdings,
    load_quotes,
    market_value,
    missing_tickers,
    pick_movers,
    purchase_cost,
    ranked_values,
    render_investments,
    render_list,
    render_pension,
    snapshot,
    summarise_investments,
    summarise_list,
    summarise_pension,
    to_gbp,
    write_cache,
)

HERE = Path(__file__).resolve().parent.parent
NOW = datetime.fromisoformat("2026-09-19T12:00:00+01:00")

BAE = Quote("BA.L", "GBp", 2000, 1960, 1900, 1600)
TRIG = Quote("TRIG.L", "GBp", 100, 90, 95, 110)
DHL = Quote("DHL.DE", "EUR", 50, 50, 40, 40)
ABF = Quote("ABF.L", "GBp", 2000, 2010, 2000, 1800)
TP = Quote("0P00015XTB.L", "GBP", 2.00, 1.98, 1.95, 1.80)
EUR = Quote("EURGBP=X", "GBP", 0.80, 0.80, 0.80, 0.80)
USD = Quote("USDGBP=X", "GBP", 0.75, 0.75, 0.75, 0.75)

QUOTES = {
    "BA.L": BAE,
    "TRIG.L": TRIG,
    "DHL.DE": DHL,
    "ABF.L": ABF,
    "0P00015XTB.L": TP,
    "EURGBP=X": EUR,
    "USDGBP=X": USD,
}


def fetch(_tickers: list[str]) -> dict[str, Quote]:
    return QUOTES


def test_load_example_csvs():
    shares = load_holdings(HERE / "shares.csv", is_pension=False)
    pension = load_holdings(HERE / "pension.csv", is_pension=True)
    assert [(h.company, h.ticker, h.owned, h.dividends, h.is_pension, h.purchased) for h in shares] == [
        ("BAE Systems", "BA.L", 5, 0, False, 1850),
        ("DHL", "DHL.DE", 40, 100, False, 48),
    ]
    assert [(h.company, h.ticker, h.owned, h.dividends, h.is_pension, h.purchased) for h in pension] == [
        ("Associated British Foods", "ABF.L", 20, 0, True, 0),
        ("True Potential", "0P00015XTB.L", 10000, 0, True, 0),
    ]


def test_snapshot_uses_week_lookback_and_previous_year_end():
    closes = [
        (date(2025, 9, 18), 10.0),
        (date(2025, 12, 31), 11.0),
        (date(2026, 1, 5), 11.5),
        (date(2026, 9, 10), 12.0),
        (date(2026, 9, 17), 13.0),
        (date(2026, 9, 18), 14.0),
    ]
    price, prev, week, ytd = snapshot(closes, date(2026, 9, 18))
    assert (price, prev, week, ytd) == (14.0, 13.0, 12.0, 11.0)


def test_pence_and_euro_to_pounds():
    assert to_gbp(1850, "GBp", {}) == 18.50
    assert to_gbp(48, "EUR", {"EUR": 0.80}) == approx(38.40)
    assert to_gbp(10, "GBP", {}) == 10
    assert to_gbp(2.0039, "GBP", {}) == 2.0039


def test_investment_total_includes_dividends_and_excludes_pension():
    holdings = load_holdings(HERE / "shares.csv", is_pension=False)
    pensions = load_holdings(HERE / "pension.csv", is_pension=True)
    inv_market = market_value(holdings, QUOTES, "price")
    # BAE 5*£20 = 100; DHL 40*£40 = 1600; True Potential 10000*£2 = 20000
    assert inv_market == 100 + 1600
    assert sum(h.dividends for h in holdings) == 100
    assert market_value(pensions, QUOTES, "price") == 20 * 20 + 10000 * 2.00


def test_gain_is_investment_total_over_purchase_cost():
    holdings = load_holdings(HERE / "shares.csv", is_pension=False)
    cost = purchase_cost(holdings, QUOTES)
    # BAE 5*£18.50 = 92.50; DHL 40*€48*0.80 = 1536
    assert cost == approx(92.50 + 1536)
    total = market_value(holdings, QUOTES, "price") + 100
    assert format_gain(total, cost) == f"{change_pct(total, cost):+.1f}%"
    assert format_gain(100, 0) == "n/a"


def test_week_and_ytd_are_market_value_of_that_command():
    investments = load_holdings(HERE / "shares.csv", is_pension=False)
    pensions = load_holdings(HERE / "pension.csv", is_pension=True)
    assert market_value(investments, QUOTES, "price") == 100 + 1600
    assert market_value(investments, QUOTES, "week") == 5 * 19 + 40 * 32
    assert market_value(pensions, QUOTES, "ytd") == 20 * 18 + 10000 * 1.80


def test_movers_over_three_percent():
    holdings = [
        Holding("BAE Systems", "BA.L", 50, 0, False),
        Holding("TRIG", "TRIG.L", 1, 0, False),
        Holding("DHL", "DHL.DE", 1, 0, False),
    ]
    label, movers = pick_movers(holdings, QUOTES)
    assert label == "Movers"
    assert movers == [("TRIG", day_pct(TRIG))]


def test_largest_mover_when_none_hit_three_percent():
    quiet = {
        "BA.L": Quote("BA.L", "GBp", 2000, 1990, 1900, 1600),
        "ABF.L": Quote("ABF.L", "GBp", 2000, 2010, 2000, 1800),
    }
    holdings = [
        Holding("BAE Systems", "BA.L", 50, 0, False),
        Holding("Associated British Foods", "ABF.L", 200, 0, True),
    ]
    label, movers = pick_movers(holdings, quiet)
    assert label == "Largest mover"
    assert movers == [("BAE Systems", day_pct(quiet["BA.L"]))]


def test_same_ticker_is_one_mover():
    holdings = [
        Holding("BAE", "BA.L", 50, 0, False),
        Holding("BAE pension", "BA.L", 10, 0, True),
    ]
    quotes = {"BA.L": Quote("BA.L", "GBp", 110, 100, 100, 100)}
    label, movers = pick_movers(holdings, quotes)
    assert label == "Movers"
    assert movers[0][0] == "BAE"
    assert movers[0][1] == approx(10.0)


def test_render_fixed_lines():
    text = render_investments(4270, 350, "+15.0%", "Movers", [("TRIG", 4.2), ("DHL", -3.1)], 0.7, -4.2, [])
    assert text == (
        "Investments: £4,270 (includes £350 dividends)\n"
        "Gain: +15.0%\n"
        "Movers: TRIG +4.2%; DHL -3.1%\n"
        "Week: +0.7%\n"
        "YTD: -4.2%"
    )
    assert render_pension(20400, 13.7, []) == "Pension: £20,400\nYTD: +13.7%"
    assert format_gbp(0) == "£0"


def test_list_is_sorted_highest_first():
    holdings = [
        *load_holdings(HERE / "shares.csv", is_pension=False),
        *load_holdings(HERE / "pension.csv", is_pension=True),
    ]
    rows = ranked_values(holdings, QUOTES)
    assert [name for name, _value in rows] == [
        "True Potential",
        "DHL",
        "Associated British Foods",
        "BAE Systems",
    ]
    assert rows[0][1] == 20000
    assert render_list(rows, []) == (
        "True Potential £20,000\n"
        "DHL £1,600\n"
        "Associated British Foods £400\n"
        "BAE Systems £100"
    )


def test_summarise_investments_excludes_pension(tmp_path: Path):
    text = summarise_investments(HERE / "shares.csv", tmp_path / "quotes.csv", now=NOW, fetch=fetch)
    investments = load_holdings(HERE / "shares.csv", is_pension=False)
    total = 100 + 1600 + 100
    week = change_pct(market_value(investments, QUOTES, "price"), market_value(investments, QUOTES, "week"))
    ytd = change_pct(market_value(investments, QUOTES, "price"), market_value(investments, QUOTES, "ytd"))
    gain = format_gain(total, purchase_cost(investments, QUOTES))
    assert text.splitlines()[0] == f"Investments: {format_gbp(total)} (includes £100 dividends)"
    assert text.splitlines()[1] == f"Gain: {gain}"
    assert "pension" not in text.lower()
    assert "True Potential" not in text
    assert text.splitlines()[2].startswith("Largest mover: BAE Systems")
    assert text.splitlines()[3] == f"Week: {week:+.1f}%"
    assert text.splitlines()[4] == f"YTD: {ytd:+.1f}%"


def test_summarise_pension_excludes_investments(tmp_path: Path):
    text = summarise_pension(HERE / "pension.csv", tmp_path / "quotes.csv", now=NOW, fetch=fetch)
    pensions = load_holdings(HERE / "pension.csv", is_pension=True)
    total = market_value(pensions, QUOTES, "price")
    ytd = change_pct(total, market_value(pensions, QUOTES, "ytd"))
    assert text.splitlines()[0] == f"Pension: {format_gbp(total)}"
    assert text.splitlines()[1] == f"YTD: {ytd:+.1f}%"
    assert "BAE" not in text
    assert "dividends" not in text


def test_summarise_list_includes_both_files(tmp_path: Path):
    text = summarise_list(
        HERE / "shares.csv",
        HERE / "pension.csv",
        tmp_path / "quotes.csv",
        now=NOW,
        fetch=fetch,
    )
    assert text.splitlines()[0] == "True Potential £20,000"
    assert "DHL £1,600" in text
    assert "BAE Systems £100" in text


def test_cache_is_reused_within_an_hour(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, QUOTES, NOW)
    calls = {"n": 0}

    def boom(_tickers: list[str]) -> dict[str, Quote]:
        calls["n"] += 1
        raise AssertionError("should not fetch")

    quotes = load_quotes(["BA.L", "DHL.DE"], cache, NOW + timedelta(minutes=59), boom)
    assert quotes["BA.L"].price == 2000
    assert calls["n"] == 0


def test_stale_cache_is_refetched(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, {"BA.L": BAE, "EURGBP=X": EUR, "USDGBP=X": USD}, NOW)
    fresh = Quote("BA.L", "GBp", 2100, 2000, 1900, 1600)

    def refetch(_tickers: list[str]) -> dict[str, Quote]:
        return {"BA.L": fresh, "EURGBP=X": EUR, "USDGBP=X": USD}

    quotes = load_quotes(["BA.L"], cache, NOW + CACHE_TTL, refetch)
    assert quotes["BA.L"].price == 2100


def test_stale_cache_used_when_fetch_fails(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, QUOTES, NOW)

    def boom(_tickers: list[str]) -> dict[str, Quote]:
        raise RuntimeError("yahoo down")

    quotes = load_quotes(["BA.L"], cache, NOW + CACHE_TTL, boom)
    assert quotes["BA.L"].price == 2000


def test_missing_file_and_missing_ticker(tmp_path: Path):
    assert summarise_investments(tmp_path / "no.csv", tmp_path / "q.csv").startswith("Error: shares file")
    assert summarise_pension(tmp_path / "no.csv", tmp_path / "q.csv").startswith("Error: pension file")
    holdings = [Holding("Mystery", "ZZZ.L", 1, 0, False)]
    assert missing_tickers(holdings, QUOTES) == ["Mystery"]


def test_yfinance_multicolumn_close():
    index = pd.date_range("2026-09-16", periods=3, freq="D")
    columns = pd.MultiIndex.from_product([["Close", "Open"], ["BA.L", "EURGBP=X"]], names=["Price", "Ticker"])
    data = pd.DataFrame(
        [[2000.0, 0.8, 1990.0, 0.8], [2010.0, 0.8, 2000.0, 0.8], [2020.0, 0.81, 2010.0, 0.81]],
        index=index,
        columns=columns,
    )
    series = _closes_from_download(data, "ba.l")
    assert list(series) == [2000.0, 2010.0, 2020.0]


def test_fund_ticker_close_column():
    index = pd.date_range("2026-09-16", periods=3, freq="D")
    columns = pd.MultiIndex.from_product([["Close"], ["0P00015XTB.L"]], names=["Price", "Ticker"])
    data = pd.DataFrame([[1.9], [2.0], [2.1]], index=index, columns=columns)
    series = _closes_from_download(data, "0P00015XTB.L")
    assert list(series) == [1.9, 2.0, 2.1]


def test_empty_fetch_does_not_clobber_cache(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, QUOTES, NOW)

    def empty(_tickers: list[str]) -> dict[str, Quote]:
        return {}

    quotes = load_quotes(["BA.L"], cache, NOW + CACHE_TTL, empty)
    assert quotes["BA.L"].price == 2000


def test_empty_fetch_without_cache_is_an_error(tmp_path: Path):
    def empty(_tickers: list[str]) -> dict[str, Quote]:
        return {}

    text = summarise_investments(HERE / "shares.csv", tmp_path / "quotes.csv", now=NOW, fetch=empty)
    assert text.startswith("Error: could not fetch prices")
