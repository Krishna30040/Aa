import os
from typing import Any

import pandas as pd
import yfinance as yf

FIB_382 = 0.382


def fib_382_upmove(swing_low: float, swing_high: float) -> float:
    # Pullback level after an up-move from low -> high.
    return swing_high - (swing_high - swing_low) * FIB_382


def fib_382_downmove(swing_low: float, swing_high: float) -> float:
    # Bounce level after a down-move from high -> low.
    return swing_low + (swing_high - swing_low) * FIB_382


def get_15m_data(ticker: str, period: str = "30d") -> pd.DataFrame:
    df = yf.download(
        tickers=ticker,
        period=period,
        interval="5m",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # Flatten MultiIndex if present.
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(-1):
            df.columns = df.columns.get_level_values(-1)
        else:
            df.columns = df.columns.get_level_values(0)

    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep].copy()

    for c in keep:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["High", "Low", "Close"])
    return df


def add_emas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    return df


def detect_trend_ema(df: pd.DataFrame) -> str | None:
    if df is None or df.empty:
        return None

    ema20 = float(df["EMA20"].iloc[-1])
    ema50 = float(df["EMA50"].iloc[-1])

    if ema20 > ema50:
        return "Uptrend"
    if ema20 < ema50:
        return "Downtrend"
    return "Flat"


def find_last_crossover_index(df: pd.DataFrame) -> int:
    """
    Returns the positional index of the most recent EMA20/EMA50 crossover.
    If none exists in the dataset, returns 0.
    """
    above = (df["EMA20"] > df["EMA50"]).astype(int)
    cross = above.diff().fillna(0).abs()

    cross_positions = cross[cross == 1].index
    if len(cross_positions) == 0:
        return 0

    last_label = cross_positions[-1]
    last_pos = df.index.get_loc(last_label)
    return int(last_pos)


def compute_trend_segment_swings(df: pd.DataFrame, trend: str, min_segment_bars: int = 60) -> dict[str, Any] | None:
    """
    Compute swing points using only the current trend segment (from last EMA crossover to now).

    Uptrend:
      - swing_low = lowest Low in segment
      - swing_high = highest High AFTER that swing_low
    Downtrend:
      - swing_high = highest High in segment
      - swing_low = lowest Low AFTER that swing_high
    """
    n = len(df)
    if n < min_segment_bars:
        return None

    start_pos = find_last_crossover_index(df)

    # If crossover too recent, extend segment backward so it has enough bars.
    start_pos = max(0, start_pos)
    if (n - start_pos) < min_segment_bars:
        start_pos = max(0, n - min_segment_bars)

    seg = df.iloc[start_pos:].copy()
    if seg.empty:
        return None

    if trend == "Uptrend":
        low_label = seg["Low"].idxmin()
        low_pos_in_seg = seg.index.get_loc(low_label)

        after_low = seg.iloc[low_pos_in_seg:]
        if after_low.empty:
            return None

        high_label = after_low["High"].idxmax()

        swing_low = float(seg.loc[low_label, "Low"])
        swing_high = float(after_low.loc[high_label, "High"])

        return {
            "trend_start_pos": start_pos,
            "swing_low": swing_low,
            "swing_high": swing_high,
            "swing_low_time": low_label,
            "swing_high_time": high_label,
        }

    if trend == "Downtrend":
        high_label = seg["High"].idxmax()
        high_pos_in_seg = seg.index.get_loc(high_label)

        after_high = seg.iloc[high_pos_in_seg:]
        if after_high.empty:
            return None

        low_label = after_high["Low"].idxmin()

        swing_high = float(seg.loc[high_label, "High"])
        swing_low = float(after_high.loc[low_label, "Low"])

        return {
            "trend_start_pos": start_pos,
            "swing_low": swing_low,
            "swing_high": swing_high,
            "swing_low_time": low_label,
            "swing_high_time": high_label,
        }

    return None


def _touches_level(bar_low: float, bar_high: float, level: float, tolerance_pct: float) -> bool:
    tol = level * (tolerance_pct / 100.0)
    return bar_low <= (level + tol) and bar_high >= (level - tol)


def _lowest_low_between(seg: pd.DataFrame, start_pos: int, end_pos: int) -> tuple[int, float]:
    window = seg.iloc[start_pos : end_pos + 1]
    low_label = window["Low"].idxmin()
    low_pos = int(seg.index.get_loc(low_label))
    low_val = float(window.loc[low_label, "Low"])
    return low_pos, low_val


def _highest_high_between(seg: pd.DataFrame, start_pos: int, end_pos: int) -> tuple[int, float]:
    window = seg.iloc[start_pos : end_pos + 1]
    high_label = window["High"].idxmax()
    high_pos = int(seg.index.get_loc(high_label))
    high_val = float(window.loc[high_label, "High"])
    return high_pos, high_val


def _finalize_event(candidate: dict[str, Any], status: str, resolution_time: Any) -> dict[str, Any]:
    first_touch_time = candidate.get("first_touch_time")
    return {
        "Trend": candidate["trend"],
        "Status": status,
        "SwingLow": round(float(candidate["swing_low"]), 4),
        "SwingHigh": round(float(candidate["swing_high"]), 4),
        "Fib38.2": round(float(candidate["fib_level"]), 4),
        "MovePct": round(float(candidate["move_pct"]), 4),
        "SwingLowTime": str(candidate["swing_low_time"]),
        "SwingHighTime": str(candidate["swing_high_time"]),
        "FirstTouchTime": "" if first_touch_time is None else str(first_touch_time),
        "ResolutionTime": str(resolution_time),
    }


def _active_event(candidate: dict[str, Any], status: str) -> dict[str, Any]:
    first_touch_time = candidate.get("first_touch_time")
    return {
        "Trend": candidate["trend"],
        "Status": status,
        "SwingLow": round(float(candidate["swing_low"]), 4),
        "SwingHigh": round(float(candidate["swing_high"]), 4),
        "Fib38.2": round(float(candidate["fib_level"]), 4),
        "MovePct": round(float(candidate["move_pct"]), 4),
        "SwingLowTime": str(candidate["swing_low_time"]),
        "SwingHighTime": str(candidate["swing_high_time"]),
        "FirstTouchTime": "" if first_touch_time is None else str(first_touch_time),
        "ResolutionTime": "",
    }


def _scan_uptrend_retracements(
    seg: pd.DataFrame, min_move_pct: float, level_tolerance_pct: float
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    n = len(seg)
    if n < 3:
        return events, None

    lows = seg["Low"].astype(float)
    highs = seg["High"].astype(float)
    closes = seg["Close"].astype(float)
    idx = seg.index

    anchor_low_idx = 0
    anchor_low = float(lows.iloc[0])
    swing_high_idx = 0
    swing_high = float(highs.iloc[0])

    state = "building_move"
    candidate: dict[str, Any] | None = None

    for pos in range(1, n):
        low = float(lows.iloc[pos])
        high = float(highs.iloc[pos])
        close = float(closes.iloc[pos])
        ts = idx[pos]

        if state == "building_move":
            if low < anchor_low:
                anchor_low_idx = pos
                anchor_low = low
                swing_high_idx = pos
                swing_high = high
            elif high > swing_high:
                swing_high_idx = pos
                swing_high = high

            if swing_high_idx > anchor_low_idx and anchor_low > 0:
                move_pct = (swing_high - anchor_low) / anchor_low * 100.0
                if move_pct >= min_move_pct:
                    candidate = {
                        "trend": "Uptrend",
                        "swing_low": anchor_low,
                        "swing_high": swing_high,
                        "swing_low_time": idx[anchor_low_idx],
                        "swing_high_time": idx[swing_high_idx],
                        "fib_level": fib_382_upmove(anchor_low, swing_high),
                        "move_pct": move_pct,
                        "first_touch_idx": None,
                        "first_touch_time": None,
                    }
                    state = "waiting_touch"
            continue

        if candidate is None:
            state = "building_move"
            continue

        if state == "waiting_touch":
            if high > swing_high:
                swing_high_idx = pos
                swing_high = high
                move_pct = (swing_high - anchor_low) / anchor_low * 100.0
                candidate.update(
                    {
                        "swing_high": swing_high,
                        "swing_high_time": idx[swing_high_idx],
                        "fib_level": fib_382_upmove(anchor_low, swing_high),
                        "move_pct": move_pct,
                    }
                )

            if low < anchor_low:
                events.append(_finalize_event(candidate, "InvalidBeforeTouch", ts))
                anchor_low_idx = pos
                anchor_low = low
                swing_high_idx = pos
                swing_high = high
                state = "building_move"
                candidate = None
                continue

            if pos > swing_high_idx and _touches_level(
                low, high, float(candidate["fib_level"]), level_tolerance_pct
            ):
                candidate["first_touch_idx"] = pos
                candidate["first_touch_time"] = ts
                state = "after_touch"
            continue

        # state == "after_touch"
        fib_level = float(candidate["fib_level"])
        band = fib_level * (level_tolerance_pct / 100.0)

        if low < anchor_low:
            events.append(_finalize_event(candidate, "InvalidAfterTouch", ts))
            anchor_low_idx = pos
            anchor_low = low
            swing_high_idx = pos
            swing_high = high
            state = "building_move"
            candidate = None
            continue

        if close < (fib_level - band):
            events.append(_finalize_event(candidate, "NotHeld", ts))
            first_touch_idx = int(candidate["first_touch_idx"])
            anchor_low_idx, anchor_low = _lowest_low_between(seg, first_touch_idx, pos)
            swing_high_idx = pos
            swing_high = high
            state = "building_move"
            candidate = None
            continue

        if high > swing_high:
            events.append(_finalize_event(candidate, "Held", ts))
            first_touch_idx = int(candidate["first_touch_idx"])
            anchor_low_idx, anchor_low = _lowest_low_between(seg, first_touch_idx, pos)
            swing_high_idx = pos
            swing_high = high
            state = "building_move"
            candidate = None
            continue

    if candidate is None:
        return events, None

    if state == "after_touch":
        active = _active_event(candidate, "ActiveTouchedNotResolved")
    else:
        active = _active_event(candidate, "ActiveWaitingTouch")

    return events, active


def _scan_downtrend_retracements(
    seg: pd.DataFrame, min_move_pct: float, level_tolerance_pct: float
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    events: list[dict[str, Any]] = []
    n = len(seg)
    if n < 3:
        return events, None

    lows = seg["Low"].astype(float)
    highs = seg["High"].astype(float)
    closes = seg["Close"].astype(float)
    idx = seg.index

    anchor_high_idx = 0
    anchor_high = float(highs.iloc[0])
    swing_low_idx = 0
    swing_low = float(lows.iloc[0])

    state = "building_move"
    candidate: dict[str, Any] | None = None

    for pos in range(1, n):
        low = float(lows.iloc[pos])
        high = float(highs.iloc[pos])
        close = float(closes.iloc[pos])
        ts = idx[pos]

        if state == "building_move":
            if high > anchor_high:
                anchor_high_idx = pos
                anchor_high = high
                swing_low_idx = pos
                swing_low = low
            elif low < swing_low:
                swing_low_idx = pos
                swing_low = low

            if swing_low_idx > anchor_high_idx and anchor_high > 0:
                move_pct = (anchor_high - swing_low) / anchor_high * 100.0
                if move_pct >= min_move_pct:
                    candidate = {
                        "trend": "Downtrend",
                        "swing_low": swing_low,
                        "swing_high": anchor_high,
                        "swing_low_time": idx[swing_low_idx],
                        "swing_high_time": idx[anchor_high_idx],
                        "fib_level": fib_382_downmove(swing_low, anchor_high),
                        "move_pct": move_pct,
                        "first_touch_idx": None,
                        "first_touch_time": None,
                    }
                    state = "waiting_touch"
            continue

        if candidate is None:
            state = "building_move"
            continue

        if state == "waiting_touch":
            if low < swing_low:
                swing_low_idx = pos
                swing_low = low
                move_pct = (anchor_high - swing_low) / anchor_high * 100.0
                candidate.update(
                    {
                        "swing_low": swing_low,
                        "swing_low_time": idx[swing_low_idx],
                        "fib_level": fib_382_downmove(swing_low, anchor_high),
                        "move_pct": move_pct,
                    }
                )

            if high > anchor_high:
                events.append(_finalize_event(candidate, "InvalidBeforeTouch", ts))
                anchor_high_idx = pos
                anchor_high = high
                swing_low_idx = pos
                swing_low = low
                state = "building_move"
                candidate = None
                continue

            if pos > swing_low_idx and _touches_level(
                low, high, float(candidate["fib_level"]), level_tolerance_pct
            ):
                candidate["first_touch_idx"] = pos
                candidate["first_touch_time"] = ts
                state = "after_touch"
            continue

        # state == "after_touch"
        fib_level = float(candidate["fib_level"])
        band = fib_level * (level_tolerance_pct / 100.0)

        if high > anchor_high:
            events.append(_finalize_event(candidate, "InvalidAfterTouch", ts))
            anchor_high_idx = pos
            anchor_high = high
            swing_low_idx = pos
            swing_low = low
            state = "building_move"
            candidate = None
            continue

        if close > (fib_level + band):
            events.append(_finalize_event(candidate, "NotHeld", ts))
            first_touch_idx = int(candidate["first_touch_idx"])
            anchor_high_idx, anchor_high = _highest_high_between(seg, first_touch_idx, pos)
            swing_low_idx = pos
            swing_low = low
            state = "building_move"
            candidate = None
            continue

        if low < swing_low:
            events.append(_finalize_event(candidate, "Held", ts))
            first_touch_idx = int(candidate["first_touch_idx"])
            anchor_high_idx, anchor_high = _highest_high_between(seg, first_touch_idx, pos)
            swing_low_idx = pos
            swing_low = low
            state = "building_move"
            candidate = None
            continue

    if candidate is None:
        return events, None

    if state == "after_touch":
        active = _active_event(candidate, "ActiveTouchedNotResolved")
    else:
        active = _active_event(candidate, "ActiveWaitingTouch")

    return events, active


def scan_fib_382_levels(
    seg: pd.DataFrame,
    trend: str,
    min_move_pct: float = 3.0,
    level_tolerance_pct: float = 0.10,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """
    Scan the active trend segment for every 38.2 setup whose impulse is >= min_move_pct.

    Returns:
      - resolved/invalid events
      - current active (unresolved) setup, if any
    """
    if seg is None or seg.empty:
        return [], None

    if trend == "Uptrend":
        return _scan_uptrend_retracements(seg, min_move_pct, level_tolerance_pct)
    if trend == "Downtrend":
        return _scan_downtrend_retracements(seg, min_move_pct, level_tolerance_pct)
    return [], None


def summarize_events(events: list[dict[str, Any]]) -> dict[str, int]:
    held_count = sum(1 for e in events if e["Status"] == "Held")
    not_held_count = sum(1 for e in events if e["Status"] == "NotHeld")
    invalid_count = sum(1 for e in events if e["Status"].startswith("Invalid"))
    retracement_count = sum(1 for e in events if e["FirstTouchTime"] != "")
    return {
        "held_count": held_count,
        "not_held_count": not_held_count,
        "invalid_count": invalid_count,
        "retracement_count": retracement_count,
    }


def _analyze_ticker_core(
    ticker: str,
    tolerance_pct: float,
    period: str,
    min_trend_segment_bars: int,
    min_move_pct: float,
    level_tolerance_pct: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    ticker = ticker.strip().upper()
    if not ticker:
        return {"Ticker": "", "Error": "Empty ticker"}, [], None

    df = get_15m_data(ticker, period=period)
    if df.empty or len(df) < 60:
        return {"Ticker": ticker, "Error": "Not enough data"}, [], None

    df = add_emas(df)
    trend = detect_trend_ema(df)

    if trend is None or trend == "Flat":
        return {"Ticker": ticker, "Error": "No trend (EMA20≈EMA50)"}, [], None

    swings = compute_trend_segment_swings(df, trend=trend, min_segment_bars=min_trend_segment_bars)
    if not swings:
        return {"Ticker": ticker, "Error": "Could not compute swings in trend segment"}, [], None

    swing_low = float(swings["swing_low"])
    swing_high = float(swings["swing_high"])
    if swing_high <= 0 or swing_low <= 0 or swing_high <= swing_low:
        return {"Ticker": ticker, "Error": "Invalid swing range"}, [], None

    if trend == "Uptrend":
        fib_level = fib_382_upmove(swing_low, swing_high)
    else:
        fib_level = fib_382_downmove(swing_low, swing_high)

    current_price = float(df["Close"].iloc[-1])
    distance_pct = abs(current_price - fib_level) / fib_level * 100.0
    hit = distance_pct <= tolerance_pct

    trend_start_pos = int(swings["trend_start_pos"])
    seg = df.iloc[trend_start_pos:].copy()
    events, active = scan_fib_382_levels(
        seg,
        trend=trend,
        min_move_pct=min_move_pct,
        level_tolerance_pct=level_tolerance_pct,
    )
    stats = summarize_events(events)
    latest_event = events[-1] if events else None

    summary = {
        "Ticker": ticker,
        "Trend": trend,
        "Hit": hit,
        "CurrentPrice": round(current_price, 4),
        "SwingHigh": round(swing_high, 4),
        "SwingLow": round(swing_low, 4),
        "Fib38.2": round(fib_level, 4),
        "DistancePct": round(distance_pct, 4),
        "TrendStartBarPos": trend_start_pos,
        "SwingLowTime": str(swings["swing_low_time"]),
        "SwingHighTime": str(swings["swing_high_time"]),
        "Any38.2Retracement>=3%Move": stats["retracement_count"] > 0,
        "RetracementCount>=3%Move": stats["retracement_count"],
        "HeldCount": stats["held_count"],
        "NotHeldCount": stats["not_held_count"],
        "InvalidCount": stats["invalid_count"],
        "LatestEventStatus": "" if latest_event is None else latest_event["Status"],
        "LatestEventTime": "" if latest_event is None else latest_event["ResolutionTime"],
        "ActiveLevelStatus": "" if active is None else active["Status"],
        "ActiveFib38.2": None if active is None else active["Fib38.2"],
        "ActiveMovePct": None if active is None else active["MovePct"],
        "Error": "",
    }

    return summary, events, active


def analyze_ticker(
    ticker: str,
    tolerance_pct: float = 0.30,
    period: str = "30d",
    min_trend_segment_bars: int = 120,
    min_move_pct: float = 3.0,
    level_tolerance_pct: float = 0.10,
) -> dict[str, Any]:
    summary, _, _ = _analyze_ticker_core(
        ticker=ticker,
        tolerance_pct=tolerance_pct,
        period=period,
        min_trend_segment_bars=min_trend_segment_bars,
        min_move_pct=min_move_pct,
        level_tolerance_pct=level_tolerance_pct,
    )
    return summary


def screen_all(
    tickers: list[str],
    tolerance_pct: float = 0.30,
    period: str = "30d",
    min_trend_segment_bars: int = 120,
    min_move_pct: float = 3.0,
    level_tolerance_pct: float = 0.10,
) -> pd.DataFrame:
    rows = []
    for t in tickers:
        rows.append(
            analyze_ticker(
                t,
                tolerance_pct=tolerance_pct,
                period=period,
                min_trend_segment_bars=min_trend_segment_bars,
                min_move_pct=min_move_pct,
                level_tolerance_pct=level_tolerance_pct,
            )
        )

    df_out = pd.DataFrame(rows)

    if "DistancePct" in df_out.columns and "Hit" in df_out.columns:
        df_out = df_out.sort_values(
            by=["Hit", "DistancePct", "Ticker"],
            ascending=[False, True, True],
            na_position="last",
        ).reset_index(drop=True)

    return df_out


def screen_all_with_event_log(
    tickers: list[str],
    tolerance_pct: float = 0.30,
    period: str = "30d",
    min_trend_segment_bars: int = 120,
    min_move_pct: float = 3.0,
    level_tolerance_pct: float = 0.10,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []

    for t in tickers:
        summary, events, active = _analyze_ticker_core(
            ticker=t,
            tolerance_pct=tolerance_pct,
            period=period,
            min_trend_segment_bars=min_trend_segment_bars,
            min_move_pct=min_move_pct,
            level_tolerance_pct=level_tolerance_pct,
        )
        summary_rows.append(summary)

        for ev in events:
            event_rows.append({"Ticker": summary.get("Ticker", t), **ev})

        if active is not None:
            event_rows.append({"Ticker": summary.get("Ticker", t), **active})

    summary_df = pd.DataFrame(summary_rows)
    if "DistancePct" in summary_df.columns and "Hit" in summary_df.columns:
        summary_df = summary_df.sort_values(
            by=["Hit", "DistancePct", "Ticker"],
            ascending=[False, True, True],
            na_position="last",
        ).reset_index(drop=True)

    events_df = pd.DataFrame(event_rows)
    if not events_df.empty:
        sort_cols = [c for c in ["Ticker", "ResolutionTime", "FirstTouchTime"] if c in events_df.columns]
        if sort_cols:
            events_df = events_df.sort_values(by=sort_cols).reset_index(drop=True)

    return summary_df, events_df


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(script_dir, "result.txt")

    if os.path.exists(input_file):
        with open(input_file, "r", encoding="utf-8") as f:
            watchlist = [line.strip().upper() for line in f if line.strip()]
    else:
        watchlist = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL"]

    print(f"Starting analysis for {len(watchlist)} tickers...\n")

    result_df, events_df = screen_all_with_event_log(
        watchlist,
        tolerance_pct=0.30,
        period="30d",
        min_trend_segment_bars=120,
        min_move_pct=3.0,
        level_tolerance_pct=0.10,
    )

    print(result_df.to_string(index=False))

    if not events_df.empty:
        print("\n38.2 level lifecycle events (impulse >= 3%):")
        print(events_df.to_string(index=False))

    output_file = os.path.join(script_dir, "fib382_hits.txt")
    hits_df = result_df[result_df["Hit"] == True].copy()
    with open(output_file, "w", encoding="utf-8") as f:
        for ticker in hits_df["Ticker"]:
            f.write(f"{ticker}\n")
    print("\nSaved hit tickers to:", output_file)

    events_file = os.path.join(script_dir, "fib382_events.csv")
    if not events_df.empty:
        events_df.to_csv(events_file, index=False)
        print("Saved lifecycle log to:", events_file)
