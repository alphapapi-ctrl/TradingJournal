"""Cash/FX regressions; all writes are confined to disposable databases."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database as db
from brokers.tradingview import parse_tradingview_csv
from journal_mcp import operations, records, reporting
from utils.cashflows import unrealised

ROOT = Path(__file__).resolve().parents[1]
HEADER = "Symbol,Side,Qty,Fill Price,Commission,Closing Time\n"
DIVIDEND = "AMEX:TEST,Dividend,10,,,2026-07-31 00:00:00\n"


class IncomeFXCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "data", prefix="income-test-")
        self.path = Path(self.temp.name) / "isolated.db"
        self.patch = patch.object(db, "DB_PATH", self.path)
        self.patch.start()
        db.init_db()
        self.account = records.save_record("accounts", {"name": "Isolated AUD", "currency": "AUD"})["id"]
        self.rates = {"2026-07-01": self.fx(2, "2026-07-01"), "2026-07-31": self.fx(1.5, "2026-07-31")}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def fx(self, rate, day, field="close"):
        return {"account_per_quote": rate, "source": "Isolated fixture", "rate_date": day, "field": field, "approximate": True}

    def cash(self, **changes):
        return records.save_record("trades", {"account_id": self.account, "symbol": "TEST", "trade_type": "dividend",
            "quantity": 0, "entry_price": 0, "exit_price": 0, "entry_time": "2026-07-31", "exit_time": "2026-07-31",
            "pnl": 15, "status": "closed", **changes})

    def test_dividend_only_preview_and_repeated_import_preserve_cash(self):
        source = HEADER + DIVIDEND + DIVIDEND
        preview = operations.import_trades("cash.csv", source, self.account, include_dividends=True, fx_rates=self.rates)
        self.assertEqual(preview["dividends"], 2)
        self.assertEqual(records.list_records("trades")["total"], 0)
        self.assertEqual(preview["preview"][0]["pnl"], 15)
        saved = operations.import_trades("cash.csv", source, self.account, dry_run=False, include_dividends=True, fx_rates=self.rates)
        self.assertEqual(saved["created"], 2)
        original = records.list_records("trades")["records"]
        repeated = operations.import_trades("cash.csv", source, self.account, dry_run=False, include_dividends=True)
        self.assertEqual(repeated["created"], 0)
        self.assertEqual(repeated["skipped_existing"], 2)
        self.assertEqual(records.list_records("trades")["records"], original)
        self.assertTrue(any(r["broker_trade_id"].endswith("_2") for r in original))

    def test_reconciled_ids_and_provenance_are_not_overwritten(self):
        row = parse_tradingview_csv((HEADER + DIVIDEND).encode())[0]
        legacy = {"quote_currency": "USD", "account_currency": "AUD", "dividend_import_20261103": {
            "amount_original": 10, "amount_aud": 15, "fx": self.fx(1.5, "2026-07-31"), "source": {"sha256": "fixture"}}}
        saved = self.cash(broker="TradingView", broker_trade_id=row["broker_trade_id"], raw_data=legacy)
        result = operations.import_trades("cash.csv", HEADER + DIVIDEND, self.account, dry_run=False, include_dividends=True)
        self.assertEqual(result["skipped_existing"], 1)
        self.assertEqual(records.get_record("trades", saved["id"]), saved)

    def test_mixed_file_converts_each_cash_leg_and_deducts_fees_once(self):
        source = HEADER + "AMEX:TEST,Buy,2,100,1,2026-07-01 00:00:00\n" + DIVIDEND + "AMEX:TEST,Sell,2,110,2,2026-07-31 00:00:00\n"
        result = operations.import_trades("mixed.csv", source, self.account, dry_run=False, include_dividends=True, fx_rates=self.rates)
        self.assertEqual(result["created"], 2)
        report = reporting.generate_report(account_id=self.account, start_date="2026-07-31", end_date="2026-07-31")
        section = report["currencies"]["AUD"]
        self.assertEqual(report["trade_count"], 1)
        self.assertEqual(report["cash_event_count"], 1)
        self.assertEqual(section["performance"]["net_pnl"], -75)  # 330 - 400 - (2 + 3)
        self.assertEqual(section["cashflow"]["dividend_income"], 15)
        self.assertEqual(section["cashflow"]["realised_cash_pnl"], -60)
        self.assertEqual(section["discipline"]["closed_trades"], 1)
        self.assertEqual(section["performance"]["win_rate"], 0)
        self.assertTrue(all(e["date"].startswith("2026-07-31") for e in section["cashflow"]["equity_curve"]))
        again = operations.import_trades("mixed.csv", source, self.account, dry_run=False, include_dividends=True, fx_rates=self.rates)
        self.assertEqual(again["created"], 0)
        self.assertEqual(records.list_records("trades")["total"], 2)
        self.assertEqual(reporting.generate_report(account_id=self.account)["currencies"]["AUD"]["cashflow"]["realised_cash_pnl"], -60)

    def test_cash_validation_and_test_exclusion(self):
        for change in ({"quantity": 1}, {"status": "open", "exit_time": None, "exit_price": None}, {"entry_price": 1},
                       {"exit_price": None}, {"exit_time": "2026-08-01"}, {"pnl": float("nan")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.cash(**change)
        actual = self.cash()
        self.cash(broker="DUMMY", broker_trade_id="DUMMY_income", pnl=1000)
        self.cash(is_test=1, pnl=500)
        self.cash(raw_data={"is_test": True}, pnl=500)
        report = reporting.generate_report(account_id=self.account)
        self.assertEqual(report["excluded_test_records"], 3)
        self.assertEqual(report["trade_count"], 0)
        self.assertEqual(report["currencies"]["AUD"]["cashflow"]["realised_cash_pnl"], 15)
        self.assertEqual(reporting.generate_report(account_id=self.account, include_test=True)["cash_event_count"], 4)
        with self.assertRaises(ValueError):
            records.trade_action("reopen", actual["id"])
        self.assertEqual(records.list_records("trades")["total"], 4)

    def test_missing_future_stale_or_nonfinite_fx_is_rejected_before_writing(self):
        for rates in (None, {}, {"2026-07-31": self.fx(1.5, "2026-08-01")}, {"2026-07-31": self.fx(1.5, "2026-07-01")},
                      {"2026-07-31": self.fx(float("nan"), "2026-07-31")}):
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                operations.import_trades("cash.csv", HEADER + DIVIDEND, self.account, dry_run=False, include_dividends=True, fx_rates=rates)
        self.assertEqual(records.list_records("trades")["total"], 0)

    def test_open_fx_cost_basis_and_realised_income_remain_separate(self):
        source = HEADER + "AMEX:TEST,Buy,2,100,1,2026-07-01 00:00:00\n" + DIVIDEND
        result = operations.import_trades("open.csv", source, self.account, dry_run=False, include_dividends=True, fx_rates=self.rates)
        row = next(r for r in records.list_records("trades")["records"] if r["status"] == "open")
        result = reporting.generate_report(account_id=self.account, valuations=[{"trade_id": row["id"], "price": 110,
            "quote_currency": "USD", "as_of": "2026-07-31", "fx": self.fx(1.5, "2026-07-31")}])["currencies"]["AUD"]
        self.assertEqual(result["unrealised"]["pnl"], -72)  # USD price gain, but AUD FX/cost-basis loss and A$2 entry fee.
        self.assertEqual(result["cashflow"]["dividend_income"], 15)
        self.assertEqual(result["total_return"]["pnl"], -57)
        unknown = unrealised(row, "AUD", 110, as_of="2026-07-31")
        self.assertIsNone(unknown["unrealised_pnl"])
        with self.assertRaises(ValueError):
            records.trade_action("close", row["id"], exit_price=110, exit_time="2026-07-31")

    def test_opening_fx_and_latest_prior_date_are_preserved(self):
        rates = {"2026-07-30": self.fx(1.4, "2026-07-30", "open")}
        result = operations.import_trades("cash.csv", HEADER + DIVIDEND, self.account, include_dividends=True, fx_rates=rates)
        meta = json.loads(result["preview"][0]["fx_metadata"])
        self.assertEqual(meta["payment"]["requested_date"], "2026-07-31")
        self.assertEqual(meta["payment"]["rate_date"], "2026-07-30")
        self.assertEqual(meta["payment"]["field"], "open")
        row = {"entry_time": "2026-07-01", "quantity": 2, "entry_price": 100, "direction": "LONG", "quote_currency": "USD",
               "raw_data": {"reconciliation_20261103": {"source_buy": {"kind": "user_authorised_demo_rebalance", "file": "rebalance_Open.csv"}}}}
        with self.assertRaises(ValueError):
            unrealised(row, "AUD", 110, self.fx(1.5, "2026-07-31"), self.fx(2, "2026-07-01"), "2026-07-31")
        self.assertEqual(unrealised(row, "AUD", 110, self.fx(1.5, "2026-07-31"), self.fx(2, "2026-07-01", "open"), "2026-07-31")["unrealised_pnl"], -70)

    def test_browser_reports_and_open_panel_keep_cash_separate(self):
        from streamlit.testing.v1 import AppTest
        source = HEADER + "AMEX:TEST,Buy,2,100,1,2026-07-01 00:00:00\n" + DIVIDEND
        operations.import_trades("open.csv", source, self.account, dry_run=False, include_dividends=True, fx_rates=self.rates)
        with patch("pages.journal._live_quote", return_value=("TEST", 110, 1.5, "USD", "2026-07-31")), \
             patch("pages.journal._live_fx", return_value=self.fx(1.5, "2026-07-31")):
            panel = AppTest.from_string("from pages.journal import _open_positions_section\n_open_positions_section()").run(timeout=20)
        self.assertFalse(panel.exception, str(panel.exception))
        self.assertTrue(any("-72.00" in item.value for item in panel.markdown))
        self.assertTrue(any("15.00" in item.value and "Recorded cash distributions" in item.value for item in panel.caption))
        self.assertTrue(any("excluded from P&L totals" in item.value for item in panel.caption))
        report = AppTest.from_string(f"from pages.statistics import _general_stats\n_general_stats({self.account})").run(timeout=20)
        self.assertFalse(report.exception, str(report.exception))
        self.assertTrue(any(metric.label == "Dividend cash (AUD)" and metric.value == "+15.00" for metric in report.metric))

    def test_browser_import_preview_retains_native_cash_type(self):
        from streamlit.testing.v1 import AppTest
        csv_text = HEADER + DIVIDEND
        # Use a same-currency synthetic source to exercise the browser commit path.
        app = AppTest.from_string(f"from pages.import_trades import _show_import_preview\nfrom brokers.tradingview import parse_tradingview_csv\n_show_import_preview(parse_tradingview_csv({csv_text.encode()!r}), 'TradingView', 'test.csv', {self.account})").run(timeout=20)
        self.assertFalse(app.exception, str(app.exception))
        app.text_input(key="tv_quote_currency").set_value("AUD").run()
        app.checkbox[0].check().run()
        # Prevent the success rerun from returning to an unrelated page.
        button = next(button for button in app.button if "Import 1 records" in button.label)
        button.click().run()
        self.assertFalse(app.exception, str(app.exception))
        row = records.list_records("trades")["records"][0]
        self.assertEqual(row["trade_type"], "dividend")
        self.assertEqual(row["quantity"], 0)
        self.assertEqual(row["pnl"], 10)

    def test_fifo_entries_use_each_purchase_dates_fx(self):
        source = HEADER + "AMEX:TEST,Buy,1,100,1,2026-07-01 00:00:00\nAMEX:TEST,Buy,1,100,1,2026-07-15 00:00:00\nAMEX:TEST,Sell,2,110,2,2026-07-31 00:00:00\n"
        rates = {**self.rates, "2026-07-15": self.fx(1.8, "2026-07-15")}
        operations.import_trades("fifo.csv", source, self.account, dry_run=False, fx_rates=rates)
        result = reporting.generate_report(account_id=self.account)["currencies"]["AUD"]
        self.assertEqual(result["performance"]["net_pnl"], -56.8)
        row = records.list_records("trades")["records"][0]
        self.assertEqual(json.loads(row["fx_metadata"])["cost_basis_account"], 380)

    def test_sidebar_does_not_sum_currencies_or_count_income_as_wins(self):
        from utils.sidebar_stats import get_sidebar_stats
        self.cash()
        self.cash(broker="DUMMY", pnl=1000)
        records.save_record("trades", {"account_id": self.account, "symbol": "TEST", "quantity": 1, "entry_price": 100,
            "entry_time": "2026-07-01", "exit_time": "2026-07-31", "status": "closed", "pnl": 10, "raw_data": {"quote_currency": "USD"}})
        stats = get_sidebar_stats()
        self.assertEqual(stats["total_closed"], 1)
        self.assertEqual(stats["wins_closed"], 0)
        self.assertEqual(stats["unvalued_closed"], 1)
        self.assertEqual(stats["net_pnl_by_currency"]["AUD"], 15)

    def test_fx_metadata_cannot_relabel_an_existing_closed_amount(self):
        trade = records.save_record("trades", {"account_id": self.account, "symbol": "TEST", "quantity": 1, "entry_price": 100,
            "entry_time": "2026-07-01", "exit_time": "2026-07-31", "exit_price": 110, "status": "closed", "pnl": 10})
        change = {"quote_currency": "USD", "fx_metadata": {"quote_currency": "USD", "pnl_currency": "AUD", "cost_currency": "AUD",
                 "entry": self.fx(2, "2026-07-01"), "exit": self.fx(1.5, "2026-07-31")}}
        with self.assertRaises(ValueError):
            records.save_record("trades", change, trade["id"])
        saved = records.save_record("trades", {**change, "pnl": -35}, trade["id"])
        self.assertEqual(saved["pnl"], -35)
        records.save_record("trades", {"raw_data": {"reconciliation_20261103": {"entry_discrepancy": "Provisional source conflict"}}}, trade["id"])
        self.assertEqual(reporting.generate_report(account_id=self.account)["currencies"]["AUD"]["provisional_trade_ids"], [trade["id"]])


if __name__ == "__main__":
    unittest.main()
