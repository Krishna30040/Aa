#!/usr/bin/env python3
"""Trend and Fibonacci analyzer driven by a watchlist text file.

Workflow implemented:
1) Read watchlist symbols from a text file.
2) Convert OHLC timeframe data into range bars based on each ticker's range size.
3) Detect trend from market structure (HH/HL or LL/LH).
4) Detect trend from EMA50 / EMA200.
5) In trend direction, check 38.2% retracement behavior.
6) If retracement holds and continuation happens, roll fib anchors forward.
7) Mark every 38.2% touch as a "hit".
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


UPTREND = "uptrend"
DOWNTREND = "downtrend"
NEUTRAL = "neutral"
MIXED = "mixed"


@dataclass
class Candle:
    timestamp: str
    open: float
    high: float
    low: float
    close: float


@dataclass
class SwingPoint:
    index: int
    timestamp: str
    price: float
    kind: str  # "H" or "L"


@dataclass
class FibHit:
    trend: str
    fib_low_time: str
    fib_low: float
    fib_high_time: str
    fib_high: float
    fib_38_2: float
    retrace_time: str
    retrace_price: float
    hit: bool
    held_level: bool
    continuation: bool
    next_extreme_time: str
    next_extreme_price: float


def read_watchlist(path: Path) -> list[str]:
    symbols: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            symbols.append(line.upper())
    return symbols


def _find_column(fieldnames: Iterable[str], candidates: list[str]) -> Optional[str]:
    lowered = {name.lower().strip(): name for name in fieldnames}
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found:
            return found
    return None


def _to_float(raw_value: str) -> float:
    text = raw_value.strip().replace(",", "")
    return float(text)


def load_ohlc_csv(path: Path) -> list[Candle]:
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row")

        open_col = _find_column(reader.fieldnames, ["open", "o"])
        high_col = _find_column(reader.fieldnames, ["high", "h"])
        low_col = _find_column(reader.fieldnames, ["low", "l"])
        close_col = _find_column(reader.fieldnames, ["close", "c", "adj_close"])
        time_col = _find_column(reader.fieldnames, ["timestamp", "datetime", "date", "time"])

        required = [open_col, high_col, low_col, close_col]
        if any(col is None for col in required):
            raise ValueError(
                f"{path} must contain Open/High/Low/Close columns. "
                f"Found columns: {reader.fieldnames}"
            )

        for idx, row in enumerate(reader, start=1):
            try:
                o = _to_float(row[open_col])  # type: ignore[index]
                h = _to_float(row[high_col])  # type: ignore[index]
                l = _to_float(row[low_col])  # type: ignore[index]
                c = _to_float(row[close_col])  # type: ignore[index]
            except (TypeError, ValueError, KeyError):
                continue

            if h < l:
                h, l = l, h
            timestamp = str(row.get(time_col, idx)) if time_col else str(idx)
            candles.append(Candle(timestamp=timestamp, open=o, high=h, low=l, close=c))
    return candles


def compute_ticker_range(candles: list[Candle], lookback: int = 100) -> float:
    if not candles:
        return 0.0
    sample = candles[-lookback:] if len(candles) > lookback else candles
    ranges = sorted(max(0.0, bar.high - bar.low) for bar in sample)
    if not ranges:
        return 0.0
    mid = len(ranges) // 2
    if len(ranges) % 2 == 1:
        median = ranges[mid]
    else:
        median = (ranges[mid - 1] + ranges[mid]) / 2.0
    return max(median, 1e-8)


def to_range_bars(candles: list[Candle], box_size: float) -> list[Candle]:
    if box_size <= 0 or len(candles) < 2:
        return []

    range_bars: list[Candle] = []
    last_close = candles[0].close
    for candle in candles[1:]:
        # Approximate intrabar path so large candles can build multiple range bars.
        for price in (candle.open, candle.high, candle.low, candle.close):
            delta = price - last_close
            while abs(delta) >= box_size:
                direction = 1.0 if delta > 0 else -1.0
                new_close = last_close + direction * box_size
                range_bars.append(
                    Candle(
                        timestamp=candle.timestamp,
                        open=last_close,
                        high=max(last_close, new_close),
                        low=min(last_close, new_close),
                        close=new_close,
                    )
                )
                last_close = new_close
                delta = price - last_close
    return range_bars


def detect_swings(range_bars: list[Candle], pivot_span: int = 2) -> list[SwingPoint]:
    if len(range_bars) < (pivot_span * 2 + 1):
        return []

    raw: list[SwingPoint] = []
    for idx in range(pivot_span, len(range_bars) - pivot_span):
        curr = range_bars[idx]
        left = range_bars[idx - pivot_span : idx]
        right = range_bars[idx + 1 : idx + pivot_span + 1]
        neighbors = left + right

        if all(curr.high > bar.high for bar in neighbors):
            raw.append(
                SwingPoint(
                    index=idx, timestamp=curr.timestamp, price=curr.high, kind="H"
                )
            )
        if all(curr.low < bar.low for bar in neighbors):
            raw.append(
                SwingPoint(index=idx, timestamp=curr.timestamp, price=curr.low, kind="L")
            )

    if not raw:
        return []

    raw.sort(key=lambda point: point.index)
    filtered: list[SwingPoint] = [raw[0]]
    for point in raw[1:]:
        prev = filtered[-1]
        if point.kind == prev.kind:
            if point.kind == "H" and point.price >= prev.price:
                filtered[-1] = point
            elif point.kind == "L" and point.price <= prev.price:
                filtered[-1] = point
        else:
            filtered.append(point)
    return filtered


def determine_structure_trend(swings: list[SwingPoint]) -> tuple[str, str]:
    if len(swings) < 4:
        return NEUTRAL, "insufficient swings"

    for idx in range(len(swings) - 4, -1, -1):
        a, b, c, d = swings[idx : idx + 4]
        pattern = (a.kind, b.kind, c.kind, d.kind)
        if pattern == ("L", "H", "L", "H"):
            if c.price > a.price and d.price > b.price:
                return UPTREND, "HH/HL structure detected"
        elif pattern == ("H", "L", "H", "L"):
            if c.price < a.price and d.price < b.price:
                return DOWNTREND, "LL/LH structure detected"
    return NEUTRAL, "no HH/HL or LL/LH sequence found"


def ema(values: list[float], period: int) -> list[Optional[float]]:
    if not values:
        return []
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(values) < period:
        return [None] * len(values)

    result: list[Optional[float]] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)
    multiplier = 2.0 / (period + 1.0)
    current = seed
    for value in values[period:]:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def determine_ema_trend(candles: list[Candle]) -> tuple[str, str, Optional[float], Optional[float]]:
    closes = [bar.close for bar in candles]
    if not closes:
        return NEUTRAL, "no data", None, None

    ema_50 = ema(closes, 50)
    ema_200 = ema(closes, 200)
    last_price = closes[-1]
    last_ema50 = ema_50[-1] if ema_50 else None
    last_ema200 = ema_200[-1] if ema_200 else None

    if last_ema50 is None:
        return NEUTRAL, "insufficient bars for EMA50", None, None

    if last_ema200 is None:
        if last_price > last_ema50:
            return UPTREND, "price above EMA50 (EMA200 unavailable)", last_ema50, None
        if last_price < last_ema50:
            return DOWNTREND, "price below EMA50 (EMA200 unavailable)", last_ema50, None
        return NEUTRAL, "price equals EMA50 (EMA200 unavailable)", last_ema50, None

    if last_price > last_ema50 > last_ema200:
        return UPTREND, "price > EMA50 > EMA200", last_ema50, last_ema200
    if last_price < last_ema50 < last_ema200:
        return DOWNTREND, "price < EMA50 < EMA200", last_ema50, last_ema200
    return NEUTRAL, "EMA alignment not directional", last_ema50, last_ema200


def combine_trend(structure_trend: str, ema_trend: str) -> str:
    directional = {UPTREND, DOWNTREND}
    if structure_trend == ema_trend and structure_trend in directional:
        return structure_trend
    if structure_trend in directional and ema_trend == NEUTRAL:
        return structure_trend
    if ema_trend in directional and structure_trend == NEUTRAL:
        return ema_trend
    if structure_trend in directional and ema_trend in directional and structure_trend != ema_trend:
        return MIXED
    return NEUTRAL


def scan_uptrend_fib_hits(swings: list[SwingPoint], tolerance_ratio: float) -> list[FibHit]:
    hits: list[FibHit] = []
    idx = 0
    while idx + 3 < len(swings):
        a, b, c, d = swings[idx : idx + 4]
        if (a.kind, b.kind, c.kind, d.kind) != ("L", "H", "L", "H"):
            idx += 1
            continue

        move = b.price - a.price
        if move <= 0:
            idx += 1
            continue

        fib_38_2 = b.price - (move * 0.382)
        tolerance = max(move * tolerance_ratio, 1e-9)
        hit = c.price <= (fib_38_2 + tolerance)
        held = c.price >= (fib_38_2 - tolerance)
        continuation = d.price > b.price

        if hit:
            hits.append(
                FibHit(
                    trend=UPTREND,
                    fib_low_time=a.timestamp,
                    fib_low=a.price,
                    fib_high_time=b.timestamp,
                    fib_high=b.price,
                    fib_38_2=fib_38_2,
                    retrace_time=c.timestamp,
                    retrace_price=c.price,
                    hit=True,
                    held_level=held,
                    continuation=continuation,
                    next_extreme_time=d.timestamp,
                    next_extreme_price=d.price,
                )
            )

        if hit and held and continuation:
            # Promote c/d to next fib low/high and continue.
            idx += 2
        else:
            idx += 1
    return hits


def scan_downtrend_fib_hits(swings: list[SwingPoint], tolerance_ratio: float) -> list[FibHit]:
    hits: list[FibHit] = []
    idx = 0
    while idx + 3 < len(swings):
        a, b, c, d = swings[idx : idx + 4]
        if (a.kind, b.kind, c.kind, d.kind) != ("H", "L", "H", "L"):
            idx += 1
            continue

        move = a.price - b.price
        if move <= 0:
            idx += 1
            continue

        fib_38_2 = b.price + (move * 0.382)
        tolerance = max(move * tolerance_ratio, 1e-9)
        hit = c.price >= (fib_38_2 - tolerance)
        held = c.price <= (fib_38_2 + tolerance)
        continuation = d.price < b.price

        if hit:
            hits.append(
                FibHit(
                    trend=DOWNTREND,
                    fib_low_time=b.timestamp,
                    fib_low=b.price,
                    fib_high_time=a.timestamp,
                    fib_high=a.price,
                    fib_38_2=fib_38_2,
                    retrace_time=c.timestamp,
                    retrace_price=c.price,
                    hit=True,
                    held_level=held,
                    continuation=continuation,
                    next_extreme_time=d.timestamp,
                    next_extreme_price=d.price,
                )
            )

        if hit and held and continuation:
            # Promote d/c to next fib low/high in downtrend progression.
            idx += 2
        else:
            idx += 1
    return hits


def find_ticker_csv(data_dir: Path, ticker: str) -> Optional[Path]:
    candidates = [
        data_dir / f"{ticker}.csv",
        data_dir / f"{ticker.upper()}.csv",
        data_dir / f"{ticker.lower()}.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def analyze_ticker(
    ticker: str,
    candles: list[Candle],
    range_lookback: int,
    pivot_span: int,
    tolerance_ratio: float,
) -> dict:
    ticker_range = compute_ticker_range(candles, lookback=range_lookback)
    range_bars = to_range_bars(candles, ticker_range)

    # Prefer range bars for swing structure. If there are too few bars/swings,
    # fall back to raw candles and a tighter pivot span.
    structure_source_name = "range_bars"
    structure_source = range_bars
    if len(structure_source) < (pivot_span * 2 + 1):
        structure_source_name = "raw_candles"
        structure_source = candles

    swings = detect_swings(structure_source, pivot_span=pivot_span)
    if not swings and pivot_span > 1:
        swings = detect_swings(structure_source, pivot_span=1)

    structure_trend, structure_reason = determine_structure_trend(swings)

    # EMA is trend smoother; use range bars when enough exist, otherwise raw candles.
    ema_source_name = "range_bars" if len(range_bars) >= 50 else "raw_candles"
    ema_source = range_bars if len(range_bars) >= 50 else candles
    ema_trend, ema_reason, ema50, ema200 = determine_ema_trend(ema_source)
    final_trend = combine_trend(structure_trend, ema_trend)

    if final_trend == UPTREND:
        fib_hits = scan_uptrend_fib_hits(swings, tolerance_ratio=tolerance_ratio)
    elif final_trend == DOWNTREND:
        fib_hits = scan_downtrend_fib_hits(swings, tolerance_ratio=tolerance_ratio)
    else:
        # If trend is mixed/neutral, still report both possible hit sequences.
        fib_hits = scan_uptrend_fib_hits(swings, tolerance_ratio=tolerance_ratio)
        fib_hits.extend(scan_downtrend_fib_hits(swings, tolerance_ratio=tolerance_ratio))

    return {
        "ticker": ticker,
        "raw_candle_count": len(candles),
        "range_box_size": ticker_range,
        "range_bar_count": len(range_bars),
        "structure_source": structure_source_name,
        "swing_count": len(swings),
        "structure_trend": structure_trend,
        "structure_reason": structure_reason,
        "ema_source": ema_source_name,
        "ema_trend": ema_trend,
        "ema_reason": ema_reason,
        "ema50": ema50,
        "ema200": ema200,
        "final_trend": final_trend,
        "fib_hit_count": len(fib_hits),
        "fib_hits": [asdict(hit) for hit in fib_hits],
    }


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trend + Fibonacci 38.2 analyzer")
    parser.add_argument(
        "--watchlist",
        default="A.txt",
        help="Path to watchlist text file (one ticker per line).",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing per-ticker OHLC CSV files.",
    )
    parser.add_argument(
        "--output",
        default="analysis_output.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--range-lookback",
        type=int,
        default=100,
        help="Bars used to compute ticker-specific median range size.",
    )
    parser.add_argument(
        "--pivot-span",
        type=int,
        default=2,
        help="Bars on each side used for swing-high/swing-low detection.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.05,
        help="Tolerance ratio around 38.2%% level (fraction of swing size).",
    )
    return parser


def main() -> int:
    parser = build_cli()
    args = parser.parse_args()

    watchlist_path = Path(args.watchlist).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not watchlist_path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {watchlist_path}")
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    tickers = read_watchlist(watchlist_path)
    if not tickers:
        raise ValueError(f"No symbols found in watchlist: {watchlist_path}")

    missing_data: list[str] = []
    analyses: list[dict] = []

    for ticker in tickers:
        csv_path = find_ticker_csv(data_dir, ticker)
        if csv_path is None:
            missing_data.append(ticker)
            continue

        candles = load_ohlc_csv(csv_path)
        if len(candles) < 3:
            missing_data.append(ticker)
            continue

        analyses.append(
            analyze_ticker(
                ticker=ticker,
                candles=candles,
                range_lookback=max(2, args.range_lookback),
                pivot_span=max(1, args.pivot_span),
                tolerance_ratio=max(0.0, args.tolerance),
            )
        )

    report = {
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "watchlist_file": str(watchlist_path),
        "data_directory": str(data_dir),
        "settings": {
            "range_lookback": args.range_lookback,
            "pivot_span": args.pivot_span,
            "tolerance": args.tolerance,
        },
        "analyzed_ticker_count": len(analyses),
        "missing_data_tickers": missing_data,
        "results": analyses,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print(f"Analyzed: {len(analyses)} ticker(s)")
    if missing_data:
        print(f"Missing/invalid data for: {', '.join(missing_data)}")
    print(f"Output written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
