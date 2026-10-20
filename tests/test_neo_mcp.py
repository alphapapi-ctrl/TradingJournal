"""Neo workflow integration checks use temporary journals and synthetic economics."""
import asyncio
import base64
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image
from mcp import Client
import database as db
from journal_mcp import neo, operations, records as r
from journal_mcp.server import create_server, http_app

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-18T10:00:00+10:00"


class NeoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="neo-test-", dir=ROOT / "data")
        self.original_db = db.DB_PATH
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(setattr, db, "DB_PATH", self.original_db)
        db.DB_PATH = Path(self.temp.name) / "journal.db"
        db.init_db()
        self.account = r.save_record("accounts", {"name": "Synthetic Demo", "account_type": "Demo", "currency": "USD", "initial_balance": 999999})["id"]

    def session(self, **changes):
        return neo.save_session({"session_key": "session-one", "account_id": self.account, "exchange_date": "2026-10-17", "local_date": "2026-10-18", "timezone": "Australia/Brisbane", "label": "NY overnight", "baseline_version": "test-1", **changes})

    def candidate(self, session=None, plan=None):
        session = session or self.session()
        return neo.save_candidate({"candidate_key": "idea-one", "session_id": session["id"], "symbol": "SYNTH", "direction": "LONG", "plan": plan or {"entry_price": 100, "stop_price": 98, "target_price": 103, "baseline_version": "test-1"}, "reason": "Initial observed plan"})

    def spec(self, **changes):
        return neo.save_instrument_spec({"symbol": "SYNTH", "version": "test-1", "quote_currency": "USD", "contract_multiplier": 2, "point_value": 2, "tick_size": 0.25, "tick_value": 0.5, "quantity_step": 1, "min_quantity": 1, "source": "Synthetic test data, not MNQ specifications", "verified_at": NOW, **changes})

    def snapshot(self, **changes):
        return neo.save_account_snapshot({"account_id": self.account, "balance": 10000, "equity": 9000, "currency": "USD", "source": "Synthetic balance", "observed_at": NOW, "trading_day_timezone": "America/New_York", "trading_day_boundary": "17:00", **changes})

    def regime(self, **changes):
        return neo.save_risk_regime({"account_id": self.account, "mode": "demo", "risk_pct": 1, "source": "Explicit demo test configuration", "effective_at": NOW, **changes})

    def trade(self, **changes):
        return r.save_record("trades", {"account_id": self.account, "symbol": "SYNTH", "direction": "LONG", "quantity": 10, "entry_price": 100, "entry_time": NOW, **changes})

    def image(self):
        buffer = io.BytesIO()
        Image.new("RGB", (10, 10), "blue").save(buffer, format="PNG")
        return buffer.getvalue()

    def upload(self, candidate):
        return neo.upload_evidence(base64.b64encode(self.image()).decode(), {"journal_entry_id": candidate["journal_entry_id"], "filename": "capture.png", "mime_type": "image/png", "stage": "pre", "captured_at": NOW, "symbol": "SYNTH", "timeframe": "1m", "source": "Synthetic chart"})["evidence"]

    def playbook(self):
        pb = r.save_record("playbooks", {"name": "Test reversal"})
        required = r.save_record("playbook_rules", {"playbook_id": pb["id"], "name": "Required", "rule_type": "required"})
        optional = r.save_record("playbook_rules", {"playbook_id": pb["id"], "name": "Preferred divergence", "rule_type": "optional"})
        r.save_record("playbook_risk_rules", {"playbook_id": pb["id"], "min_required_pct": 100, "min_optional_pct": 0, "risk_level": "no_trade", "risk_multiplier": 0})
        return pb["id"], str(required["id"]), str(optional["id"])

    def test_migration_repeat_and_revision_detects_ui_write(self):
        db.init_db()
        db.init_db()
        account = r.get_record("accounts", self.account)
        self.assertEqual(account["initial_balance"], 999999)
        self.assertIn("management", r.schema("evidence")["entities"]["evidence"]["fields"]["stage"]["enum"])
        db.execute("UPDATE accounts SET notes='UI edit' WHERE id=?", (self.account,))
        with self.assertRaisesRegex(ValueError, "Revision conflict"):
            r.save_record("accounts", {"name": "Stale"}, self.account, expected_revision=account["revision"])
        self.assertEqual(r.get_record("accounts", self.account)["notes"], "UI edit")

    def test_create_retry_conflict_and_failed_writes_do_not_consume_key(self):
        data = {"name": "Retry", "currency": "USD"}
        first = r.save_record("accounts", data, idempotency_key="account-key")
        self.assertEqual(first, r.save_record("accounts", data, idempotency_key="account-key"))
        with self.assertRaisesRegex(ValueError, "different input"):
            r.save_record("accounts", {"name": "Different"}, idempotency_key="account-key")
        with self.assertRaises(ValueError):
            r.save_record("trades", {"symbol": "Missing"}, idempotency_key="failed")
        valid = self.trade()
        result = r.save_record("trades", {"raw_data": "Now valid"}, valid["id"], idempotency_key="failed")
        self.assertEqual(result["raw_data"], "Now valid")

    def test_plan_versions_preserve_original_and_provenance(self):
        pb, _, _ = self.playbook()
        template = r.save_record("journal_templates", {"name": "Neo template", "template_type": "trade", "version": "2"})
        original = {"entry_price": 100, "stop_price": 98, "baseline_version": "test-1", "playbook_id": pb, "template_id": template["id"]}
        candidate = self.candidate(plan=original)
        entry = r.get_record("journal_entries", candidate["journal_entry_id"])
        self.assertEqual(entry["template_version"], "2@1")
        revised = neo.save_candidate({"plan": {**original, "stop_price": 97}, "reason": "New structure"}, candidate["id"], candidate["revision"], "revision-key")
        self.assertEqual(len(revised["plans"]), 2)
        self.assertEqual(json.loads(revised["plans"][0]["plan"]), original)
        self.assertEqual(r.get_record("journal_entries", entry["id"])["pre_plan"], entry["pre_plan"])
        self.assertEqual(revised, neo.save_candidate({"plan": {**original, "stop_price": 97}, "reason": "New structure"}, candidate["id"], candidate["revision"], "revision-key"))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "Append-only"):
            with db.transaction():
                db.execute("UPDATE candidate_plans SET reason='erase'")
        with self.assertRaises(ValueError):
            neo.save_candidate({"status": "open"}, candidate["id"], revised["revision"])

    def test_review_block_survives_midnight_and_requires_restart(self):
        first = self.session(review_block=1, review_reason="Rule break")
        second = self.session(session_key="next-day", exchange_date="2026-10-18", local_date="2026-10-19")
        self.assertEqual(second["review_block"], 1)
        with self.assertRaises(ValueError):
            neo.save_session({"review_block": 0}, second["id"], second["revision"])
        cleared = neo.save_session({"review_block": 0, "restart_decision": "Reviewed and resumed", "handover": {"next_action": "Await location"}}, second["id"], second["revision"])
        self.assertEqual(cleared["review_block"], 0)
        self.assertEqual(json.loads(cleared["handover"])["next_action"], "Await location")
        self.assertEqual(first["local_date"], "2026-10-18")

    def test_four_state_readiness_and_guard_semantics(self):
        pb, req, opt = self.playbook()
        def evaluate(a, b):
            return r.evaluate_playbook(pb, evidence={req: {"status": a, "source": "chart", "observed_at": NOW}, opt: {"status": b}})
        passed = evaluate("pass", "fail")
        self.assertEqual((passed["readiness"], passed["risk_score"], passed["risk_level"], passed["risk_multiplier"]), ("ready", 70, "normal", 1))
        self.assertEqual(evaluate("pass", "pass")["risk_score"], 100)
        self.assertEqual(evaluate("fail", "pass")["risk_level"], "no_trade")
        for state in ("unknown", "caution"):
            result = evaluate(state, "pass")
            self.assertEqual(result["readiness"], "incomplete")
            self.assertIsNone(result["risk_score"])
            self.assertIsNone(result["risk_multiplier"])
        optional_missing = evaluate("pass", "unknown")
        self.assertEqual(optional_missing["readiness"], "ready")
        self.assertIsNone(optional_missing["risk_score"])
        self.assertEqual(optional_missing["risk_level"], "normal")

    def test_sizing_uses_balance_costs_limits_and_rounds_down(self):
        candidate = self.candidate()
        snapshot, regime, spec = self.snapshot(), self.regime(max_combined_risk=70), self.spec()
        result = neo.size_candidate(candidate["id"], snapshot["id"], regime["id"], spec["id"], candidate["revision"], NOW, costs_per_contract=1, existing_open_risk=20)
        self.assertEqual(result["sizing"]["risk_budget"], 100)
        self.assertEqual(result["sizing"]["quantity"], 10)
        self.assertEqual(result["sizing"]["actual_risk"], 50)
        self.assertEqual(len(result["candidate"]["plans"]), 2)
        latest = result["candidate"]
        override = neo.size_candidate(latest["id"], snapshot["id"], regime["id"], spec["id"], latest["revision"], NOW, risk_pct_override=0.001, override_reason="This idea only", existing_open_risk=0)
        self.assertEqual(override["sizing"]["quantity"], 0)
        self.assertTrue(override["sizing"]["below_minimum"])
        self.assertEqual(r.get_record("risk_regimes", regime["id"])["risk_pct"], 1)

    def test_fx_and_account_mode_must_be_explicit(self):
        candidate = self.candidate()
        snapshot, regime, spec = self.snapshot(), self.regime(), self.spec(quote_currency="EUR")
        with self.assertRaises(ValueError):
            neo.size_candidate(candidate["id"], snapshot["id"], regime["id"], spec["id"], candidate["revision"], NOW)
        result = neo.size_candidate(candidate["id"], snapshot["id"], regime["id"], spec["id"], candidate["revision"], NOW, fx_rate=1.5, fx_source="Synthetic", fx_observed_at=NOW)
        self.assertEqual(result["sizing"]["account_point_value"], 3)
        self.assertEqual(result["sizing"]["quantity"], 16)
        with self.assertRaises(ValueError):
            self.regime(mode="live")
        with self.assertRaises(ValueError):
            self.snapshot(currency="EUR")

    def test_contract_partial_close_preserves_economics_and_safe_retry(self):
        spec = self.spec(quote_currency="EUR")
        trade = self.trade(instrument_spec_id=spec["id"], sizing_assumptions={"fx_rate": 1.5, "fx_source": "Synthetic", "fx_observed_at": NOW}, commission=10, planned_risk=60)
        first = r.trade_action("partial_close", trade["id"], 110, "2026-10-18T11:00:00+10:00", 4, extra_commission=1, expected_revision=trade["revision"], idempotency_key="partial")
        retry = r.trade_action("partial_close", trade["id"], 110, "2026-10-18T11:00:00+10:00", 4, extra_commission=1, expected_revision=trade["revision"], idempotency_key="partial")
        self.assertEqual(first, retry)
        self.assertEqual(first["closed_trade"]["pnl"], 120)
        self.assertEqual(first["closed_trade"]["instrument_spec_id"], spec["id"])
        self.assertEqual(first["closed_trade"]["commission"], 5)
        closed = r.trade_action("close", trade["id"], 110, "2026-10-18T11:01:00+10:00")
        self.assertEqual(closed["closed_trade"]["pnl"], 180)
        self.assertEqual(r.list_records("trades")["total"], 2)

    def test_evidence_upload_bytes_dedup_chunk_and_private_download(self):
        candidate = self.candidate()
        evidence = self.upload(candidate)
        self.assertEqual(evidence, self.upload(candidate))
        self.assertEqual(r.list_records("evidence")["total"], 1)
        chunk, payload = neo.read_evidence(evidence["id"], max_bytes=10), b""
        offset = 0
        while True:
            chunk = neo.read_evidence(evidence["id"], offset, 10)
            payload += base64.b64decode(chunk["content"])
            if chunk["next_offset"] is None:
                break
            offset = chunk["next_offset"]
        self.assertEqual(payload, self.image())
        token = parse_qs(urlparse(chunk["download_path"]).query)["token"][0]
        self.assertEqual(neo.authorized_evidence(evidence["id"], token)[1], self.image())
        for token_value in (None, "wrong"):
            with self.assertRaises(ValueError):
                neo.authorized_evidence(evidence["id"], token_value)
        with patch.object(neo.time, "time", return_value=chunk["expires_at"] + 1), self.assertRaises(ValueError):
            neo.authorized_evidence(evidence["id"], token)
        backup = operations.backup_database()
        with closing(sqlite3.connect(backup["path"])) as conn:
            self.assertEqual(conn.execute("SELECT content FROM evidence_blobs").fetchone()[0], self.image())

    def test_invalid_evidence_and_cross_candidate_links_rejected(self):
        candidate = self.candidate()
        data = {"journal_entry_id": candidate["journal_entry_id"], "filename": "x.png", "mime_type": "image/png", "stage": "pre", "captured_at": NOW, "symbol": "SYNTH", "timeframe": "1m", "source": "Test"}
        for content in ("C:\\capture.png", base64.b64encode(b"not an image").decode()):
            with self.assertRaises(ValueError):
                neo.upload_evidence(content, data)
        for changes in ({"filename": "../x.png"}, {"mime_type": "image/jpeg"}, {"captured_at": "2026-10-18T10:00:00"}, {"symbol": "Other"}):
            with self.assertRaises(ValueError):
                neo.upload_evidence(base64.b64encode(self.image()).decode(), {**data, **changes})
        self.assertEqual(r.list_records("evidence")["total"], 0)

    def test_management_event_is_append_only_and_preserves_preplan(self):
        candidate = self.candidate()
        evidence = self.upload(candidate)
        data = {"event_key": "decision-one", "journal_entry_id": candidate["journal_entry_id"], "observed_at": NOW, "decision": "Hold original plan", "reason": "Observed structure", "action_status": "proposed", "before_stop": 98, "after_stop": 99, "evidence_ids": [evidence["id"]]}
        first = neo.append_management_event(data)
        self.assertEqual(first, neo.append_management_event(data))
        with self.assertRaises(ValueError):
            neo.append_management_event({**data, "decision": "Changed history"})
        with self.assertRaises(sqlite3.IntegrityError):
            with db.transaction():
                db.execute("DELETE FROM management_events WHERE id=?", (first["id"],))
        self.assertEqual(json.loads(r.get_record("journal_entries", candidate["journal_entry_id"])["pre_plan"])["stop_price"], 98)

    def test_fill_reconciliation_costs_corrections_and_lifecycle(self):
        candidate = self.candidate(plan={"entry_price": 100, "stop_price": 98, "planned_cash_risk": 40, "baseline_version": "test-1"})
        spec = self.spec()
        trade = self.trade(instrument_spec_id=spec["id"], commission=1)
        data = {"candidate_id": candidate["id"], "trade_id": trade["id"], "source": "Test broker", "source_fill_id": "entry-1", "filled_at": NOW, "kind": "entry", "price": 100, "quantity": 10, "commission": 1, "currency": "USD"}
        entry_fill = neo.record_fill(data)
        self.assertEqual(entry_fill, neo.record_fill(data))
        self.assertEqual(r.get_record("candidates", candidate["id"])["status"], "open")
        exit_fill = neo.record_fill({**data, "source_fill_id": "exit-1", "filled_at": "2026-10-18T11:00:00+10:00", "kind": "exit", "price": 110, "commission": 2})
        self.assertFalse(neo.reconcile_candidate(candidate["id"])["complete"])
        correction = {**data, "source_fill_id": "exit-correction", "filled_at": "2026-10-18T01:00:00+00:00", "kind": "exit", "price": 110, "commission": 2, "pnl": 200, "corrects_fill_id": exit_fill["id"]}
        corrected = neo.record_fill(correction)
        self.assertEqual(corrected, neo.record_fill(correction))
        self.assertFalse(neo.reconcile_candidate(candidate["id"])["complete"])
        r.trade_action("close", trade["id"], 110, "2026-10-18T11:00:00+10:00", extra_commission=2)
        report = neo.reconcile_candidate(candidate["id"])
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["net_realised_pnl"], 197)
        self.assertEqual(report["realised_net_r"], 4.925)
        current = r.get_record("candidates", candidate["id"])
        closed = neo.save_candidate({"status": "closed"}, candidate["id"], current["revision"])
        self.assertEqual(closed["status"], "closed")

    def test_official_client_exposes_and_calls_new_workflow(self):
        async def run():
            async with Client(create_server()) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertIn("expected_revision", tools["save_session"].input_schema["properties"])
                result = await client.call_tool("save_session", {"data": {"session_key": "mcp", "account_id": self.account, "exchange_date": "2026-10-17", "local_date": "2026-10-18", "timezone": "Australia/Brisbane", "label": "MCP", "baseline_version": "test-1"}, "idempotency_key": "mcp-session"})
                self.assertFalse(result.is_error, result)
                again = await client.call_tool("save_session", {"data": {"session_key": "mcp", "account_id": self.account, "exchange_date": "2026-10-17", "local_date": "2026-10-18", "timezone": "Australia/Brisbane", "label": "MCP", "baseline_version": "test-1"}, "idempotency_key": "mcp-session"})
                self.assertEqual(result.structured_content, again.structured_content)
        asyncio.run(run())

    def test_journal_ui_displays_real_stored_image_and_timeline(self):
        candidate = self.candidate()
        self.upload(candidate)
        neo.append_management_event({"event_key": "ui-event", "journal_entry_id": candidate["journal_entry_id"], "observed_at": NOW, "decision": "Keep plan", "reason": "No change", "action_status": "observed"})
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("from utils.neo_ui import show\nshow()").run(timeout=15)
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any("Observed" in item.value for item in app.caption))
        self.assertFalse(app.get("json"))
        self.assertTrue(app.get("imgs"))

    def test_two_writers_cannot_overwrite_same_revision(self):
        account = r.get_record("accounts", self.account)
        def update(note):
            try:
                return r.save_record("accounts", {"notes": note}, self.account, account["revision"])
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as writers:
            results = list(writers.map(update, ["First writer", "Second writer"]))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertTrue(any("Revision conflict" in result for result in results if isinstance(result, str)))

    def test_eligible_order_intent_and_cancel_do_not_create_trade(self):
        pb, req, opt = self.playbook()
        candidate = self.candidate(plan={"entry_price": 100, "stop_price": 98, "baseline_version": "test-1", "playbook_id": pb, "checklist": {req: {"status": "pass"}, opt: {"status": "fail"}}})
        snapshot, regime, spec = self.snapshot(), self.regime(), self.spec()
        sized = neo.size_candidate(candidate["id"], snapshot["id"], regime["id"], spec["id"], candidate["revision"], NOW)["candidate"]
        assessed = neo.save_candidate({"status": "assessed"}, candidate["id"], sized["revision"])
        pending = neo.save_candidate({"status": "pending"}, candidate["id"], assessed["revision"])
        cancelled = neo.save_candidate({"status": "cancelled"}, candidate["id"], pending["revision"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(r.list_records("trades")["total"], 0)

    def test_partial_fill_reconciliation_allocates_entry_fees_once(self):
        candidate = self.candidate(plan={"entry_price": 100, "stop_price": 98, "planned_cash_risk": 40, "baseline_version": "test-1"})
        spec = self.spec()
        trade = self.trade(instrument_spec_id=spec["id"], commission=10, swap=-2)
        data = {"candidate_id": candidate["id"], "trade_id": trade["id"], "source": "Test broker", "source_fill_id": "entry", "filled_at": NOW, "kind": "entry", "price": 100, "quantity": 10, "commission": 10, "swap": -2, "currency": "USD"}
        neo.record_fill(data)
        closed = r.trade_action("partial_close", trade["id"], 110, "2026-10-18T11:00:00+10:00", 4, extra_commission=1)
        neo.record_fill({**data, "trade_id": closed["closed_trade"]["id"], "source_fill_id": "exit-part", "filled_at": "2026-10-18T11:00:00+10:00", "kind": "exit", "quantity": 4, "price": 110, "commission": 1, "swap": 0, "pnl": 80})
        report = neo.reconcile_candidate(candidate["id"])
        self.assertTrue(report["complete"], report)
        self.assertEqual(report["open_quantity"], 6)
        self.assertEqual(report["commissions"], 5)
        self.assertEqual(report["swap"], -0.8)
        self.assertEqual(report["net_realised_pnl"], 74.2)
        with self.assertRaises(ValueError):
            neo.save_candidate({"status": "closed"}, candidate["id"], r.get_record("candidates", candidate["id"])["revision"])

    def test_http_image_route_requires_capability_and_host_checks(self):
        candidate = self.candidate()
        evidence = self.upload(candidate)
        access = neo.read_evidence(evidence["id"])
        from starlette.testclient import TestClient
        with TestClient(http_app(create_server(), "127.0.0.1"), base_url="http://127.0.0.1", client=("127.0.0.1", 4242)) as client:
            denied = client.get(f"/evidence/{evidence['id']}")
            self.assertEqual(denied.status_code, 404)
            allowed = client.get(access["download_path"])
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.content, self.image())
            self.assertEqual(allowed.headers["content-type"], "image/png")
            self.assertEqual(allowed.headers["cache-control"], "no-store")
            self.assertNotEqual(client.get(access["download_path"], headers={"Host": "attacker.example"}).status_code, 200)

    def test_ui_review_preserves_namespaced_metadata(self):
        from utils.trade_ops import save_journal_entry
        candidate = self.candidate()
        entry_id = candidate["journal_entry_id"]
        r.save_record("journal_entries", {"custom_fields": {"neo": {"evidence": ["legacy reference"]}, "other": 1}}, entry_id)
        save_journal_entry(entry_id, "trade", "2026-10-18", "Reviewed", "Executed", "Calm", "Lesson", "B", 7)
        self.assertEqual(json.loads(r.get_record("journal_entries", entry_id)["custom_fields"])["other"], 1)


if __name__ == "__main__":
    unittest.main()
