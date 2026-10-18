"""Pattern acceptance checks run entirely in disposable SQLite journals."""
import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from mcp import Client
import database as db
from journal_mcp import neo, patterns as p, records as r, reporting
from journal_mcp.server import create_server

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-18T10:00:00+10:00"


class PatternCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pattern-test-", dir=ROOT / "data")
        self.original_db = db.DB_PATH
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(setattr, db, "DB_PATH", self.original_db)
        db.DB_PATH = Path(self.temp.name) / "journal.db"
        db.init_db()
        self.account = r.save_record("accounts", {"name": "Disposable demo", "account_type": "Demo", "currency": "USD"})["id"]
        self.counter = 0

    def key(self):
        self.counter += 1
        return f"test-{self.counter}"

    def entry(self, **changes):
        key = self.key()
        return p.save_pattern_entry({"entry_key": key, "account_id": self.account, "event_date": "2026-10-18", "timezone": "Australia/Brisbane", "situation": "Synthetic acceptance-test reflection", **changes}, key)

    def definition(self, **changes):
        key = self.key()
        return p.save_psychology_pattern({"pattern_key": key, "label": "Missed move", "practice_response": "Wait for the next planned setup", **changes}, key)

    def trade(self, **changes):
        return r.save_record("trades", {"account_id": self.account, "symbol": "SYNTH", "direction": "LONG", "entry_price": 100, "quantity": 1, "entry_time": NOW, **changes})

    def session(self, **changes):
        return neo.save_session({"session_key": self.key(), "account_id": self.account, "exchange_date": "2026-10-17", "local_date": "2026-10-18", "timezone": "Australia/Brisbane", "label": "Test overnight", "baseline_version": "test", **changes})

    def report(self, **changes):
        return p.generate_pattern_report([self.account], "2026-10-18", "2026-10-18", **changes)

    def image(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    def upload(self, **owner):
        return neo.upload_evidence(base64.b64encode(self.image()).decode(), {"filename": "test.png", "mime_type": "image/png", "stage": "management", "captured_at": NOW, "symbol": "SYNTH", "timeframe": "1m", "source": "Synthetic test", **owner})["evidence"]

    def test_partial_no_trade_observation_and_no_invented_time(self):
        entry = self.entry()
        self.assertIsNone(entry["occurred_at"])
        self.assertIsNone(entry["pattern_id"])
        self.assertEqual((entry["urge_response"], entry["rule_breach"], entry["plan_adherence"]), ("unknown", "unknown", "unknown"))
        self.assertEqual(r.list_records("trades")["total"], 0)
        self.assertEqual(r.list_records("journal_entries")["total"], 0)
        self.assertEqual(r.details("pattern_entries", entry["id"])["situation"], entry["situation"])
        with self.assertRaises(p.PatternError):
            self.entry(situation=None, title="A title is not an observation")

    def test_urge_action_breach_and_adherence_are_distinct(self):
        resisted = self.entry(urge_response="resisted", action_taken="Waited")
        acted = self.entry(urge_response="acted_on", rule_breach="unknown", plan_adherence="followed")
        self.assertNotEqual(resisted["id"], acted["id"])
        self.assertEqual(acted["rule_breach"], "unknown")
        self.assertEqual(self.report()["totals"]["occurrences"], 2)

    def test_many_trades_tags_and_followups_count_as_one(self):
        trade_a, trade_b = self.trade(), self.trade()
        session = self.session()
        evidence = self.upload(journal_entry_id=session["journal_entry_id"])
        event = neo.append_management_event({"event_key": self.key(), "journal_entry_id": session["journal_entry_id"], "decision": "Wait", "reason": "Test", "action_status": "observed", "observed_at": NOW})
        entry = self.entry(session_id=session["id"], trade_ids=[trade_a["id"], trade_b["id"]], evidence_ids=[evidence["id"]], management_event_ids=[event["id"]], trigger_tags=["missed move", "fast market"])
        for note in ("Practised waiting", "Reviewed later"):
            p.append_pattern_followup({"pattern_entry_id": entry["id"], "followup_key": self.key(), "note": note, "response_practised": True}, self.key())
        report = self.report(group_by="trigger_tags")
        self.assertEqual(report["totals"]["occurrences"], 1)
        self.assertEqual(report["totals"]["distinct_trades"], 2)
        self.assertEqual(report["totals"]["distinct_sessions"], 1)
        self.assertEqual(sum(group["occurrences"] for group in report["groups"]), 2)
        self.assertTrue(report["groups_overlap"])
        self.assertEqual(len(report["useful_responses"]), 2)
        self.assertNotIn("note", report["useful_responses"][0])

    def test_conflicting_links_fail_without_partial_records(self):
        other = r.save_record("accounts", {"name": "Another test account"})
        trade = self.trade(account_id=other["id"])
        for changes, code in (({"trade_ids": [trade["id"]]}, "account_mismatch"), ({"session_id": 99999}, "missing_link")):
            with self.assertRaises(p.PatternError) as raised:
                self.entry(**changes)
            self.assertEqual(raised.exception.payload["code"], code)
        self.assertEqual(r.list_records("pattern_entries")["total"], 0)
        self.assertEqual(r.list_records("pattern_revisions")["total"], 0)

    def test_idempotency_revision_conflicts_and_array_replacement(self):
        data = {"entry_key": "stable", "account_id": self.account, "event_date": "2026-10-18", "timezone": "Australia/Brisbane", "trigger": "Observed trigger", "trigger_tags": ["a", "b"]}
        first = p.save_pattern_entry(data, "create")
        self.assertEqual(first, p.save_pattern_entry(data, "create"))
        with self.assertRaises(p.PatternError) as changed:
            p.save_pattern_entry({**data, "trigger": "Changed"}, "create")
        self.assertEqual(changed.exception.payload["code"], "idempotency_conflict")
        second = p.save_pattern_entry({"trigger_tags": ["c"]}, "edit", first["id"], first["revision"])
        self.assertEqual(second["trigger_tags"], ["c"])
        with self.assertRaises(p.PatternError) as stale:
            p.save_pattern_entry({"trigger": "Overwrite"}, "stale", first["id"], first["revision"])
        self.assertEqual(stale.exception.payload["current_revision"], second["revision"])
        self.assertEqual(p.get("pattern_entries", first["id"])["trigger"], "Observed trigger")

    def test_auditable_edits_archive_and_followup_corrections(self):
        definition = self.definition()
        entry = self.entry(pattern_id=definition["id"], trigger="Original words")
        updated = p.save_pattern_entry({"trigger": "Clarified words"}, self.key(), entry["id"], entry["revision"])
        self.assertEqual(updated["history"][0]["snapshot"]["trigger"], "Original words")
        renamed = p.save_psychology_pattern({"label": "Renamed pattern", "status": "archived"}, self.key(), definition["id"], definition["revision"])
        self.assertEqual(self.report()["recurrence"][0]["label"], "Renamed pattern")
        self.assertEqual(renamed["history"][0]["snapshot"]["label"], "Missed move")
        note = {"pattern_entry_id": entry["id"], "followup_key": "f1", "note": "First account"}
        first = p.append_pattern_followup(note, "followup-1")
        self.assertEqual(first, p.append_pattern_followup(note, "followup-1"))
        correction = p.append_pattern_followup({"pattern_entry_id": entry["id"], "followup_key": "f2", "note": "Correction", "corrects_followup_id": first["id"]}, "followup-2")
        self.assertEqual(len(correction["parent"]["followups"]), 2)
        self.assertEqual(self.report()["totals"]["occurrences"], 1)
        with self.assertRaises(Exception):
            r.save_record("pattern_entries", {"trigger": "Bypass"}, entry["id"])
        with self.assertRaises(Exception):
            r.delete_record("pattern_followups", first["id"])

    def test_resistance_rate_denominators_and_zero_eligible(self):
        for response in ("resisted", "resisted", "acted_on", "no_urge", "unknown", "unknown"):
            self.entry(urge_response=response)
        rate = self.report()["totals"]["urge_response"]
        self.assertEqual(rate["denominator"], 3)
        self.assertEqual(rate["rate"], 2 / 3)
        self.assertEqual(rate["counts"]["unknown"], 2)
        zero = self.report(filters={"urge_response": "unknown"})["totals"]["urge_response"]
        self.assertIsNone(zero["rate"])
        self.assertEqual(zero["denominator"], 0)

    def test_date_basis_overnight_pagination_and_missing_context(self):
        session = self.session()
        self.entry(session_id=session["id"], occurred_at="2026-10-18T00:00:00+00:00")
        no_session = self.entry()
        local = self.report(limit=1)
        self.assertEqual(local["total"], 2)
        self.assertIsNotNone(local["next_offset"])
        exchange = p.generate_pattern_report([self.account], "2026-10-17", "2026-10-17", date_basis="session_exchange_date")
        self.assertEqual(exchange["total"], 1)
        self.assertEqual(exchange["data_quality"]["excluded_missing_date_context_ids"], [no_session["id"]])
        with self.assertRaises(p.PatternError):
            self.entry(occurred_at="2026-10-17T00:00:00+00:00")

    def test_followup_parent_update_atomic_and_pending_without_handover(self):
        session = self.session(review_block=1, review_reason="Existing review block", handover={"unrelated": "keep"})
        entry = self.entry(session_id=session["id"], next_session_intention="Wait for valid location")
        self.assertEqual(r.list_records("pattern_entries", {"account_id": self.account, "follow_up_status": "pending"})["total"], 1)
        payload = {"pattern_entry_id": entry["id"], "followup_key": "atomic", "note": "Reviewed", "parent_update": {"status": "reviewed", "follow_up_status": "reviewed", "next_session_intention": "Practise waiting"}}
        with self.assertRaises(p.PatternError):
            p.append_pattern_followup(payload, "bad-revision", expected_revision=9999)
        self.assertEqual(r.list_records("pattern_followups")["total"], 0)
        result = p.append_pattern_followup(payload, "good-revision", expected_revision=entry["revision"])
        self.assertGreater(result["parent"]["revision"], entry["revision"])
        self.assertEqual(result["parent"]["next_session_intention"], "Practise waiting")
        unchanged = r.get_record("trading_sessions", session["id"])
        self.assertEqual(unchanged["review_block"], 1)
        self.assertEqual(json.loads(unchanged["handover"]), {"unrelated": "keep"})

    def test_pattern_owned_screenshot_without_dummy_entry(self):
        entry = self.entry(symbol="SYNTH")
        evidence = self.upload(pattern_entry_id=entry["id"])
        self.assertIsNone(evidence["journal_entry_id"])
        self.assertEqual(evidence["pattern_entry_id"], entry["id"])
        self.assertEqual(neo.evidence_bytes(evidence["id"])[1], self.image())
        parent = p.details("pattern_entries", entry["id"])
        self.assertIn(evidence["id"], parent["evidence_ids"])
        self.assertGreater(parent["revision"], entry["revision"])
        self.assertEqual(r.list_records("journal_entries")["total"], 0)
        db.init_db()
        self.assertEqual(neo.evidence_bytes(evidence["id"])[1], self.image())

    def test_provenance_states_uncalibrated_notes_and_rule_adoption(self):
        entry = self.entry(emotion_body_state="Tense", field_provenance={"emotion_body_state": {"kind": "user_report"}}, field_states={"root_belief_or_fear": "not_applicable", "automatic_thought": "unknown"}, uncalibrated_score_note="Before: 3/5, no agreed scale")
        self.assertEqual(entry["field_states"]["automatic_thought"], "unknown")
        with self.assertRaises(p.PatternError):
            p.save_pattern_entry({"emotion_body_state": "An inferred emotion", "field_provenance": {"emotion_body_state": {"kind": "assistant_interpretation"}}}, self.key(), entry["id"], entry["revision"])
        with self.assertRaises(p.PatternError):
            self.entry(before_rating=3)
        with self.assertRaises(p.PatternError):
            self.entry(proposed_rule="New rule", proposed_rule_status="adopted")
        adopted = self.entry(proposed_rule="Write a review", proposed_rule_status="adopted", adoption_decision="Explicit test decision", adoption_reference="Test user instruction", adopted_at=NOW)
        self.assertEqual(adopted["proposed_rule_status"], "adopted")
        self.assertEqual(r.list_records("risk_regimes")["total"], 0)

    def test_explicit_account_scope_and_archiving(self):
        entry = self.entry()
        with self.assertRaises(p.PatternError):
            p.generate_pattern_report([], "2026-10-18", "2026-10-18")
        p.save_pattern_entry({"status": "archived"}, self.key(), entry["id"], entry["revision"])
        self.assertEqual(self.report()["total"], 0)
        self.assertEqual(self.report(include_archived=True)["archived"]["occurrences"], 1)

    def legacy(self, **changes):
        return r.save_record("journal_entries", {"entry_type": "daily", "entry_date": "2026-10-18", "account_id": self.account,
                                                "psychology": "Unstructured original reflection: missed the move. Before score 3/5.",
                                                "custom_fields": {"other": {"keep": 1}, "neo_pattern": {"pattern_key": self.key(), "follow_up_status": "pending"}}, **changes})

    def test_migration_dry_run_repeat_source_conflict_and_dedup(self):
        source = self.legacy()
        selection = {"source_ids": [source["id"]], "timezone": "Australia/Brisbane"}
        preview = p.migrate_pattern_entries(selection)
        self.assertEqual(preview["counts"], {"proposed": 1})
        self.assertEqual(r.get_record("journal_entries", source["id"]), source)
        self.assertEqual(r.list_records("pattern_entries")["total"], 0)
        committed = p.migrate_pattern_entries(selection, False, "migration")
        self.assertEqual(committed, p.migrate_pattern_entries(selection, False, "migration"))
        repeated = p.migrate_pattern_entries(selection, False, "migration-again")
        self.assertEqual(repeated["counts"], {"already_migrated": 1})
        entry = p.details("pattern_entries", committed["mappings"][0]["pattern_entry_id"])
        self.assertIn("Unstructured original reflection", entry["legacy_reflection_text"])
        self.assertEqual(entry["migration_review_required"], 1)
        self.assertEqual(self.report()["total"], 1)
        self.assertEqual(r.list_records("trades")["total"], 0)
        updated_source = r.get_record("journal_entries", source["id"])
        self.assertEqual(json.loads(updated_source["custom_fields"])["other"], {"keep": 1})
        from utils.trade_ops import get_journal_entries
        self.assertEqual(get_journal_entries("daily"), [])
        r.save_record("journal_entries", {"lessons": "Later source edit"}, source["id"])
        changed = p.migrate_pattern_entries(selection, False, "migration-reconcile")
        self.assertEqual(changed["counts"], {"reconciliation_required": 1})
        self.assertEqual(p.get("pattern_entries", entry["id"])["revision"], entry["revision"])

    def test_migration_explicit_template_no_fixed_id_and_heading_mapping(self):
        template = r.save_record("journal_templates", {"name": "Disposable pattern template", "template_type": "daily"})
        source = self.legacy(template_id=template["id"], custom_fields={"other": 5}, psychology="## Situation\nMissed a move\n## Trigger\nPrice accelerated\n## Better response\nWait")
        preview = p.migrate_pattern_entries({"template_ids": [template["id"]], "timezone": "Australia/Brisbane"})
        self.assertEqual(preview["items"][0]["mapping"]["trigger"], "Price accelerated")
        ordinary = r.save_record("journal_entries", {"entry_type": "daily", "entry_date": "2026-10-18", "account_id": self.account, "psychology": "Routine readiness"})
        rejected = p.migrate_pattern_entries({"source_ids": [ordinary["id"]], "timezone": "Australia/Brisbane"})
        self.assertEqual(rejected["counts"], {"conflict": 1})

    def test_migration_marker_failure_rolls_back_target_and_history(self):
        source = self.legacy()
        with patch.object(r, "save_record", side_effect=p.PatternError("revision_conflict", "Injected source conflict")):
            report = p.migrate_pattern_entries({"source_ids": [source["id"]], "timezone": "Australia/Brisbane"}, False, "migration-error")
        self.assertEqual(report["counts"], {"conflict": 1})
        self.assertEqual(r.list_records("pattern_entries")["total"], 0)
        self.assertEqual(r.list_records("pattern_revisions")["total"], 0)

    def test_migration_rejects_malformed_legacy_metadata_without_erasing_it(self):
        template = r.save_record("journal_templates", {"name": "Legacy test pattern", "template_type": "daily"})
        source = self.legacy(template_id=template["id"])
        db.execute("UPDATE journal_entries SET custom_fields='[1,2]' WHERE id=?", (source["id"],))
        selection = {"source_ids": [source["id"]], "template_ids": [template["id"]], "timezone": "Australia/Brisbane"}
        for dry_run in (True, False):
            result = p.migrate_pattern_entries(selection, dry_run, "malformed-migration" if not dry_run else None)
            self.assertEqual(result["counts"], {"conflict": 1})
        self.assertEqual(r.get_record("journal_entries", source["id"])["custom_fields"], "[1,2]")
        self.assertEqual(r.list_records("pattern_entries")["total"], 0)

    def test_previous_evidence_schema_upgrades_without_losing_images(self):
        db.DB_PATH = Path(self.temp.name) / "previous.db"
        with patch("journal_mcp.pattern_schema.migrate"):
            db.init_db()
        account = r.save_record("accounts", {"name": "Previous account"})
        journal = r.save_record("journal_entries", {"entry_type": "daily", "entry_date": "2026-10-18", "account_id": account["id"]})
        import hashlib
        image = self.image()
        with db.transaction():
            evidence_id = neo.insert("evidence", {"journal_entry_id": journal["id"], "filename": "old.png", "mime_type": "image/png", "sha256": hashlib.sha256(image).hexdigest(), "byte_size": len(image), "stage": "pre", "captured_at": NOW, "symbol": "SYNTH", "timeframe": "1m", "source": "Old test image"})
            db.execute("INSERT INTO evidence_blobs VALUES (?,?)", (evidence_id, image))
        db.init_db()
        db.init_db()
        self.assertEqual(neo.evidence_bytes(evidence_id)[1], image)
        self.assertIsNone(r.get_record("evidence", evidence_id)["pattern_entry_id"])
        self.assertEqual(r.get_record("accounts", account["id"])["name"], "Previous account")

    def test_failed_upgrade_rolls_back_schema_and_existing_image(self):
        db.DB_PATH = Path(self.temp.name) / "upgrade-failure.db"
        with patch("journal_mcp.pattern_schema.migrate"):
            db.init_db()
        old_schema = db.fetch_one("SELECT sql FROM sqlite_master WHERE name='evidence'")["sql"]
        from journal_mcp import pattern_schema
        original = pattern_schema.migrate
        def fail_after_rebuild(conn):
            original(conn)
            raise RuntimeError("Injected migration interruption")
        with patch.object(pattern_schema, "migrate", side_effect=fail_after_rebuild), self.assertRaises(RuntimeError):
            db.init_db()
        self.assertEqual(db.fetch_one("SELECT sql FROM sqlite_master WHERE name='evidence'")["sql"], old_schema)
        self.assertIsNone(db.fetch_one("SELECT name FROM sqlite_master WHERE name='pattern_entries'"))
        db.init_db()
        self.assertIsNotNone(db.fetch_one("SELECT name FROM sqlite_master WHERE name='pattern_entries'"))

    def test_followup_correction_changed_retry_and_parent_update_repeat(self):
        entry = self.entry()
        payload = {"pattern_entry_id": entry["id"], "followup_key": "update-parent", "note": "Practised", "parent_update": {"next_session_intention": "Wait"}}
        first = p.append_pattern_followup(payload, "parent-update", expected_revision=entry["revision"])
        self.assertEqual(first, p.append_pattern_followup(payload, "parent-update", expected_revision=entry["revision"]))
        with self.assertRaises(p.PatternError) as changed:
            p.append_pattern_followup({**payload, "note": "Changed"}, "parent-update", expected_revision=entry["revision"])
        self.assertEqual(changed.exception.payload["code"], "idempotency_conflict")
        self.assertEqual(r.list_records("pattern_followups")["total"], 1)

    def test_reports_keep_examples_bounded_and_other_accounts_excluded(self):
        self.entry(situation="long observation " * 100, urge_response="resisted", plan_adherence="followed")
        other = r.save_record("accounts", {"name": "Outside scope"})
        self.entry(account_id=other["id"], urge_response="acted_on")
        scoped = self.report(include_examples=True)
        self.assertEqual(scoped["totals"]["occurrences"], 1)
        self.assertLessEqual(len(scoped["entries"][0]["situation"]), 500)
        self.assertNotIn("situation", self.report()["entries"][0])
        self.assertEqual(scoped["comparison"]["prior"]["sample_size"], 0)
        self.assertFalse(scoped["comparison"]["absence_means_resolved"])

    def test_context_reassignment_and_cross_account_evidence_are_rejected(self):
        trade = self.trade()
        self.entry(trade_ids=[trade["id"]])
        other = r.save_record("accounts", {"name": "Other"})
        with self.assertRaises(Exception):
            r.save_record("trades", {"account_id": other["id"]}, trade["id"])
        self.assertEqual(r.get_record("trades", trade["id"])["account_id"], self.account)
        other_pattern = self.entry(account_id=other["id"], symbol="SYNTH")
        evidence = self.upload(pattern_entry_id=other_pattern["id"])
        with self.assertRaises(p.PatternError) as mismatch:
            self.entry(evidence_ids=[evidence["id"]])
        self.assertEqual(mismatch.exception.payload["code"], "account_mismatch")

    def test_export_nested_values_as_json_and_web_view(self):
        entry = self.entry(trigger_tags=["observed"])
        from journal_mcp import operations
        export = operations.export_records("pattern_entries", {"account_id": self.account}, format="csv")
        import csv
        contents = operations.export_path(export["filename"]).read_text(encoding="utf-8-sig")
        row = next(csv.DictReader(io.StringIO(contents)))
        self.assertEqual(json.loads(row["trigger_tags"]), ["observed"])
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string(f"from utils.pattern_ui import show\nshow({self.account})").run(timeout=15)
        self.assertFalse(app.exception, str(app.exception))
        self.assertTrue(any("Pending reflections: 1" in item.value for item in app.caption))

    def test_mcp_typed_discovery_and_structured_conflicts(self):
        async def run():
            async with Client(create_server()) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(len(tools), 38)
                self.assertIn("$defs", tools["save_pattern_entry"].input_schema)
                schema = await client.call_tool("journal_schema", {})
                self.assertEqual(schema.structured_content["schema_version"], "1.2.0")
                self.assertIn("create_schema", schema.structured_content["entities"]["pattern_entries"]["contract"])
                result = await client.call_tool("save_pattern_entry", {"data": {"entry_key": "mcp", "account_id": self.account, "event_date": "2026-10-18", "timezone": "Australia/Brisbane", "situation": "Test"}, "idempotency_key": "mcp-create"})
                self.assertFalse(result.is_error, result)
                record = result.structured_content
                image = self.image()
                uploaded = await client.call_tool("upload_evidence", {"content_base64": base64.b64encode(image).decode(), "data": {"pattern_entry_id": record["id"], "filename": "mcp.png", "mime_type": "image/png", "stage": "pre", "captured_at": NOW, "symbol": "SYNTH", "timeframe": "1m", "source": "Synthetic MCP test"}, "idempotency_key": "mcp-image"})
                self.assertFalse(uploaded.is_error, uploaded)
                downloaded = await client.call_tool("read_evidence", {"evidence_id": uploaded.structured_content["evidence"]["id"]})
                self.assertFalse(downloaded.is_error, downloaded)
                self.assertEqual(base64.b64decode(downloaded.structured_content["content"]), image)
                current = (await client.call_tool("get_record", {"entity": "pattern_entries", "record_id": record["id"]})).structured_content
                followed = await client.call_tool("append_pattern_followup", {"data": {"pattern_entry_id": record["id"], "followup_key": "mcp-followup", "note": "Practised response", "response_practised": True, "parent_update": {"follow_up_status": "reviewed"}}, "expected_revision": current["revision"], "idempotency_key": "mcp-followup"})
                self.assertFalse(followed.is_error, followed)
                report = await client.call_tool("generate_pattern_report", {"account_ids": [self.account], "start_date": "2026-10-18", "end_date": "2026-10-18", "filters": {"follow_up_status": "reviewed"}})
                self.assertFalse(report.is_error, report)
                self.assertEqual(report.structured_content["total"], 1)
                conflict = await client.call_tool("save_pattern_entry", {"data": {"situation": "Changed"}, "pattern_entry_id": record["id"], "expected_revision": 999, "idempotency_key": "mcp-conflict"})
                self.assertTrue(conflict.is_error)
                self.assertEqual(conflict.structured_content["error"]["code"], "revision_conflict")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
