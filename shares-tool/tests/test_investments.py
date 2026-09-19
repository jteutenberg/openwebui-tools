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
    load_holdings,
    load_quotes,
    market_value,
    missing_tickers,
    pick_movers,
    render,
    snapshot,
    summarise,
    to_gbp,
    write_cache,
)

HERE = Path(__file__).resolve().parent.parent
NOW = datetime.fromisoformat("2026-09-19T12:00:00+01:00")

BAE = Quote("BA.L", "GBp", 2000, 1960, 1900, 1600)
TRIG = Quote("TRIG.L", "GBp", 100, 90, 95, 110)
DHL = Quote("DHL.DE", "EUR", 50, 50, 40, 40)
ABF = Quote("ABF.L", "GBp", 2000, 2010, 2000, 1800)
ONT = Quote("ONT.L", "GBp", 150, 140, 140, 200)
EUR = Quote("EURGBP=X", "GBP", 0.80, 0.80, 0.80, 0.80)
USD = Quote("USDGBP=X", "GBP", 0.75, 0.75, 0.75, 0.75)

QUOTES = {
    "BA.L": BAE,
    "TRIG.L": TRIG,
    "DHL.DE": DHL,
    "ABF.L": ABF,
    "ONT.L": ONT,
    "EURGBP=X": EUR,
    "USDGBP=X": USD,
}


def test_load_example_csvs():
    shares = load_holdings(HERE / "shares.csv", is_pension=False)
    pension = load_holdings(HERE / "pension.csv", is_pension=True)
    assert [(h.company, h.ticker, h.owned, h.dividends, h.is_pension) for h in shares] == [
        ("BAE Systems", "BA.L", 50, 0, False),
        ("Renewables Infrastructure Group", "TRIG.L", 2100, 350, False),
        ("DHL", "DHL.DE", 460, 0, False),
    ]
    assert [(h.company, h.ticker, h.owned, h.dividends, h.is_pension) for h in pension] == [
        ("Associated British Foods", "ABF.L", 200, 0, True),
        ("Oxford Nanopore", "ONT.L", 2000, 0, True),
    ]


def test_snapshot_uses_last_two_closes_and_lookback():
    closes = [
        (date(2025, 9, 18), 10.0),
        (date(2026, 9, 10), 12.0),
        (date(2026, 9, 17), 13.0),
        (date(2026, 9, 18), 14.0),
    ]
    price, prev, week, year = snapshot(closes)
    assert (price, prev, week, year) == (14.0, 13.0, 12.0, 10.0)


def test_pence_and_euro_to_pounds():
    assert to_gbp(1850, "GBp", {}) == 18.50
    assert to_gbp(48, "EUR", {"EUR": 0.80}) == approx(38.40)
    assert to_gbp(10, "GBP", {}) == 10


def test_investment_total_includes_dividends_and_excludes_pension():
    holdings = load_holdings(HERE / "shares.csv", is_pension=False)
    pensions = load_holdings(HERE / "pension.csv", is_pension=True)
    inv_market = market_value(holdings, QUOTES, "price")
    # BAE 50*£20 = 1000; TRIG 2100*£1 = 2100; DHL 460*£40 = 18400
    assert inv_market == 1000 + 2100 + 18400
    assert sum(h.dividends for h in holdings) == 350
    assert market_value(pensions, QUOTES, "price") == 200 * 20 + 2000 * 1.50


def test_week_and_year_are_market_value_of_every_holding():
    holdings = [
        *load_holdings(HERE / "shares.csv", is_pension=False),
        *load_holdings(HERE / "pension.csv", is_pension=True),
    ]
    now = market_value(holdings, QUOTES, "price")
    week = market_value(holdings, QUOTES, "week")
    year = market_value(holdings, QUOTES, "year")
    # Dividends must not leak into the market totals
    assert now == 1000 + 2100 + 18400 + 4000 + 3000
    assert week == 50 * 19 + 2100 * 0.95 + 460 * 32 + 200 * 20 + 2000 * 1.40
    assert change_pct(now, week) == 100.0 * (now / week - 1)
    assert change_pct(now, year) == 100.0 * (now / year - 1)


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
    text = render(4270, 350, 6890, "Movers", [("TRIG", 4.2), ("DHL", -3.1)], 0.7, -4.2, [])
    assert text == (
        "Investments: £4,270 (includes £350 dividends)\n"
        "Investments + pension: £6,890\n"
        "Movers: TRIG +4.2%; DHL -3.1%\n"
        "Week: +0.7%\n"
        "Year: -4.2%"
    )
    assert format_gbp(0) == "£0"


def test_summarise_uses_injected_quotes(tmp_path: Path):
    def fetch(_tickers: list[str]) -> dict[str, Quote]:
        return QUOTES

    text = summarise(HERE / "shares.csv", HERE / "pension.csv", tmp_path / "quotes.csv", now=NOW, fetch=fetch)
    investments = 1000 + 2100 + 18400 + 350
    combined = investments + 4000 + 3000
    holdings = [
        *load_holdings(HERE / "shares.csv", is_pension=False),
        *load_holdings(HERE / "pension.csv", is_pension=True),
    ]
    week = change_pct(market_value(holdings, QUOTES, "price"), market_value(holdings, QUOTES, "week"))
    year = change_pct(market_value(holdings, QUOTES, "price"), market_value(holdings, QUOTES, "year"))
    assert text.splitlines()[0] == f"Investments: {format_gbp(investments)} (includes £350 dividends)"
    assert text.splitlines()[1] == f"Investments + pension: {format_gbp(combined)}"
    assert text.splitlines()[2].startswith("Movers: ")
    assert "Renewables Infrastructure Group +11.1%" in text.splitlines()[2]
    assert "Oxford Nanopore +7.1%" in text.splitlines()[2]
    assert text.splitlines()[3] == f"Week: {week:+.1f}%"
    assert text.splitlines()[4] == f"Year: {year:+.1f}%"


def test_cache_is_reused_within_an_hour(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, QUOTES, NOW)
    calls = {"n": 0}

    def fetch(_tickers: list[str]) -> dict[str, Quote]:
        calls["n"] += 1
        raise AssertionError("should not fetch")

    quotes = load_quotes(["BA.L", "DHL.DE"], cache, NOW + timedelta(minutes=59), fetch)
    assert quotes["BA.L"].price == 2000
    assert calls["n"] == 0


def test_stale_cache_is_refetched(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, {"BA.L": BAE, "EURGBP=X": EUR, "USDGBP=X": USD}, NOW)
    fresh = Quote("BA.L", "GBp", 2100, 2000, 1900, 1600)

    def fetch(_tickers: list[str]) -> dict[str, Quote]:
        return {"BA.L": fresh, "EURGBP=X": EUR, "USDGBP=X": USD}

    quotes = load_quotes(["BA.L"], cache, NOW + CACHE_TTL, fetch)
    assert quotes["BA.L"].price == 2100


def test_stale_cache_used_when_fetch_fails(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, QUOTES, NOW)

    def fetch(_tickers: list[str]) -> dict[str, Quote]:
        raise RuntimeError("yahoo down")

    quotes = load_quotes(["BA.L"], cache, NOW + CACHE_TTL, fetch)
    assert quotes["BA.L"].price == 2000


def test_missing_file_and_missing_ticker(tmp_path: Path):
    assert summarise(tmp_path / "no.csv", HERE / "pension.csv", tmp_path / "q.csv").startswith("Error: shares file")
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


def test_empty_fetch_does_not_clobber_cache(tmp_path: Path):
    cache = tmp_path / "quotes.csv"
    write_cache(cache, QUOTES, NOW)

    def fetch(_tickers: list[str]) -> dict[str, Quote]:
        return {}

    quotes = load_quotes(["BA.L"], cache, NOW + CACHE_TTL, fetch)
    assert quotes["BA.L"].price == 2000


def test_empty_fetch_without_cache_is_an_error(tmp_path: Path):
    def fetch(_tickers: list[str]) -> dict[str, Quote]:
        return {}

    text = summarise(HERE / "shares.csv", HERE / "pension.csv", tmp_path / "quotes.csv", now=NOW, fetch=fetch)
    assert text.startswith("Error: could not fetch prices")
