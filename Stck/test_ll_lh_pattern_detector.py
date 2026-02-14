import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ll_lh_pattern_detector import Candle, build_demo_candles, find_ll_lh_ll_lh_ll


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


if __name__ == "__main__":
    unittest.main()
