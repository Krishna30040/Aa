# Trend + Fibonacci 38.2 Analyzer

This project implements your workflow:

1. Read ticker symbols from a watchlist text file.
2. Download OHLC data from Yahoo Finance.
3. Convert OHLC timeframe data into range bars using a ticker-specific range size.
4. Detect swing highs/lows with ZigZag on raw candles (widely used approach).
5. Detect structure trend (`HH/HL` for uptrend, `LL/LH` for downtrend).
6. Detect EMA trend from raw OHLC candles with `EMA50` and `EMA200` (price above EMA200 = uptrend, otherwise downtrend).
7. In trend direction, check if retracement reaches 38.2% and holds.
8. If continuation happens, promote new fib anchors and keep scanning.
9. Mark all 38.2% touches as `hit`.

## Files

- `trend_fib_analyzer.py`: main script
- `A.txt`: watchlist file (one symbol per line)
- `requirements.txt`: Python dependency list

## Watchlist format

`A.txt` should contain one ticker per line:

```txt
AAPL
MSFT
NVDA
```

Blank lines and `# comments` are ignored.

## Hardcoded settings (no CLI inputs)

All runtime inputs are hardcoded at the top of `trend_fib_analyzer.py`:

- `WATCHLIST_PATH`
- `OUTPUT_PATH`
- `YAHOO_PERIOD`
- `YAHOO_INTERVAL`
- `YAHOO_AUTO_ADJUST`
- `RANGE_LOOKBACK`
- `RANGE_FACTOR`
- `PIVOT_SPAN`
- `ZIGZAG_REVERSAL_PCT`
- `FIB_TOLERANCE`

Edit those constants directly in the script when needed.

Note: Yahoo intraday intervals have lookback limits (for example, `15m` is
limited to about 60 days). The script automatically clamps period to Yahoo's
allowed range and prints the effective period used.

The script prefers ZigZag swing detection (`zigzag` package) and will fall back
to local pivot-based detection only if ZigZag is unavailable.

## Install and run

From `Stck/`:

```bash
python3 -m pip install -r requirements.txt
python3 trend_fib_analyzer.py
```

## Output

The JSON output includes per-ticker:

- range box size used
- structure trend + EMA trend + combined trend
- current price + timestamp
- latest swing low/high + timestamps
- latest fib 38.2 reference with anchor timestamps
- iterative 38.2% fib hit records (`hit`, `held_level`, `continuation`)
- generated file path: `analysis_output.json` (or your hardcoded `OUTPUT_PATH`)

The script also prints a concise per-ticker line in terminal output showing:

- current price and timestamp
- last analyzed swing low and swing high with timestamps (from raw OHLC candles)
- latest fib 38.2 level with fib low/high anchors and timestamps
- for current trend: fib retraced or not, and fib level held or not
