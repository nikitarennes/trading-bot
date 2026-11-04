"""Offline tests for paper rebalance ticker validation."""
import argparse
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_paper_rebalance import parse_tickers, run_paper_rebalance


class ParseTickersTests(unittest.TestCase):
    def test_normalizes_and_preserves_order(self):
        self.assertEqual(
            parse_tickers("wfrd, WING,LPX"),
            ["WFRD", "WING", "LPX"],
        )

    def test_requires_two_symbols(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_tickers("WFRD")

    def test_rejects_duplicates(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_tickers("WFRD,wfrd")

    def test_rejects_invalid_symbols(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_tickers("WFRD,INVALID SYMBOL")

    def test_submits_only_through_paper_client(self):
        analysis = SimpleNamespace(
            run_analysis=lambda tickers, **kwargs: (
                [{"ticker": ticker} for ticker in tickers],
                {"score": 50},
            )
        )
        calls = []

        def run_trading(**kwargs):
            calls.append(kwargs)
            return {"status": "completed", "results": []}

        trader = SimpleNamespace(run_trading=run_trading)
        with (
            patch.dict(os.environ, {
                "ALPACA_API_KEY": "test-key",
                "ALPACA_SECRET_KEY": "test-secret",
            }),
            patch.dict(sys.modules, {"main": analysis, "alpaca_trader": trader}),
        ):
            result = run_paper_rebalance(["WFRD", "WING"])

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["paper"], True)

    def test_missing_analysis_prevents_any_rebalance(self):
        analysis = SimpleNamespace(run_analysis=lambda *args, **kwargs: ([], None))
        calls = []
        trader = SimpleNamespace(run_trading=lambda **kwargs: calls.append(kwargs))
        with (
            patch.dict(os.environ, {
                "ALPACA_API_KEY": "test-key",
                "ALPACA_SECRET_KEY": "test-secret",
            }),
            patch.dict(sys.modules, {"main": analysis, "alpaca_trader": trader}),
        ):
            with self.assertRaisesRegex(RuntimeError, "no rebalance orders submitted"):
                run_paper_rebalance(["WFRD", "WING"])

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
