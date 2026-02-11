#!/usr/bin/env python3
"""
Scan tickers on a 15-minute timeframe and check Fibonacci 38.2% hits.

Input format:
- Plain text file
- One ticker per line
- Empty lines and lines that start with # are ignored
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


YAHOO_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass
class Candle:
    timestamp: dt.datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float


@dataclass
class ScanResult:
    symbol: str
    trend: str | None = None
    change_pct: float | None = None
    last_close: float | None = None
    fib_382: float | None = None
    fib_hit: bool | None = None
    last_candle_time: dt.datetime | None = None
    note: str = ""
    error: str | None = None


def read_tickers(input_path: Path) -> list[str]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    tickers: list[str] = []
    for raw_line in input_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().upper()
        if not line or line.startswith("#"):
            continue
        tickers.append(line)

    if not tickers:
        raise ValueError(f"No tickers found in {input_path}")

    return tickers


def _value_at(values: list[float | None], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_candles(symbol: str, interval: str = "15m", range_window: str = "5d") -> list[Candle]:
    params = urllib.parse.urlencode(
        {
            "interval": interval,
            "range": range_window,
            "includePrePost": "false",
            "events": "div,splits",
        }
    )
    symbol_encoded = urllib.parse.quote(symbol)
    url = f"{YAHOO_CHART_ENDPOINT.format(symbol=symbol_encoded)}?{params}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (stock-scanner/1.0)"},
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        description = error.get("description") or "Unknown Yahoo Finance API error"
        raise RuntimeError(description)

    results = chart.get("result") or []
    if not results:
        raise RuntimeError("No chart result returned from Yahoo Finance API.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []

    candles: list[Candle] = []
    for i, timestamp in enumerate(timestamps):
        open_price = _value_at(opens, i)
        high_price = _value_at(highs, i)
        low_price = _value_at(lows, i)
        close_price = _value_at(closes, i)
        if None in (open_price, high_price, low_price, close_price):
            continue

        candles.append(
            Candle(
                timestamp=dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc),
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                close_price=close_price,
            )
        )

    if not candles:
        raise RuntimeError("No complete OHLC candles returned for symbol.")

    return candles


def determine_trend(candles: list[Candle], lookback_bars: int = 8) -> tuple[str, float]:
    """
    Determine trend from 15-minute candles.

    lookback_bars=8 means trend based on the last 2 hours (8 x 15m).
    """
    if lookback_bars < 1:
        raise ValueError("lookback_bars must be >= 1")

    if len(candles) < lookback_bars + 1:
        raise RuntimeError(
            f"Need at least {lookback_bars + 1} candles, received {len(candles)}."
        )

    start_close = candles[-(lookback_bars + 1)].close_price
    end_close = candles[-1].close_price
    if start_close == 0:
        raise RuntimeError("Cannot determine trend from zero starting close.")

    change_pct = ((end_close - start_close) / start_close) * 100
    sideways_threshold_pct = 0.1

    if change_pct > sideways_threshold_pct:
        return "UP", change_pct
    if change_pct < -sideways_threshold_pct:
        return "DOWN", change_pct
    return "SIDEWAYS", change_pct


def fibonacci_382_status(
    candles: list[Candle],
    trend: str,
    swing_lookback_bars: int = 26,
) -> tuple[float, bool, str]:
    if swing_lookback_bars < 2:
        raise ValueError("swing_lookback_bars must be >= 2")

    window = candles[-swing_lookback_bars:] if len(candles) >= swing_lookback_bars else candles
    swing_high = max(c.high_price for c in window)
    swing_low = min(c.low_price for c in window)
    span = swing_high - swing_low
    if span <= 0:
        raise RuntimeError("Cannot calculate Fibonacci level because high == low.")

    latest = candles[-1]
    fib_from_uptrend = swing_high - (0.382 * span)
    fib_from_downtrend = swing_low + (0.382 * span)

    if trend == "UP":
        fib_level = fib_from_uptrend
        note = "38.2% retracement from recent up-swing"
    elif trend == "DOWN":
        fib_level = fib_from_downtrend
        note = "38.2% retracement from recent down-swing"
    else:
        if abs(latest.close_price - fib_from_uptrend) <= abs(latest.close_price - fib_from_downtrend):
            fib_level = fib_from_uptrend
            note = "sideways: nearest 38.2% level (up-swing basis)"
        else:
            fib_level = fib_from_downtrend
            note = "sideways: nearest 38.2% level (down-swing basis)"

    fib_hit = latest.low_price <= fib_level <= latest.high_price
    return fib_level, fib_hit, note


def scan_symbol(
    symbol: str,
    trend_lookback_bars: int,
    swing_lookback_bars: int,
) -> ScanResult:
    try:
        candles = fetch_candles(symbol, interval="15m", range_window="5d")
        trend, change_pct = determine_trend(candles, lookback_bars=trend_lookback_bars)
        fib_382, fib_hit, note = fibonacci_382_status(
            candles,
            trend,
            swing_lookback_bars=swing_lookback_bars,
        )
        latest = candles[-1]
        return ScanResult(
            symbol=symbol,
            trend=trend,
            change_pct=change_pct,
            last_close=latest.close_price,
            fib_382=fib_382,
            fib_hit=fib_hit,
            last_candle_time=latest.timestamp,
            note=note,
        )
    except Exception as exc:  # noqa: BLE001 - return scanner errors per symbol
        return ScanResult(symbol=symbol, error=str(exc))


def format_output(
    results: list[ScanResult],
    trend_lookback_bars: int,
    swing_lookback_bars: int,
) -> str:
    now = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"Stock scan generated: {now}",
        "Data source: Yahoo Finance chart API",
        "Timeframe: 15-minute candles",
        f"Trend rule: compare close now vs close {trend_lookback_bars} candles ago",
        (
            "Fib rule: 38.2% retracement over the most recent "
            f"{swing_lookback_bars} candles; HIT means latest 15m candle touched that level"
        ),
        "",
    ]

    header = (
        "Ticker   Trend      Change%    LastClose   Fib38.2    Hit   "
        "Last Candle (UTC)     Note/Error"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for result in results:
        if result.error:
            lines.append(f"{result.symbol:<8} ERROR      {'-':>8}   {'-':>10}   {'-':>8}   {'-':>3}   {'-':<20} {result.error}")
            continue

        last_candle = (
            result.last_candle_time.strftime("%Y-%m-%d %H:%M")
            if result.last_candle_time
            else "-"
        )
        hit_value = "YES" if result.fib_hit else "NO"
        lines.append(
            f"{result.symbol:<8} "
            f"{(result.trend or '-'): <9} "
            f"{(result.change_pct if result.change_pct is not None else 0):>8.2f}%   "
            f"{(result.last_close if result.last_close is not None else 0):>10.2f}   "
            f"{(result.fib_382 if result.fib_382 is not None else 0):>8.2f}   "
            f"{hit_value:>3}   "
            f"{last_candle:<20} "
            f"{result.note}"
        )

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Read ticker symbols from a text file, detect 15-minute trend, "
            "and report whether the 38.2% Fibonacci level is hit."
        )
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=script_dir / "A.txt",
        help="Path to input ticker file (default: Stck/A.txt)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=script_dir / "scan_output.txt",
        help="Path for output report file (default: Stck/scan_output.txt)",
    )
    parser.add_argument(
        "--trend-lookback",
        type=int,
        default=8,
        help="Candles used to determine trend (default: 8 candles = 2 hours)",
    )
    parser.add_argument(
        "--swing-lookback",
        type=int,
        default=26,
        help="Candles used for Fibonacci swing range (default: 26 candles)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    tickers = read_tickers(args.input)
    results = [
        scan_symbol(
            symbol=ticker,
            trend_lookback_bars=args.trend_lookback,
            swing_lookback_bars=args.swing_lookback,
        )
        for ticker in tickers
    ]

    output_text = format_output(
        results=results,
        trend_lookback_bars=args.trend_lookback,
        swing_lookback_bars=args.swing_lookback,
    )
    args.output.write_text(output_text, encoding="utf-8")

    print(f"Processed {len(tickers)} tickers. Output written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
