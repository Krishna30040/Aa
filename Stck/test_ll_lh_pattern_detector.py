import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ll_lh_pattern_detector import (
    Candle,
    build_demo_candles,
    find_ll_lh_ll_lh_ll,
    parse_yahoo_chart_payload,
)


class LlLhPatternDetectorTests(unittest.TestCase):
    def test_demo_has_exactly_one_match(self) -> None:
        candles = build_demo_candles()
        matches = find_ll_lh_ll_lh_ll(candles, left_bars=1, right_bars=1)

        self.assertEqual(1, len(matches))
        self.assertEqual(["LL", "LH", "LL", "LH", "LL"], [p.label for p in matches[0]])

    def test_no_match_returns_empty_list(self) -> None:
        candles = [
            Candle(index=i, timestamp=str(i), open=v, high=v + 0.4, low=v - 0.4, close=v)
            for i, v in enumerate([10.0, 10.4, 10.8, 11.1, 11.3, 11.7, 11.9, 12.2, 12.4, 12.8])
        ]
        matches = find_ll_lh_ll_lh_ll(candles, left_bars=1, right_bars=1)
        self.assertEqual([], matches)

    def test_parse_yahoo_payload_skips_sparse_rows(self) -> None:
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1700000000, 1700003600, 1700007200],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [1.0, None, 3.0],
                                    "high": [2.0, None, 4.0],
                                    "low": [0.5, None, 2.5],
                                    "close": [1.5, None, 3.5],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }

        candles = parse_yahoo_chart_payload(payload)
        self.assertEqual(2, len(candles))
        self.assertEqual(0, candles[0].index)
        self.assertEqual(1, candles[1].index)
        self.assertAlmostEqual(2.0, candles[0].high)
        self.assertAlmostEqual(2.5, candles[1].low)


if __name__ == "__main__":
    unittest.main()
