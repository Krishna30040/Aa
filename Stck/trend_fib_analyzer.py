#!/usr/bin/env python3
"""Trend and Fibonacci analyzer driven by a watchlist text file.

Workflow implemented:
1) Read watchlist symbols from a text file.
2) Download OHLC data from Yahoo Finance and convert to range bars.
3) Detect trend from market structure (HH/HL or LL/LH).
4) Detect trend from EMA50 / EMA200.
5) In trend direction, check 38.2% retracement behavior.
6) If retracement holds and continuation happens, roll fib anchors forward.
7) Mark every 38.2% touch as a "hit".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf


UPTREND = "uptrend"
DOWNTREND = "downtrend"
NEUTRAL = "neutral"
MIXED = "mixed"


# Hardcoded runtime settings (no command-line inputs).
SCRIPT_DIR = Path(__file__).resolve().parent
WATCHLIST_PATH = SCRIPT_DIR / "A.txt"
OUTPUT_PATH = SCRIPT_DIR / "analysis_output.json"

YAHOO_PERIOD = "1y"
YAHOO_INTERVAL = "1d"
YAHOO_AUTO_ADJUST = False

RANGE_LOOKBACK = 100
RANGE_FACTOR = 0.25
PIVOT_SPAN = 2
FIB_TOLERANCE = 0.05


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


def download_ohlc_from_yahoo(
    ticker: str,
    period: str,
    interval: str,
    auto_adjust: bool,
) -> list[Candle]:
    try:
        history = yf.Ticker(ticker).history(
            period=period,
            interval=interval,
            auto_adjust=auto_adjust,
        )
    except Exception:
        return []
    if history is None or history.empty:
        return []

    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(set(history.columns)):
        return []

    candles: list[Candle] = []
    for timestamp, row in history.iterrows():
        try:
            o = float(row["Open"])
            h = float(row["High"])
            l = float(row["Low"])
            c = float(row["Close"])
        except (TypeError, ValueError, KeyError):
            continue

        if h < l:
            h, l = l, h
        timestamp_text = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        candles.append(Candle(timestamp=timestamp_text, open=o, high=h, low=l, close=c))

    return candles


def compute_ticker_range(
    candles: list[Candle], lookback: int = 100, range_factor: float = 0.25
) -> float:
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
    box_size = median * max(range_factor, 1e-6)
    return max(box_size, 1e-8)


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

        if all(curr.high >= bar.high for bar in neighbors) and any(
            curr.high > bar.high for bar in neighbors
        ):
            raw.append(
                SwingPoint(
                    index=idx, timestamp=curr.timestamp, price=curr.high, kind="H"
                )
            )
        if all(curr.low <= bar.low for bar in neighbors) and any(
            curr.low < bar.low for bar in neighbors
        ):
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
        return DOWNTREND, "price at/below EMA50 (EMA200 unavailable)", last_ema50, None

    # Primary EMA rule requested: above EMA200 => uptrend, otherwise downtrend.
    if last_price > last_ema200:
        if last_price > last_ema50:
            return UPTREND, "price above EMA200 (also above EMA50)", last_ema50, last_ema200
        return UPTREND, "price above EMA200 (below EMA50)", last_ema50, last_ema200

    if last_price < last_ema50 < last_ema200:
        return DOWNTREND, "price below EMA200 (also below EMA50)", last_ema50, last_ema200
    return DOWNTREND, "price at/below EMA200", last_ema50, last_ema200


def combine_trend(structure_trend: str, ema_trend: str) -> str:
    directional = {UPTREND, DOWNTREND}
    # EMA trend is dominant for final trend direction.
    if ema_trend in directional:
        return ema_trend
    if structure_trend in directional:
        return structure_trend
    return NEUTRAL


def _latest_swing_by_kind(swings: list[SwingPoint], kind: str) -> Optional[SwingPoint]:
    for point in reversed(swings):
        if point.kind == kind:
            return point
    return None


def _build_fib_reference(
    trend: str,
    low_point: SwingPoint,
    high_point: SwingPoint,
    source: str,
) -> Optional[dict]:
    move = high_point.price - low_point.price
    if move <= 0:
        return None

    if trend == UPTREND:
        fib_38_2 = high_point.price - (move * 0.382)
    elif trend == DOWNTREND:
        fib_38_2 = low_point.price + (move * 0.382)
    else:
        return None

    return {
        "source": source,
        "trend": trend,
        "fib_low_time": low_point.timestamp,
        "fib_low": low_point.price,
        "fib_high_time": high_point.timestamp,
        "fib_high": high_point.price,
        "fib_38_2": fib_38_2,
    }


def derive_latest_fib_reference(
    swings: list[SwingPoint], final_trend: str, fib_hits: list[FibHit]
) -> Optional[dict]:
    if fib_hits:
        last_hit = fib_hits[-1]
        return {
            "source": "last_fib_hit",
            "trend": last_hit.trend,
            "fib_low_time": last_hit.fib_low_time,
            "fib_low": last_hit.fib_low,
            "fib_high_time": last_hit.fib_high_time,
            "fib_high": last_hit.fib_high,
            "fib_38_2": last_hit.fib_38_2,
        }

    # No fib hits: derive last analyzed fib anchors from recent swings.
    preferred_patterns: list[tuple[str, str, str]]
    if final_trend == UPTREND:
        preferred_patterns = [(UPTREND, "L", "H")]
    elif final_trend == DOWNTREND:
        preferred_patterns = [(DOWNTREND, "H", "L")]
    else:
        preferred_patterns = [(UPTREND, "L", "H"), (DOWNTREND, "H", "L")]

    for trend, first_kind, second_kind in preferred_patterns:
        for idx in range(len(swings) - 2, -1, -1):
            first = swings[idx]
            second = swings[idx + 1]
            if first.kind != first_kind or second.kind != second_kind:
                continue
            if trend == UPTREND:
                low_point, high_point = first, second
            else:
                high_point, low_point = first, second
            reference = _build_fib_reference(
                trend=trend, low_point=low_point, high_point=high_point, source="derived_swings"
            )
            if reference is not None:
                return reference
    return None


def _find_candle_index_by_timestamp(candles: list[Candle], timestamp: str) -> Optional[int]:
    for idx, candle in enumerate(candles):
        if candle.timestamp == timestamp:
            return idx
    return None


def evaluate_current_trend_fib_status(
    candles: list[Candle], fib_reference: Optional[dict], tolerance_ratio: float
) -> Optional[dict]:
    if not fib_reference:
        return None

    trend = fib_reference.get("trend")
    fib_level = fib_reference.get("fib_38_2")
    fib_low = fib_reference.get("fib_low")
    fib_high = fib_reference.get("fib_high")
    fib_low_time = fib_reference.get("fib_low_time")
    fib_high_time = fib_reference.get("fib_high_time")

    if trend not in {UPTREND, DOWNTREND}:
        return None
    if not isinstance(fib_level, (int, float)):
        return None
    if not isinstance(fib_low, (int, float)) or not isinstance(fib_high, (int, float)):
        return None
    if not isinstance(fib_low_time, str) or not isinstance(fib_high_time, str):
        return None

    move = abs(float(fib_high) - float(fib_low))
    tolerance = max(move * tolerance_ratio, 1e-9)
    anchor_time = fib_high_time if trend == UPTREND else fib_low_time
    anchor_index = _find_candle_index_by_timestamp(candles, anchor_time)
    if anchor_index is None:
        return None

    candles_after_anchor = candles[anchor_index + 1 :]
    retraced = False
    retrace_time: Optional[str] = None
    retrace_price: Optional[float] = None
    held: Optional[bool] = None

    if trend == UPTREND:
        for candle in candles_after_anchor:
            if candle.low <= float(fib_level) + tolerance:
                retraced = True
                retrace_time = candle.timestamp
                retrace_price = candle.low
                break
        if retraced and candles_after_anchor:
            lowest_after = min(candle.low for candle in candles_after_anchor)
            held = lowest_after >= (float(fib_level) - tolerance)
    else:
        for candle in candles_after_anchor:
            if candle.high >= float(fib_level) - tolerance:
                retraced = True
                retrace_time = candle.timestamp
                retrace_price = candle.high
                break
        if retraced and candles_after_anchor:
            highest_after = max(candle.high for candle in candles_after_anchor)
            held = highest_after <= (float(fib_level) + tolerance)

    return {
        "trend": trend,
        "fib_38_2": float(fib_level),
        "fib_low_time": fib_low_time,
        "fib_low": float(fib_low),
        "fib_high_time": fib_high_time,
        "fib_high": float(fib_high),
        "tolerance": tolerance,
        "anchor_time": anchor_time,
        "retraced": retraced,
        "retrace_time": retrace_time,
        "retrace_price": retrace_price,
        "held": held,
    }


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


def analyze_ticker(
    ticker: str,
    candles: list[Candle],
    range_lookback: int,
    range_factor: float,
    pivot_span: int,
    tolerance_ratio: float,
) -> dict:
    ticker_range = compute_ticker_range(
        candles, lookback=range_lookback, range_factor=range_factor
    )
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

    # EMA trend is computed on raw OHLC candles to match chart EMA readings.
    ema_source_name = "raw_candles"
    ema_source = candles
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

    # Use raw-candle swings for displayed timestamps so highs/lows map to actual OHLC bars.
    display_swings = detect_swings(candles, pivot_span=pivot_span)
    display_swing_source = "raw_candles"
    if not display_swings and pivot_span > 1:
        display_swings = detect_swings(candles, pivot_span=1)
    if not display_swings:
        display_swings = swings
        display_swing_source = structure_source_name

    last_swing_low = _latest_swing_by_kind(display_swings, "L")
    last_swing_high = _latest_swing_by_kind(display_swings, "H")
    latest_fib_reference = derive_latest_fib_reference(
        swings=display_swings, final_trend=final_trend, fib_hits=[]
    )
    current_trend_fib_status = evaluate_current_trend_fib_status(
        candles=candles,
        fib_reference=latest_fib_reference,
        tolerance_ratio=tolerance_ratio,
    )
    current_candle = candles[-1]

    return {
        "ticker": ticker,
        "raw_candle_count": len(candles),
        "range_box_size": ticker_range,
        "range_bar_count": len(range_bars),
        "structure_source": structure_source_name,
        "swing_count": len(swings),
        "display_swing_source": display_swing_source,
        "display_swing_count": len(display_swings),
        "structure_trend": structure_trend,
        "structure_reason": structure_reason,
        "ema_source": ema_source_name,
        "ema_trend": ema_trend,
        "ema_reason": ema_reason,
        "ema50": ema50,
        "ema200": ema200,
        "final_trend": final_trend,
        "current_price": {
            "timestamp": current_candle.timestamp,
            "price": current_candle.close,
        },
        "last_swing_low": asdict(last_swing_low) if last_swing_low else None,
        "last_swing_high": asdict(last_swing_high) if last_swing_high else None,
        "last_fib_reference": latest_fib_reference,
        "current_trend_fib_status": current_trend_fib_status,
        "fib_hit_count": len(fib_hits),
        "fib_hits": [asdict(hit) for hit in fib_hits],
    }


def _format_price_time(value: Optional[float], timestamp: Optional[str]) -> str:
    if value is None or timestamp is None:
        return "n/a"
    return f"{value:.4f} @ {timestamp}"


def print_ticker_terminal_summary(analysis: dict) -> None:
    ticker = analysis.get("ticker", "UNKNOWN")
    final_trend = analysis.get("final_trend", NEUTRAL)
    last_low = analysis.get("last_swing_low")
    last_high = analysis.get("last_swing_high")
    current = analysis.get("current_price")
    ema200 = analysis.get("ema200")
    fib_reference = analysis.get("last_fib_reference")
    fib_status = analysis.get("current_trend_fib_status")

    current_text = _format_price_time(
        current.get("price") if isinstance(current, dict) else None,
        current.get("timestamp") if isinstance(current, dict) else None,
    )
    low_text = _format_price_time(
        last_low.get("price") if isinstance(last_low, dict) else None,
        last_low.get("timestamp") if isinstance(last_low, dict) else None,
    )
    high_text = _format_price_time(
        last_high.get("price") if isinstance(last_high, dict) else None,
        last_high.get("timestamp") if isinstance(last_high, dict) else None,
    )

    ema200_text = "n/a"
    current_price_value: Optional[float] = None
    if isinstance(current, dict):
        raw_price = current.get("price")
        if isinstance(raw_price, (int, float)):
            current_price_value = float(raw_price)
    if isinstance(ema200, (int, float)):
        if current_price_value is not None:
            relation = "above" if current_price_value > float(ema200) else "at/below"
            ema200_text = f"{float(ema200):.4f} ({relation})"
        else:
            ema200_text = f"{float(ema200):.4f}"

    if isinstance(fib_reference, dict):
        fib_value = fib_reference.get("fib_38_2")
        fib_low = _format_price_time(
            fib_reference.get("fib_low"), fib_reference.get("fib_low_time")
        )
        fib_high = _format_price_time(
            fib_reference.get("fib_high"), fib_reference.get("fib_high_time")
        )
        if isinstance(fib_value, (int, float)):
            fib_text = f"{float(fib_value):.4f} | low {fib_low} -> high {fib_high}"
        else:
            fib_text = "n/a"
    else:
        fib_text = "n/a"

    retraced_text = "n/a"
    held_text = "n/a"
    retrace_touch_text = "n/a"
    if isinstance(fib_status, dict):
        retraced_value = fib_status.get("retraced")
        held_value = fib_status.get("held")
        retrace_touch_text = _format_price_time(
            fib_status.get("retrace_price"), fib_status.get("retrace_time")
        )
        retraced_text = "yes" if retraced_value is True else "no"
        if held_value is True:
            held_text = "yes"
        elif held_value is False:
            held_text = "no"

    print(
        f"{ticker} | Trend: {final_trend} | Current: {current_text} | "
        f"EMA200: {ema200_text} | "
        f"Last Swing Low: {low_text} | Last Swing High: {high_text} | "
        f"Fib 38.2: {fib_text} | Retraced: {retraced_text} "
        f"(at {retrace_touch_text}) | Held: {held_text}"
    )


def main() -> int:
    watchlist_path = WATCHLIST_PATH.resolve()
    output_path = OUTPUT_PATH.resolve()

    if not watchlist_path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {watchlist_path}")

    tickers = read_watchlist(watchlist_path)
    if not tickers:
        raise ValueError(f"No symbols found in watchlist: {watchlist_path}")

    missing_or_failed: list[str] = []
    analyses: list[dict] = []

    for ticker in tickers:
        candles = download_ohlc_from_yahoo(
            ticker=ticker,
            period=YAHOO_PERIOD,
            interval=YAHOO_INTERVAL,
            auto_adjust=YAHOO_AUTO_ADJUST,
        )
        if len(candles) < 3:
            missing_or_failed.append(ticker)
            continue

        analysis = analyze_ticker(
            ticker=ticker,
            candles=candles,
            range_lookback=max(2, RANGE_LOOKBACK),
            range_factor=max(1e-6, RANGE_FACTOR),
            pivot_span=max(1, PIVOT_SPAN),
            tolerance_ratio=max(0.0, FIB_TOLERANCE),
        )
        analyses.append(analysis)
        print_ticker_terminal_summary(analysis)

    report = {
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "watchlist_file": str(watchlist_path),
        "data_source": "yahoo_finance",
        "yahoo_period": YAHOO_PERIOD,
        "yahoo_interval": YAHOO_INTERVAL,
        "yahoo_auto_adjust": YAHOO_AUTO_ADJUST,
        "settings": {
            "range_lookback": RANGE_LOOKBACK,
            "range_factor": RANGE_FACTOR,
            "pivot_span": PIVOT_SPAN,
            "tolerance": FIB_TOLERANCE,
        },
        "analyzed_ticker_count": len(analyses),
        "missing_or_failed_tickers": missing_or_failed,
        "results": analyses,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print(f"Analyzed: {len(analyses)} ticker(s)")
    if missing_or_failed:
        print(f"No Yahoo data or insufficient data for: {', '.join(missing_or_failed)}")
    print(f"Output written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
