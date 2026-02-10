#!/usr/bin/env python3
"""
Uptrend 38.2% retracement stock screener.

This screener:
1) Detects swing highs/lows using pivot windows.
2) Looks for an uptrend sequence: L1 -> H1 -> L2 -> H2 where:
   - L2 is a Higher Low (HL): L2 > L1
   - H2 is a Higher High (HH): H2 > H1
3) Validates that L2 retraced near 38.2% of the L1->H1 impulse.
4) Computes the active 38.2% retracement from L2->H2 and filters
   symbols whose current close is at/near that level.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

FIB_382 = 0.382


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: pd.Timestamp
    price: float
    kind: str  # "H" for swing high, "L" for swing low


@dataclass(frozen=True)
class RetracementSetup:
    base_low: SwingPoint      # L1
    impulse_high: SwingPoint  # H1
    pullback_low: SwingPoint  # L2 (HL)
    breakout_high: SwingPoint  # H2 (HH)
    fib_382_level: float
    retracement_ratio: float


def parse_symbols(symbol_args: Iterable[str], symbols_file: Optional[str]) -> list[str]:
    raw: list[str] = []

    for token in symbol_args:
        raw.extend(part.strip() for part in token.split(",") if part.strip())

    if symbols_file:
        for line in Path(symbols_file).read_text(encoding="utf-8").splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                raw.extend(part.strip() for part in clean.split(",") if part.strip())

    seen: set[str] = set()
    symbols: list[str] = []
    for sym in raw:
        upper = sym.upper()
        if upper not in seen:
            seen.add(upper)
            symbols.append(upper)
    return symbols


def fetch_ohlc(symbol: str, period: str, interval: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "yfinance is required. Install dependencies from Stck/requirements.txt"
        ) from exc

    data = yf.download(
        symbol,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=False,
        threads=False,
    )
    if data.empty:
        return data

    # yfinance may return MultiIndex columns for some versions/configurations.
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = ["Open", "High", "Low", "Close"]
    missing = [col for col in required if col not in data.columns]
    if missing:
        raise ValueError(f"{symbol}: missing columns {missing} in downloaded data")

    out = data[required].dropna().copy()
    out.index = pd.to_datetime(out.index)
    return out


def _compress_swings(swings: list[SwingPoint]) -> list[SwingPoint]:
    """Keep alternating swing sequence and strongest points of same kind."""
    if not swings:
        return []

    compressed: list[SwingPoint] = []
    for swing in sorted(swings, key=lambda p: (p.index, p.kind)):
        if not compressed:
            compressed.append(swing)
            continue

        last = compressed[-1]
        if swing.index == last.index:
            # Skip duplicate pivot on same bar to keep sequence clean.
            continue

        if swing.kind == last.kind:
            if swing.kind == "H" and swing.price >= last.price:
                compressed[-1] = swing
            elif swing.kind == "L" and swing.price <= last.price:
                compressed[-1] = swing
        else:
            compressed.append(swing)

    return compressed


def detect_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> list[SwingPoint]:
    if len(df) < (left + right + 1):
        return []

    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    idx = list(df.index)

    swings: list[SwingPoint] = []

    for i in range(left, len(df) - right):
        high_window = highs[i - left : i + right + 1]
        low_window = lows[i - left : i + right + 1]

        current_high = highs[i]
        current_low = lows[i]

        is_swing_high = current_high == max(high_window) and high_window.count(current_high) == 1
        is_swing_low = current_low == min(low_window) and low_window.count(current_low) == 1

        if is_swing_high:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp=pd.Timestamp(idx[i]),
                    price=float(current_high),
                    kind="H",
                )
            )
        if is_swing_low:
            swings.append(
                SwingPoint(
                    index=i,
                    timestamp=pd.Timestamp(idx[i]),
                    price=float(current_low),
                    kind="L",
                )
            )

    return _compress_swings(swings)


def find_uptrend_382_setups(
    swings: list[SwingPoint],
    retracement_tolerance: float = 0.05,
) -> list[RetracementSetup]:
    """
    Find valid L1-H1-L2-H2 uptrend structures with L2 near 38.2% retracement.

    retracement_tolerance is in ratio terms:
    - 0.05 means +/- 5% around 0.382 (i.e. between 0.332 and 0.432).
    """
    setups: list[RetracementSetup] = []
    if len(swings) < 4:
        return setups

    for i in range(len(swings) - 3):
        l1, h1, l2, h2 = swings[i : i + 4]

        if (l1.kind, h1.kind, l2.kind, h2.kind) != ("L", "H", "L", "H"):
            continue

        # Enforce HL and HH structure.
        if not (l2.price > l1.price and h2.price > h1.price):
            continue

        impulse = h1.price - l1.price
        if impulse <= 0:
            continue

        retracement_ratio = (h1.price - l2.price) / impulse
        if abs(retracement_ratio - FIB_382) > retracement_tolerance:
            continue

        fib_382_level = h1.price - FIB_382 * impulse

        # "Support holds": allow slight noise under level, but not a deep break.
        max_break = impulse * retracement_tolerance
        if l2.price < fib_382_level - max_break:
            continue

        setups.append(
            RetracementSetup(
                base_low=l1,
                impulse_high=h1,
                pullback_low=l2,
                breakout_high=h2,
                fib_382_level=fib_382_level,
                retracement_ratio=retracement_ratio,
            )
        )

    return setups


def screen_symbol(
    symbol: str,
    df: pd.DataFrame,
    left: int,
    right: int,
    retracement_tolerance: float,
    current_zone_pct: float,
) -> Optional[dict]:
    swings = detect_swings(df, left=left, right=right)
    setups = find_uptrend_382_setups(swings, retracement_tolerance=retracement_tolerance)
    if not setups:
        return None

    latest_setup = setups[-1]
    current_close = float(df["Close"].iloc[-1])

    # Current filter: price reached near 38.2% of previous HL -> HH leg.
    previous_hl = latest_setup.pullback_low
    previous_hh = latest_setup.breakout_high
    active_leg = previous_hh.price - previous_hl.price
    if active_leg <= 0:
        return None

    active_fib_382 = previous_hh.price - FIB_382 * active_leg
    distance_pct = ((current_close - active_fib_382) / active_fib_382) * 100.0

    if abs(distance_pct) > current_zone_pct:
        return None

    return {
        "symbol": symbol,
        "close": current_close,
        "active_fib_382": active_fib_382,
        "distance_to_382_pct": distance_pct,
        "l1_date": latest_setup.base_low.timestamp.date().isoformat(),
        "h1_date": latest_setup.impulse_high.timestamp.date().isoformat(),
        "l2_date": latest_setup.pullback_low.timestamp.date().isoformat(),
        "h2_date": latest_setup.breakout_high.timestamp.date().isoformat(),
        "initial_retracement_pct": latest_setup.retracement_ratio * 100.0,
    }


def format_results(results: list[dict]) -> str:
    if not results:
        return "No symbols matched the HH/HL + 38.2 retracement conditions."

    df = pd.DataFrame(results)
    df = df.sort_values(by="distance_to_382_pct", key=lambda s: s.abs(), ascending=True)

    rename_map = {
        "symbol": "Symbol",
        "close": "Close",
        "active_fib_382": "Active_38.2",
        "distance_to_382_pct": "Dist_%_to_38.2",
        "initial_retracement_pct": "FirstPullbackRetr_%"
    }
    df = df.rename(columns=rename_map)

    float_columns = [
        "Close",
        "Active_38.2",
        "Dist_%_to_38.2",
        "FirstPullbackRetr_%",
    ]
    for col in float_columns:
        df[col] = df[col].map(lambda x: round(float(x), 4))

    return df.to_string(index=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Screen stocks for uptrend HH/HL structure where pullbacks respect 38.2% "
            "retracement and current price is near the active 38.2% level."
        )
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=[],
        help="Symbols to scan (space-separated and/or comma-separated).",
    )
    parser.add_argument(
        "--symbols-file",
        default=None,
        help="Optional text file with symbols (one per line or comma-separated).",
    )
    parser.add_argument("--period", default="1y", help="Yahoo period, e.g. 6mo, 1y, 2y.")
    parser.add_argument("--interval", default="1d", help="Yahoo interval, e.g. 1d, 1h.")
    parser.add_argument(
        "--pivot-left",
        type=int,
        default=3,
        help="Bars to the left for pivot swing detection.",
    )
    parser.add_argument(
        "--pivot-right",
        type=int,
        default=3,
        help="Bars to the right for pivot swing detection.",
    )
    parser.add_argument(
        "--retracement-tolerance",
        type=float,
        default=0.05,
        help=(
            "Tolerance around 38.2 retracement in ratio terms. "
            "Example: 0.05 means acceptable retracement is 38.2% +/- 5.0%."
        ),
    )
    parser.add_argument(
        "--current-zone-pct",
        type=float,
        default=1.0,
        help="Current close must be within this %% distance of active 38.2 level.",
    )
    parser.add_argument(
        "--min-bars",
        type=int,
        default=80,
        help="Minimum bars required before symbol is evaluated.",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Optional path to save screener matches as CSV.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols, args.symbols_file)
    if not symbols:
        parser.error("No symbols provided. Use --symbols and/or --symbols-file.")

    results: list[dict] = []

    for symbol in symbols:
        try:
            data = fetch_ohlc(symbol, period=args.period, interval=args.interval)
            if data.empty:
                print(f"[WARN] {symbol}: no data returned.", file=sys.stderr)
                continue
            if len(data) < args.min_bars:
                print(
                    f"[WARN] {symbol}: not enough bars ({len(data)} < {args.min_bars}).",
                    file=sys.stderr,
                )
                continue

            row = screen_symbol(
                symbol=symbol,
                df=data,
                left=args.pivot_left,
                right=args.pivot_right,
                retracement_tolerance=args.retracement_tolerance,
                current_zone_pct=args.current_zone_pct,
            )
            if row:
                results.append(row)
        except Exception as exc:  # pragma: no cover
            print(f"[WARN] {symbol}: {exc}", file=sys.stderr)

    if args.output_csv:
        out_df = pd.DataFrame(results)
        out_path = Path(args.output_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(out_path, index=False)
        print(f"Saved {len(out_df)} match(es) to {out_path}")

    print(format_results(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
