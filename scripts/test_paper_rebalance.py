"""Offline tests for weekly discovery-based paper rebalancing."""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from run_paper_rebalance import run_paper_rebalance, save_discovery_snapshot
import stock_discovery


class PaperRebalanceTests(unittest.TestCase):
    def setUp(self):
        self.universe = {
            "AAA": {"name": "Company A", "sector": "Industrials"},
            "BBB": {"name": "Company B", "sector": "Industrials"},
        }
        self.discovered = [
            {"ticker": "AAA", "category": "Core Long"},
            {"ticker": "CCC", "category": "Weak Long"},
        ]
        self.discovery = SimpleNamespace(
            load_ijh_universe=lambda: self.universe,
            discover_stocks=lambda universe: {
                "additions": self.discovered,
                "removals": [],
            },
        )
        self.analysis_calls = []
        self.analysis = SimpleNamespace(
            run_analysis=self._run_analysis,
        )
        self.trading_calls = []
        self.trader = SimpleNamespace(
            run_trading=self._run_trading,
        )
        self.modules = patch.dict(sys.modules, {
            "stock_discovery": self.discovery,
            "main": self.analysis,
            "alpaca_trader": self.trader,
        })
        self.modules.start()
        self.env = patch.dict(os.environ, {
            "ALPACA_API_KEY": "test-key",
            "ALPACA_SECRET_KEY": "test-secret",
            "FMP_API_KEY": "test-fmp-key",
        })
        self.env.start()
        self.addCleanup(self.modules.stop)
        self.addCleanup(self.env.stop)
        self.snapshot_writer = patch("run_paper_rebalance.save_discovery_snapshot")
        self.snapshot_writer.start()
        self.addCleanup(self.snapshot_writer.stop)

    def _run_analysis(self, tickers, **kwargs):
        self.analysis_calls.append((tickers, kwargs))
        return [{"ticker": ticker} for ticker in tickers], {"score": 50}

    def _run_trading(self, **kwargs):
        self.trading_calls.append(kwargs)
        return {"status": "completed", "results": []}

    def test_discovers_candidates_and_rebalances_paper_only(self):
        outcome = run_paper_rebalance(dry_run=True)

        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(self.analysis_calls[0][0], ["AAA", "CCC"])
        self.assertEqual(self.analysis_calls[0][1], {
            "skip_news": True,
            "skip_alpha": True,
        })
        self.assertIs(self.trading_calls[0]["paper"], True)
        self.assertIs(self.trading_calls[0]["dry_run"], True)

    def test_incomplete_candidate_analysis_prevents_trading(self):
        self.analysis.run_analysis = lambda tickers, **kwargs: (
            [{"ticker": tickers[0]}], {"score": 50}
        )

        with self.assertRaisesRegex(RuntimeError, "no rebalance orders submitted"):
            run_paper_rebalance()

        self.assertEqual(self.trading_calls, [])

    def test_fewer_than_two_discovery_candidates_skips_trading(self):
        self.discovered = [{"ticker": "AAA"}]
        outcome = run_paper_rebalance()

        self.assertEqual(outcome["status"], "skipped")
        self.assertEqual(self.analysis_calls, [])
        self.assertEqual(self.trading_calls, [])

    def test_missing_credentials_prevents_discovery(self):
        with patch.dict(os.environ, {"FMP_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "FMP_API_KEY"):
                run_paper_rebalance()
        self.assertEqual(self.analysis_calls, [])
        self.assertEqual(self.trading_calls, [])


class IjhUniverseTests(unittest.TestCase):
    def test_loads_equities_and_rejects_non_equity_rows(self):
        rows = ["Ticker,Name,Type,Weight,Sector"]
        rows.extend(
            f"TICK{i},Company {i},Equity,0.1,Industrials"
            for i in range(301)
        )
        rows.append("CASHXX,Cash,Cash,1.0,")
        response = SimpleNamespace(
            text="iShares header\n" + "\n".join(rows),
            raise_for_status=lambda: None,
        )

        with patch("stock_discovery.requests.get", return_value=response):
            universe = stock_discovery.load_ijh_universe()

        self.assertEqual(len(universe), 301)
        self.assertEqual(universe["TICK0"]["sector"], "Industrials")


class DiscoverySnapshotTests(unittest.TestCase):
    def test_persists_universe_additions_and_removals_without_credentials(self):
        universe = {"AAA": {"sector": "Industrials"}}
        discovery = {
            "additions": [{"ticker": "AAA", "market_cap": 4_000_000_000}],
            "removals": [{"ticker": "OLD", "reason": "failed screen"}],
            "report": "weekly screening report",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = save_discovery_snapshot(universe, discovery, output_dir=directory)
            with open(path, encoding="utf-8") as snapshot_file:
                snapshot = json.load(snapshot_file)

        self.assertEqual(snapshot["candidate_universe"], universe)
        self.assertEqual(snapshot["additions"], discovery["additions"])
        self.assertEqual(snapshot["removals"], discovery["removals"])
        self.assertIn("captured_at_utc", snapshot)
        self.assertNotIn("FMP_API_KEY", json.dumps(snapshot))


if __name__ == "__main__":
    unittest.main()
