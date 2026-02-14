from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Candle:
    index: int
    timestamp: str
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: str
    kind: str  # "H" or "L"
    price: float
    label: str | None = None


def extract_swing_points(
    candles: Sequence[Candle],
    left_bars: int = 2,
    right_bars: int = 2,
) -> list[SwingPoint]:
    """Extracts local pivot highs/lows using a fractal-style rule."""
    if left_bars < 1 or right_bars < 1:
        raise ValueError("left_bars and right_bars must be >= 1")
    if len(candles) < left_bars + right_bars + 1:
        return []

    swings: list[SwingPoint] = []
    for i in range(left_bars, len(candles) - right_bars):
        current = candles[i]
        window = candles[i - left_bars : i + right_bars + 1]

        highs = [c.high for c in window]
        lows = [c.low for c in window]

        is_swing_high = current.high == max(highs) and highs.count(current.high) == 1
        is_swing_low = current.low == min(lows) and lows.count(current.low) == 1

        # Outside bars can be both high and low in the same window; skip ambiguity.
        if is_swing_high and not is_swing_low:
            swings.append(
                SwingPoint(
                    index=current.index,
                    timestamp=current.timestamp,
                    kind="H",
                    price=current.high,
                )
            )
        elif is_swing_low and not is_swing_high:
            swings.append(
                SwingPoint(
                    index=current.index,
                    timestamp=current.timestamp,
                    kind="L",
                    price=current.low,
                )
            )

    return swings


def label_swings(swings: Iterable[SwingPoint]) -> list[SwingPoint]:
    """
    Labels each swing against the previous same-kind swing:
    - Lows: LL (lower low), HL (higher low), EL (equal low)
    - Highs: LH (lower high), HH (higher high), EH (equal high)
    """
    labeled: list[SwingPoint] = []
    prev_low: float | None = None
    prev_high: float | None = None

    for swing in swings:
        label: str | None = None
        if swing.kind == "L":
            if prev_low is not None:
                if swing.price < prev_low:
                    label = "LL"
                elif swing.price > prev_low:
                    label = "HL"
                else:
                    label = "EL"
            prev_low = swing.price
        else:
            if prev_high is not None:
                if swing.price < prev_high:
                    label = "LH"
                elif swing.price > prev_high:
                    label = "HH"
                else:
                    label = "EH"
            prev_high = swing.price

        labeled.append(
            SwingPoint(
                index=swing.index,
                timestamp=swing.timestamp,
                kind=swing.kind,
                price=swing.price,
                label=label,
            )
        )
    return labeled


def find_label_pattern(
    labeled_swings: Sequence[SwingPoint],
    pattern: Sequence[str],
) -> list[list[SwingPoint]]:
    """Finds contiguous windows whose labels exactly match `pattern`."""
    if not pattern:
        return []

    events = [s for s in labeled_swings if s.label is not None]
    pattern_len = len(pattern)
    if len(events) < pattern_len:
        return []

    matches: list[list[SwingPoint]] = []
    for start in range(len(events) - pattern_len + 1):
        window = events[start : start + pattern_len]
        labels = [s.label for s in window]
        if labels == list(pattern):
            matches.append(window)
    return matches


def find_ll_lh_ll_lh_ll(
    candles: Sequence[Candle],
    left_bars: int = 2,
    right_bars: int = 2,
) -> list[list[SwingPoint]]:
    swings = extract_swing_points(candles, left_bars=left_bars, right_bars=right_bars)
    labeled = label_swings(swings)
    return find_label_pattern(labeled, pattern=("LL", "LH", "LL", "LH", "LL"))


def load_ohlc_csv(path: Path) -> list[Candle]:
    """
    Loads OHLC candles from CSV.
    Required columns: high, low
    Optional columns: timestamp/time/date/datetime, open, close
    """
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV file has no header")

        rows: list[Candle] = []
        for i, raw_row in enumerate(reader):
            row = {k.strip().lower(): (v.strip() if v is not None else "") for k, v in raw_row.items()}

            timestamp = (
                row.get("timestamp")
                or row.get("time")
                or row.get("date")
                or row.get("datetime")
                or str(i)
            )

            if row.get("high") in (None, "") or row.get("low") in (None, ""):
                raise ValueError("CSV requires 'high' and 'low' columns with numeric values")

            high = float(row["high"])
            low = float(row["low"])
            open_ = float(row["open"]) if row.get("open") not in (None, "") else low
            close = float(row["close"]) if row.get("close") not in (None, "") else high

            rows.append(
                Candle(
                    index=i,
                    timestamp=timestamp,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                )
            )

    return rows


def build_demo_candles() -> list[Candle]:
    """
    Synthetic candles that contain one LL, LH, LL, LH, LL sequence
    with left/right pivot settings of 1 bar.
    """
    highs = [11.0, 12.0, 13.0, 12.2, 11.3, 12.0, 12.4, 11.0, 10.0, 11.0, 11.4, 10.2, 9.2, 9.8, 9.0]
    lows = [9.0, 8.0, 11.2, 9.5, 7.0, 9.0, 10.0, 8.5, 6.5, 8.2, 9.0, 7.8, 5.8, 7.0, 6.0]

    candles: list[Candle] = []
    for i, (high, low) in enumerate(zip(highs, lows)):
        midpoint = (high + low) / 2.0
        candles.append(
            Candle(
                index=i,
                timestamp=f"T{i:02d}",
                open=midpoint,
                high=high,
                low=low,
                close=midpoint,
            )
        )
    return candles


def print_matches(matches: Sequence[Sequence[SwingPoint]]) -> None:
    if not matches:
        print("No LL, LH, LL, LH, LL pattern found.")
        return

    print(f"Found {len(matches)} match(es):")
    for i, match in enumerate(matches, start=1):
        start, end = match[0], match[-1]
        print(f"\nMatch #{i}: bars {start.index} -> {end.index}")
        for point in match:
            print(
                f"  idx={point.index:>4} ts={point.timestamp:<10} "
                f"type={point.kind} price={point.price:>8.2f} label={point.label}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect LL, LH, LL, LH, LL swing pattern in OHLC data."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="Path to CSV with at least high,low columns (optional open,close,timestamp).",
    )
    parser.add_argument("--left", type=int, default=2, help="Bars to the left for pivot detection.")
    parser.add_argument("--right", type=int, default=2, help="Bars to the right for pivot detection.")
    args = parser.parse_args()

    if args.csv:
        candles = load_ohlc_csv(args.csv)
    else:
        candles = build_demo_candles()
        # Demo data is built around 1-bar pivots.
        if args.left == 2 and args.right == 2:
            args.left = 1
            args.right = 1

    matches = find_ll_lh_ll_lh_ll(candles, left_bars=args.left, right_bars=args.right)
    print_matches(matches)


if __name__ == "__main__":
    main()
