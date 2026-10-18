"""Isolated integration tests: no access to the user's trading database."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import database as db
from journal_mcp import operations as ops, records as r, reporting
from journal_mcp.server import create_server
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mcp-test-", dir=ROOT / "data")
        self.original_db = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / "journal.db"
        db.init_db()
        self.account = r.save_record("accounts", {"name": "Test", "broker": "Manual", "currency": "USD"})["id"]

    def tearDown(self):
        db.DB_PATH = self.original_db
        self.temp.cleanup()

    def trade(self, **changes):
        return r.save_record("trades", {"account_id": self.account, "symbol": "ABC", "direction": "LONG",
                                       "quantity": 10, "entry_price": 100, "entry_time": "2026-10-01T09:00:00", **changes})

    def test_patch_journal_and_relationship_validation(self):
        trade = self.trade()
        entry = r.save_record("journal_entries", {"entry_type": "trade", "entry_date": "2026-10-01", "trade_id": trade["id"],
                               "stage": "pre", "pre_analysis": "Setup", "pre_plan": "Buy", "pre_body_state": "Calm", "custom_fields": {"x": 1}})
        changed = r.save_record("journal_entries", {"pre_plan": "Wait", "pre_body_state": None}, entry["id"])
        self.assertEqual(changed["pre_analysis"], "Setup")
        self.assertEqual(changed["pre_plan"], "Wait")
        self.assertIsNone(changed["pre_body_state"])
        self.assertEqual(changed["account_id"], self.account)
        self.assertEqual(json.loads(changed["custom_fields"]), {"x": 1})
        with self.assertRaises(ValueError):
            r.save_record("journal_entries", {"trade_id": 99999}, entry["id"])

    def test_merge_close_partial_and_reopen(self):
        a = self.trade(commission=10, swap=2, planned_risk=100)
        b = self.trade(entry_price=110)
        pos = r.position_action("merge", [a["id"], b["id"]])
        self.assertEqual(pos["avg_entry_price"], 105)
        closed = r.trade_action("partial_close", a["id"], 120, "2026-10-02T10:00:00", 4, extra_commission=1)
        self.assertEqual(closed["trade"]["quantity"], 6)
        self.assertEqual(closed["closed_trade"]["pnl"], 80)
        self.assertAlmostEqual(closed["closed_trade"]["commission"], 5)
        self.assertAlmostEqual(closed["closed_trade"]["swap"], 0.8)
        self.assertAlmostEqual(closed["trade"]["swap"], 1.2)
        self.assertAlmostEqual(closed["trade"]["planned_risk"], 60)
        self.assertEqual(r.details("positions", pos["id"])["total_quantity"], 20)
        r.trade_action("close", b["id"], 115, "2026-10-02T11:00:00")
        self.assertEqual(r.details("positions", pos["id"])["total_pnl"], 130)
        r.trade_action("reopen", b["id"])
        self.assertEqual(r.details("positions", pos["id"])["total_pnl"], 80)
        r.position_action("unmerge", position_id=pos["id"])
        self.assertIsNone(r.get_record("trades", a["id"])["position_id"])

    def test_invalid_writes_and_cross_account_merge_are_rejected(self):
        for patch_data in ({"quantity": -1}, {"entry_price": float("nan")}, {"direction": "BUY"}, {"symbol": ""}, {"unknown": 1}):
            with self.assertRaises(ValueError):
                self.trade(**patch_data)
        a = self.trade()
        other = r.save_record("accounts", {"name": "Other"})["id"]
        b = self.trade(account_id=other)
        with self.assertRaises(ValueError):
            r.position_action("merge", [a["id"], b["id"]])
        with self.assertRaises(ValueError):
            r.trade_action("partial_close", a["id"], 110, close_quantity=10)
        with self.assertRaises(ValueError):
            r.save_record("trades", {"status": "closed", "exit_time": "2026-10-02"}, a["id"])
        self.assertEqual(r.list_records("positions")["total"], 0)

    def test_notes_patch_preserves_broker_pnl_and_exit_time_validation(self):
        trade = self.trade(status="closed", exit_price=110, exit_time="2026-10-02", pnl=999)
        updated = r.save_record("trades", {"raw_data": "Notes only"}, trade["id"])
        self.assertEqual(updated["pnl"], 999)
        with self.assertRaises(ValueError):
            self.trade(status="closed", exit_time="2026-10-01T08:59:00")
        from utils.trade_ops import upsert_trade_from_broker
        content = {"account_id": self.account, "broker": "Test", "broker_trade_id": "42", "symbol": "ABC", "direction": "LONG",
                   "entry_price": 1, "exit_price": None, "quantity": 1, "entry_time": "2026-10-01", "exit_time": None, "pnl": 0, "status": "open"}
        first, new = upsert_trade_from_broker(content)
        other = r.save_record("accounts", {"name": "Second"})["id"]
        second, new = upsert_trade_from_broker({**content, "account_id": other})
        self.assertTrue(new)
        self.assertNotEqual(first, second)

    def test_transaction_rolls_back_multi_step_operation(self):
        a, b = self.trade(), self.trade()
        with patch("utils.trade_ops.merge_trades_into_position", side_effect=lambda *args: (db.execute("UPDATE trades SET pnl=999"), (_ for _ in ()).throw(ValueError("failure")))):
            with self.assertRaises(ValueError):
                r.position_action("merge", [a["id"], b["id"]])
        self.assertEqual(r.get_record("trades", a["id"])["pnl"], 0)
        with self.assertRaises(ValueError):
            with db.transaction():
                r.save_record("accounts", {"name": "Rolled back"})
                raise ValueError("rollback nested savepoint")
        self.assertEqual(r.list_records("accounts")["total"], 1)

    def test_playbook_or_groups_and_rule_delete(self):
        pb = r.save_record("playbooks", {"name": "Breakout"})["id"]
        rules = [r.save_record("playbook_rules", {"playbook_id": pb, "name": f"Rule {i}", "rule_type": "required", "rule_group": "either"}) for i in range(2)]
        threshold = r.save_record("playbook_risk_rules", {"playbook_id": pb, "min_required_pct": 100, "risk_level": "no_trade", "risk_multiplier": 0})
        self.assertEqual(r.evaluate_playbook(pb, {str(rules[0]["id"]): True})["required_pct"], 100)
        self.assertEqual(r.evaluate_playbook(pb, {})["risk_level"], "no_trade")
        trade = self.trade()
        r.assign_playbook(pb, {str(rules[0]["id"]): True}, trade_id=trade["id"])
        self.assertEqual(r.get_record("trades", trade["id"])["risk_score"], 100)
        r.delete_record("playbook_rules", rules[0]["id"])
        self.assertEqual(r.get_record("trades", trade["id"])["risk_score"], 40)
        r.delete_record("playbooks", pb)
        self.assertIsNone(r.get_record("trades", trade["id"])["playbook_id"])
        self.assertEqual(r.list_records("playbook_risk_rules")["total"], 0)

    def test_report_pnl_costs_inclusive_dates_and_deduplicated_reviews(self):
        a = self.trade(status="closed", exit_time="2026-10-02T15:30:00", exit_price=110, pnl=100, commission=2, swap=1)
        b = self.trade(status="closed", entry_time="2026-10-01T16:59:00", exit_time="2026-10-02", exit_price=95, pnl=-50, commission=2)
        pos = r.position_action("merge", [a["id"], b["id"]])
        r.save_record("journal_entries", {"entry_type": "trade", "entry_date": "2026-10-01", "position_id": pos["id"], "stage": "pre", "pre_plan": "Plan"})
        r.save_record("journal_entries", {"entry_type": "trade", "entry_date": "2026-10-02", "position_id": pos["id"], "grade": "A", "mood": 8, "trade_category": 1})
        r.save_record("journal_entries", {"entry_type": "trade", "entry_date": "2026-10-02", "trade_id": a["id"], "grade": "B", "mood": 6, "trade_category": 3})
        report = reporting.generate_report(account_id=self.account, start_date="2026-10-01", end_date="2026-10-01")
        section = report["currencies"]["USD"]
        self.assertEqual(section["performance"]["net_pnl"], 45)
        self.assertEqual(section["performance"]["max_drawdown"], -52)
        self.assertEqual(section["discipline"]["planned_and_reviewed"], 2)
        self.assertEqual(section["discipline"]["average_mood"], 7)
        self.assertEqual(sum(v["total_trades"] for v in section["categories"]), 2)
        json.dumps(report, allow_nan=False)

    def test_reports_separate_currencies_and_bound_work(self):
        self.trade(status="closed", exit_time="2026-10-02", pnl=10)
        aud = r.save_record("accounts", {"name": "AUD", "currency": "AUD"})["id"]
        self.trade(account_id=aud, status="closed", exit_time="2026-10-02", pnl=20)
        report = reporting.generate_report()
        self.assertEqual(set(report["currencies"]), {"USD", "AUD"})
        with patch.object(reporting, "MAX_REPORT_TRADES", 1), self.assertRaises(ValueError):
            reporting.generate_report()
        self.assertEqual(reporting.performance([{"id": 1, "pnl": -10}])["max_drawdown"], -10)

    def test_playbook_report_does_not_classify_unassessed_trades_as_failed(self):
        pb = r.save_record("playbooks", {"name": "Plan"})["id"]
        rule = r.save_record("playbook_rules", {"playbook_id": pb, "name": "Required", "rule_type": "required"})
        r.save_record("playbook_risk_rules", {"playbook_id": pb, "risk_level": "no_trade", "min_required_pct": 100})
        checked = self.trade(status="closed", exit_time="2026-10-02", pnl=100)
        r.assign_playbook(pb, {str(rule["id"]): True}, trade_id=checked["id"])
        self.trade(status="closed", exit_time="2026-10-02", pnl=-10, playbook_id=pb)
        report = reporting.generate_report("playbooks", account_id=self.account)
        plan = report["currencies"]["USD"]["playbooks"][0]
        self.assertEqual(plan["unassessed_trades"], 1)
        self.assertEqual(plan["rules"][0]["compliance_pct"], 100)
        self.assertEqual(plan["risk_outcomes"][0]["total_trades"], 1)
        self.assertEqual(plan["risk_outcomes"][0]["risk_level"], "normal")

    def test_pagination_search_and_sql_identifier_rejection(self):
        self.trade(symbol="ABC_1")
        self.trade(symbol="ABCX1")
        page = r.list_records("trades", limit=1)
        self.assertEqual(page["next_offset"], 1)
        self.assertIsNone(r.list_records("trades", limit=1, offset=1)["next_offset"])
        self.assertEqual(r.list_records("trades", search="ABC_1")["total"], 1)
        for entity, filters in (("trades; DROP TABLE trades", {}), ("trades", {"id) OR 1=1 --": 1})):
            with self.assertRaises(ValueError):
                r.list_records(entity, filters)

    def test_export_report_backup_and_path_containment(self):
        trade = self.trade()
        exported = ops.export_records("trades")
        self.assertEqual(exported["exported"], 1)
        chunk = ops.read_export(exported["filename"])
        self.assertIn(b"ABC", base64.b64decode(chunk["content"]))
        saved = ops.save_report(account_id=self.account)
        self.assertIn("Trading journal report", ops.export_path(saved["filename"]).read_text())
        for name in ("../journal.db", "C:\\journal.db", "..\\journal.db"):
            with self.assertRaises(ValueError):
                ops.read_export(name)
        backup = ops.backup_database()
        with closing(sqlite3.connect(backup["path"])) as conn:
            self.assertEqual(conn.execute("SELECT symbol FROM trades WHERE id=?", (trade["id"],)).fetchone()[0], "ABC")

    def test_settings_and_risk_calculator(self):
        result = ops.settings("update", {"risk_pct": 1, "ftp_password": "test-secret"})
        self.assertEqual(result["settings"]["ftp_password"], "[redacted]")
        self.assertEqual(ops.settings(include_secrets=True)["settings"]["ftp_password"], "test-secret")
        sizing = ops.risk_calculator(100, 95, 10000)
        self.assertEqual(sizing["quantity"], 20)
        with self.assertRaises(ValueError):
            ops.risk_calculator(100, 105, 10000)

    def test_deletes_preserve_text_and_clear_links(self):
        trade = self.trade()
        entry = r.save_record("journal_entries", {"entry_type": "trade", "entry_date": "2026-10-01", "trade_id": trade["id"], "lessons": "Keep this"})
        r.delete_record("trades", trade["id"])
        self.assertEqual(r.get_record("journal_entries", entry["id"])["lessons"], "Keep this")
        self.assertIsNone(r.get_record("journal_entries", entry["id"])["trade_id"])
        r.delete_record("accounts", self.account)
        self.assertIsNone(r.get_record("journal_entries", entry["id"])["account_id"])

    def test_import_preview_idempotency_and_account_isolation(self):
        content = "Ticket,Open Time,Type,Size,Symbol,Open Price,Close Time,Close Price,Commission,Taxes,Swap,Profit\n42,2026-10-01 09:00:00,buy,1,EURUSD,1.1,2026-10-02 10:00:00,1.2,2,0,1,100\n"
        preview = ops.import_trades("icmarkets.csv", content, self.account)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(r.list_records("trades")["total"], 0)
        self.assertEqual(ops.import_trades("icmarkets.csv", content, self.account, dry_run=False)["created"], 1)
        self.assertEqual(ops.import_trades("icmarkets.csv", content, self.account, dry_run=False)["updated"], 1)
        other = r.save_record("accounts", {"name": "Other"})["id"]
        self.assertEqual(ops.import_trades("icmarkets.csv", content, other, dry_run=False)["created"], 1)
        self.assertEqual(r.list_records("trades")["total"], 2)

    def test_import_failure_rolls_back_all_rows(self):
        parsed = [{"symbol": "ABC", "direction": "LONG", "entry_price": 10, "quantity": 1, "entry_time": "2026-10-01", "status": "open", "broker_trade_id": str(i)} for i in range(2)]
        real_save = r.save_record
        count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError("injected import failure")
            return real_save(*args, **kwargs)
        with patch("brokers.importers.auto_detect_and_parse", return_value=(parsed, "Test")), patch.object(r, "save_record", side_effect=fail_second), self.assertRaises(ValueError):
            ops.import_trades("test.csv", "test", self.account, dry_run=False)
        self.assertEqual(r.list_records("trades")["total"], 0)
        self.assertEqual(r.list_records("import_history")["total"], 0)

    def test_generated_entities_schema_and_replay_session(self):
        self.assertTrue(r.schema("day_features")["entities"]["day_features"]["read_only"])
        with self.assertRaises(ValueError):
            r.save_record("day_features", {"date": "2026-10-01"})
        with self.assertRaises(ValueError):
            r.save_record("positions", {"notes": "No trades"})
        session = ops.replay_action("create_session", data={"name": "Test replay", "instrument": "DEUIDXEUR", "day": "2026-10-01", "balance": 10000})
        account = r.get_record("accounts", session["account_id"])
        self.assertEqual(account["account_type"], "Demo")
        r.save_record("replay_sessions", {"cursor": 5, "pending_orders": []}, session["id"])
        self.assertEqual(r.get_record("replay_sessions", session["id"])["cursor"], 5)

    def test_official_mcp_client_in_process(self):
        async def run():
            async with Client(create_server()) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual(len(tools), 38)
                self.assertTrue(all(tool.description for tool in tools))
                result = await client.call_tool("list_records", {"entity": "accounts"})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["total"], 1)
                error = await client.call_tool("save_record", {"entity": "trades", "data": {"symbol": "Missing quantity"}})
                self.assertTrue(error.is_error)
                resource = await client.read_resource("journal://schema")
                self.assertIn("entities", resource.contents[0].text)
        asyncio.run(asyncio.wait_for(run(), timeout=15))

    def test_short_lived_worker_writes_to_selected_database(self):
        from journal_mcp.server import isolated_call
        content = "Ticket,Open Time,Type,Size,Symbol,Open Price,Close Time,Close Price,Commission,Taxes,Swap,Profit\n42,2026-10-01,buy,1,EURUSD,1.1,2026-10-02,1.2,2,0,1,100\n"
        result = isolated_call("import_trades", {"filename": "icmarkets.csv", "content": content, "account_id": self.account, "dry_run": False})
        self.assertEqual(result["created"], 1)
        self.assertEqual(r.list_records("trades")["total"], 1)


class TransportCase(unittest.TestCase):
    def test_real_http_lan_transport_and_stdio(self):
        with tempfile.TemporaryDirectory(prefix="mcp transport ", dir=ROOT / "data") as folder:
            database = str(Path(folder) / "transport.db")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with (Path(folder) / "server.log").open("w+") as log:
                process = subprocess.Popen([sys.executable, str(ROOT / "mcp_server.py"), "--host", "0.0.0.0", "--port", str(port), "--database", database],
                                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=log,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    url = f"http://127.0.0.1:{port}"
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            with urlopen(url + "/health", timeout=1) as response:
                                self.assertEqual(json.load(response)["service"], "trading-journal-mcp")
                            break
                        except OSError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.flush()
                                log.seek(0)
                                self.fail(log.read())
                            time.sleep(0.1)
                    async def run():
                        async with Client(url + "/mcp") as client:
                            result = await client.call_tool("save_record", {"entity": "accounts", "data": {"name": "HTTP account"}})
                            self.assertFalse(result.is_error, str(result))
                            diagnostics = await client.call_tool("diagnostics")
                            self.assertFalse(diagnostics.structured_content["authentication"])
                            self.assertEqual(len((await client.list_tools()).tools), 38)
                            baseline = diagnostics.structured_content
                            self.assertFalse(baseline["data_libraries_in_main_process"])
                            account_id = result.structured_content["id"]
                            # Representative report workload, seeded only into the test DB.
                            with closing(sqlite3.connect(database)) as conn:
                                conn.executemany("INSERT INTO trades(account_id,symbol,direction,quantity,entry_price,entry_time,exit_time,pnl,commission,swap,status) VALUES (?,?,'LONG',1,100,'2026-10-01T12:00:00','2026-10-02',?,1,0,'closed')",
                                                 [(account_id, f"SYM{i % 20}", 10 if i % 3 else -5) for i in range(5000)])
                                conn.commit()
                            start = time.monotonic()
                            report = await client.call_tool("generate_report", {"account_id": account_id})
                            report_seconds = time.monotonic() - start
                            self.assertEqual(report.structured_content["trade_count"], 5000)
                            after = (await client.call_tool("diagnostics")).structured_content
                            await asyncio.sleep(2)
                            idle = (await client.call_tool("diagnostics")).structured_content
                            print("\nMCP resource sample: " + json.dumps({"baseline_mb": baseline["working_set_mb"], "after_5000_trade_report_mb": after["working_set_mb"], "report_seconds": round(report_seconds, 3), "cpu_seconds_over_2s_idle": round(idle["cpu_seconds"] - after["cpu_seconds"], 3)}))
                            exported = await client.call_tool("export_records", {"entity": "accounts"})
                            return exported.structured_content
                    exported = asyncio.run(run())
                    async def legacy():
                        async with Client(url + "/mcp", mode="legacy") as client:
                            result = await client.call_tool("list_records", {"entity": "accounts"})
                            self.assertEqual(result.structured_content["total"], 1)
                    asyncio.run(legacy())
                    with urlopen(url + exported["download_path"], timeout=2) as response:
                        self.assertIn(b"HTTP account", response.read())
                    for headers, code in (({"Host": "untrusted.example"}, 421), ({"Origin": "https://untrusted.example"}, 403)):
                        with self.assertRaises(HTTPError) as error:
                            urlopen(Request(url + "/health", headers=headers), timeout=2)
                        self.assertEqual(error.exception.code, code)
                        error.exception.close()
                finally:
                    process.terminate()
                    process.wait(timeout=10)
            async def stdio():
                params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py"), "--transport", "stdio", "--database", database], cwd=str(ROOT))
                async with Client(params) as client:
                    result = await client.call_tool("list_records", {"entity": "accounts"})
                    self.assertEqual(result.structured_content["records"][0]["name"], "HTTP account")
            asyncio.run(stdio())
            # Exercise the actual Windows background launcher and identity-checked
            # stop script, including a path with spaces and a separate test DB.
            if os.name == "nt":
                launch = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "launch_mcp.ps1"),
                          "-Background", "-Port", str(port), "-Database", database]
                launched = subprocess.run(launch, capture_output=True, text=True, cwd=ROOT, timeout=30)
                try:
                    self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
                    with urlopen(url + "/health", timeout=2) as response:
                        self.assertEqual(json.load(response)["service"], "trading-journal-mcp")
                    duplicate = subprocess.run(launch, capture_output=True, text=True, cwd=ROOT, timeout=15)
                    self.assertIn("already running", duplicate.stdout)
                finally:
                    stopped = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "stop_mcp.ps1"),
                                              "-Database", database], capture_output=True, text=True, cwd=ROOT, timeout=15)
                    self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                    time.sleep(0.5)


if __name__ == "__main__":
    unittest.main()
