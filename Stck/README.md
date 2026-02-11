# Stock Scanner (15m Trend + Fibonacci 38.2%)

This script:

1. Reads ticker symbols from a text file (`A.txt` by default)
2. Pulls 15-minute candles from Yahoo Finance
3. Detects short-term trend (UP / DOWN / SIDEWAYS)
4. Checks whether the latest 15-minute candle touched the 38.2% Fibonacci retracement level
5. Writes results to an output text file (`scan_output.txt` by default)

## Usage

From the repository root:

```bash
python3 Stck/stock_scanner.py
```

Use custom files:

```bash
python3 Stck/stock_scanner.py -i Stck/A.txt -o Stck/scan_output.txt
```

Optional tuning:

```bash
python3 Stck/stock_scanner.py --trend-lookback 8 --swing-lookback 26
```

## Input format (`A.txt`)

- One ticker per line
- Empty lines are ignored
- Lines that start with `#` are ignored

Example:

```txt
# US stocks
AAPL
MSFT
TSLA
```

## Output fields

- `Trend`: Based on 15-minute closes
- `Change%`: Percent change for trend lookback window
- `Fib38.2`: 38.2% retracement level from recent swing window
- `Hit`: `YES` if latest 15-minute candle range touched the 38.2% level
