# Trend + Fibonacci 38.2 Analyzer

This project implements your workflow:

1. Read ticker symbols from a watchlist text file.
2. Convert OHLC timeframe data into range bars using a ticker-specific range size.
3. Detect structure trend (`HH/HL` for uptrend, `LL/LH` for downtrend).
4. Detect EMA trend with `EMA50` and `EMA200`.
5. In trend direction, check if retracement reaches 38.2% and holds.
6. If continuation happens, promote new fib anchors and keep scanning.
7. Mark all 38.2% touches as `hit`.

## Files

- `trend_fib_analyzer.py`: main script
- `A.txt`: watchlist file (one symbol per line)

## Input format

Create one CSV file per symbol in a data directory, e.g.:

- `data/AAPL.csv`
- `data/MSFT.csv`

CSV header must include at least:

- `Open`, `High`, `Low`, `Close`

Optional timestamp column names:

- `Timestamp`, `Datetime`, `Date`, or `Time`

## Watchlist format

`A.txt` should contain one ticker per line:

```txt
AAPL
MSFT
NVDA
```

Blank lines and `# comments` are ignored.

## Run

From `Stck/`:

```bash
python trend_fib_analyzer.py \
  --watchlist A.txt \
  --data-dir data \
  --range-factor 0.25 \
  --output analysis_output.json
```

## Output

The JSON output includes per-ticker:

- range box size used
- structure trend + EMA trend + combined trend
- iterative 38.2% fib hit records (`hit`, `held_level`, `continuation`)
