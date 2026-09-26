"""
title: Household Investments
author: Family
description: Use how_are_investments for shares, savings, or the stock market. Use how_is_pension for pension. Use list_shares for a breakdown of every holding.
required_open_webui_version: 0.6.0
requirements: yfinance
version: 0.2.0
license: MIT
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

UK_TZ = ZoneInfo("Europe/London")
CACHE_TTL = timedelta(hours=1)
MOVER_PCT = 3.0
MAX_LINES = 50
FX_TICKERS = ("EURGBP=X", "USDGBP=X")
PENCE = frozenset({"GBp", "GBX"})

FetchFn = Callable[[list[str]], dict[str, "Quote"]]


def _default_path(name: str) -> str:
    return str(Path(__file__).resolve().parent / name)


@dataclass(frozen=True)
class Holding:
    company: str
    ticker: str
    owned: float
    dividends: float
    is_pension: bool
    purchased: float = 0.0


@dataclass(frozen=True)
class Quote:
    ticker: str
    currency: str
    price: float
    prev: float
    week: float
    ytd: float


def now_uk(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(UK_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=UK_TZ)
    return now.astimezone(UK_TZ)


def load_holdings(path: Path, is_pension: bool) -> list[Holding]:
    rows: list[Holding] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            company = (row.get("company") or "").strip()
            ticker = (row.get("ticker") or "").strip()
            if not company or not ticker:
                continue
            owned = float(row.get("owned") or 0)
            dividends = 0.0 if is_pension else float(row.get("dividends") or 0)
            purchased = 0.0 if is_pension else float(row.get("purchased") or 0)
            rows.append(Holding(company, ticker, owned, dividends, is_pension, purchased))
    return rows


def snapshot(closes: list[tuple[date, float]], as_of: date) -> Optional[tuple[float, float, float, float]]:
    if not closes:
        return None
    closes = sorted(closes, key=lambda item: item[0])
    price = closes[-1][1]
    prev = closes[-2][1] if len(closes) >= 2 else price
    last = closes[-1][0]
    year_end = date(as_of.year, 1, 1) - timedelta(days=1)
    return price, prev, _on_or_before(closes, last - timedelta(days=7)), _on_or_before(closes, year_end)


def _on_or_before(closes: list[tuple[date, float]], target: date) -> float:
    for day, price in reversed(closes):
        if day <= target:
            return price
    return closes[0][1]


def day_pct(quote: Quote) -> float:
    if quote.prev == 0:
        return 0.0
    return 100.0 * (quote.price / quote.prev - 1.0)


def to_gbp(price: float, currency: str, fx: dict[str, float]) -> float:
    if currency in PENCE:
        return price * 0.01
    if currency == "GBP":
        return price
    rate = fx.get(currency)
    if rate is None:
        raise ValueError(currency)
    return price * rate


def fx_rates(quotes: dict[str, Quote], field: str) -> dict[str, float]:
    rates: dict[str, float] = {}
    for ticker, quote in quotes.items():
        if ticker.endswith("GBP=X"):
            rates[ticker[: -len("GBP=X")]] = getattr(quote, field)
    return rates


def market_value(holdings: list[Holding], quotes: dict[str, Quote], field: str) -> float:
    rates = fx_rates(quotes, field)
    total = 0.0
    for holding in holdings:
        quote = quotes.get(holding.ticker)
        if quote is None:
            continue
        try:
            total += holding.owned * to_gbp(getattr(quote, field), quote.currency, rates)
        except ValueError:
            continue
    return total


def purchase_cost(holdings: list[Holding], quotes: dict[str, Quote]) -> float:
    rates = fx_rates(quotes, "price")
    total = 0.0
    for holding in holdings:
        if holding.purchased == 0:
            continue
        quote = quotes.get(holding.ticker)
        if quote is None:
            continue
        try:
            total += holding.owned * to_gbp(holding.purchased, quote.currency, rates)
        except ValueError:
            continue
    return total


def missing_tickers(holdings: list[Holding], quotes: dict[str, Quote]) -> list[str]:
    rates_now = fx_rates(quotes, "price")
    missing: list[str] = []
    seen: set[str] = set()
    for holding in holdings:
        if holding.ticker in seen:
            continue
        seen.add(holding.ticker)
        quote = quotes.get(holding.ticker)
        if quote is None:
            missing.append(holding.company)
            continue
        try:
            to_gbp(quote.price, quote.currency, rates_now)
        except ValueError:
            missing.append(holding.company)
    return missing


def pick_movers(holdings: list[Holding], quotes: dict[str, Quote]) -> tuple[str, list[tuple[str, float]]]:
    seen: set[str] = set()
    scored: list[tuple[str, float]] = []
    for holding in holdings:
        if holding.ticker in seen or holding.ticker not in quotes:
            continue
        seen.add(holding.ticker)
        scored.append((holding.company, day_pct(quotes[holding.ticker])))
    big = [item for item in scored if abs(item[1]) >= MOVER_PCT]
    if big:
        big.sort(key=lambda item: -abs(item[1]))
        return "Movers", big
    if not scored:
        return "Movers", []
    return "Largest mover", [max(scored, key=lambda item: abs(item[1]))]


def ranked_values(holdings: list[Holding], quotes: dict[str, Quote]) -> list[tuple[str, float]]:
    rates = fx_rates(quotes, "price")
    rows: list[tuple[str, float]] = []
    for holding in holdings:
        quote = quotes.get(holding.ticker)
        if quote is None:
            continue
        try:
            value = holding.owned * to_gbp(quote.price, quote.currency, rates)
        except ValueError:
            continue
        rows.append((holding.company, value))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return rows


def format_gbp(amount: float) -> str:
    return f"£{amount:,.0f}"


def format_pct(pct: float) -> str:
    return f"{pct:+.1f}%"


def change_pct(now: float, then: float) -> float:
    if then == 0:
        return 0.0
    return 100.0 * (now / then - 1.0)


def format_gain(total: float, cost: float) -> str:
    if cost == 0:
        return "n/a"
    return format_pct(change_pct(total, cost))


def with_missing(lines: list[str], missing: list[str]) -> str:
    if missing:
        lines.append("Missing: " + ", ".join(missing))
    return "\n".join(lines)


def render_investments(
    total: float,
    dividends: float,
    gain: str,
    mover_label: str,
    movers: list[tuple[str, float]],
    week: float,
    ytd: float,
    missing: list[str],
) -> str:
    bits = "; ".join(f"{name} {format_pct(pct)}" for name, pct in movers)
    return with_missing(
        [
            f"Investments: {format_gbp(total)} (includes {format_gbp(dividends)} dividends)",
            f"Gain: {gain}",
            f"{mover_label}: {bits or 'none'}",
            f"Week: {format_pct(week)}",
            f"YTD: {format_pct(ytd)}",
        ],
        missing,
    )


def render_pension(total: float, ytd: float, missing: list[str]) -> str:
    return with_missing(
        [
            f"Pension: {format_gbp(total)}",
            f"YTD: {format_pct(ytd)}",
        ],
        missing,
    )


def render_list(rows: list[tuple[str, float]], missing: list[str]) -> str:
    if not rows and not missing:
        return "No holdings."
    lines = [f"{name} {format_gbp(value)}" for name, value in rows]
    if len(lines) > MAX_LINES:
        hidden = len(lines) - MAX_LINES
        lines = lines[:MAX_LINES] + [f"({hidden} more)"]
    return with_missing(lines, missing)


def cache_age(fetched_at: datetime, now: datetime) -> timedelta:
    return now_uk(now) - now_uk(fetched_at)


def read_cache(path: Path) -> Optional[tuple[datetime, dict[str, Quote]]]:
    if not path.is_file():
        return None
    quotes: dict[str, Quote] = {}
    fetched_at: Optional[datetime] = None
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            when = datetime.fromisoformat(row["fetched_at"])
            fetched_at = when if fetched_at is None else min(fetched_at, when)
            ticker = row["ticker"]
            quotes[ticker] = Quote(
                ticker=ticker,
                currency=row["currency"],
                price=float(row["price"]),
                prev=float(row["prev"]),
                week=float(row["week"]),
                ytd=float(row.get("ytd") or row.get("year") or 0),
            )
    if fetched_at is None:
        return None
    return fetched_at, quotes


def write_cache(path: Path, quotes: dict[str, Quote], fetched_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fieldnames = ["fetched_at", "ticker", "currency", "price", "prev", "week", "ytd"]
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        iso = fetched_at.isoformat()
        for quote in quotes.values():
            writer.writerow(
                {
                    "fetched_at": iso,
                    "ticker": quote.ticker,
                    "currency": quote.currency,
                    "price": quote.price,
                    "prev": quote.prev,
                    "week": quote.week,
                    "ytd": quote.ytd,
                }
            )
    tmp.replace(path)


def _closes_from_download(data, ticker: str):
    if data is None or getattr(data, "empty", True):
        return None
    ticker = ticker.upper()
    if getattr(data.columns, "nlevels", 1) > 1:
        try:
            close = data["Close"]
        except Exception:
            return None
        if ticker in close.columns:
            return close[ticker]
        return None
    if "Close" in data.columns:
        return data["Close"]
    return None


def _pairs(series) -> list[tuple[date, float]]:
    pairs: list[tuple[date, float]] = []
    for index, value in series.dropna().items():
        day = index.date() if hasattr(index, "date") else index
        pairs.append((day, float(value)))
    return pairs


def _currency(ticker: str) -> str:
    import yfinance as yf

    info = yf.Ticker(ticker).fast_info
    if hasattr(info, "get"):
        currency = info.get("currency")
    else:
        currency = getattr(info, "currency", None)
    return str(currency or "GBP")


def fetch_quotes(tickers: list[str]) -> dict[str, Quote]:
    import yfinance as yf

    as_of = now_uk().date()
    wanted = list(dict.fromkeys([*tickers, *FX_TICKERS]))
    data = yf.download(
        wanted,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
        repair=False,
    )
    quotes: dict[str, Quote] = {}
    extra_fx: list[str] = []
    for ticker in wanted:
        series = _closes_from_download(data, ticker)
        if series is None:
            continue
        points = snapshot(_pairs(series), as_of)
        if points is None:
            continue
        currency = "GBP" if ticker.endswith("=X") else _currency(ticker)
        quotes[ticker] = Quote(ticker, currency, *points)
        if currency not in PENCE | {"GBP"} and f"{currency}GBP=X" not in quotes:
            extra_fx.append(f"{currency}GBP=X")

    extra_fx = [ticker for ticker in dict.fromkeys(extra_fx) if ticker not in quotes]
    if extra_fx:
        extra = yf.download(
            extra_fx,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            repair=False,
        )
        for ticker in extra_fx:
            series = _closes_from_download(extra, ticker)
            if series is None:
                continue
            points = snapshot(_pairs(series), as_of)
            if points is None:
                continue
            quotes[ticker] = Quote(ticker, "GBP", *points)
    return quotes


def load_quotes(
    tickers: list[str],
    cache_path: Path,
    now: datetime,
    fetch: FetchFn,
) -> dict[str, Quote]:
    if not tickers:
        return {}
    cached = read_cache(cache_path)
    needed = set(tickers) | set(FX_TICKERS)
    if cached is not None:
        fetched_at, quotes = cached
        if cache_age(fetched_at, now) < CACHE_TTL and needed <= quotes.keys():
            return quotes
    try:
        quotes = fetch(tickers)
        if not any(ticker in quotes for ticker in tickers):
            raise RuntimeError("no prices")
    except Exception:
        if cached is not None:
            return cached[1]
        raise
    write_cache(cache_path, quotes, now)
    return quotes


def _needed_quotes(
    holdings: list[Holding],
    cache_path: Path,
    now: datetime,
    fetch: Optional[FetchFn],
) -> tuple[Optional[dict[str, Quote]], str]:
    try:
        return load_quotes(
            list(dict.fromkeys(holding.ticker for holding in holdings)),
            cache_path,
            now,
            fetch or fetch_quotes,
        ), ""
    except Exception as exc:
        return None, f"Error: could not fetch prices ({exc})."


def _read_file(path: Path, is_pension: bool) -> tuple[Optional[list[Holding]], str]:
    kind = "pension" if is_pension else "shares"
    if not path.is_file():
        return None, f"Error: {kind} file '{path}' not found."
    return load_holdings(path, is_pension), ""


def summarise_investments(
    shares_path: Path,
    cache_path: Path,
    now: Optional[datetime] = None,
    fetch: Optional[FetchFn] = None,
) -> str:
    now = now_uk(now)
    investments, error = _read_file(shares_path, False)
    if error:
        return error
    quotes, error = _needed_quotes(investments, cache_path, now, fetch)
    if error:
        return error
    dividends = sum(holding.dividends for holding in investments)
    total = market_value(investments, quotes, "price") + dividends
    mover_label, movers = pick_movers(investments, quotes)
    return render_investments(
        total,
        dividends,
        format_gain(total, purchase_cost(investments, quotes)),
        mover_label,
        movers,
        change_pct(market_value(investments, quotes, "price"), market_value(investments, quotes, "week")),
        change_pct(market_value(investments, quotes, "price"), market_value(investments, quotes, "ytd")),
        missing_tickers(investments, quotes),
    )


def summarise_pension(
    pension_path: Path,
    cache_path: Path,
    now: Optional[datetime] = None,
    fetch: Optional[FetchFn] = None,
) -> str:
    now = now_uk(now)
    pensions, error = _read_file(pension_path, True)
    if error:
        return error
    quotes, error = _needed_quotes(pensions, cache_path, now, fetch)
    if error:
        return error
    total = market_value(pensions, quotes, "price")
    return render_pension(
        total,
        change_pct(total, market_value(pensions, quotes, "ytd")),
        missing_tickers(pensions, quotes),
    )


def summarise_list(
    shares_path: Path,
    pension_path: Path,
    cache_path: Path,
    now: Optional[datetime] = None,
    fetch: Optional[FetchFn] = None,
) -> str:
    now = now_uk(now)
    investments, error = _read_file(shares_path, False)
    if error:
        return error
    pensions, error = _read_file(pension_path, True)
    if error:
        return error
    holdings = [*investments, *pensions]
    quotes, error = _needed_quotes(holdings, cache_path, now, fetch)
    if error:
        return error
    return render_list(ranked_values(holdings, quotes), missing_tickers(holdings, quotes))


class Tools:
    class Valves(BaseModel):
        shares_csv: str = Field(default=_default_path("shares.csv"), description="Path to investments CSV")
        pension_csv: str = Field(default=_default_path("pension.csv"), description="Path to pension CSV")
        cache_csv: str = Field(default=_default_path("quotes.csv"), description="Path to cached Yahoo quotes CSV")

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def how_are_investments(self) -> str:
        """
        Summarise shares (not pension): total, gain, movers, week, YTD. Use for investments, shares, savings, or the stock market. Call with no arguments.
        """
        return summarise_investments(Path(self.valves.shares_csv), Path(self.valves.cache_csv))

    async def how_is_pension(self) -> str:
        """
        Summarise pension total and YTD. Use when the user asks about pension. Call with no arguments.
        """
        return summarise_pension(Path(self.valves.pension_csv), Path(self.valves.cache_csv))

    async def list_shares(self) -> str:
        """
        List every holding and its current value, highest first. Use for a breakdown or what is held. Call with no arguments.
        """
        return summarise_list(
            Path(self.valves.shares_csv),
            Path(self.valves.pension_csv),
            Path(self.valves.cache_csv),
        )
