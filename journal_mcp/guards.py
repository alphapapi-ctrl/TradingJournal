"""Atomic retry and concurrency helpers shared by all workflow writes."""
import hashlib
import json
import database as db


def replay(key, operation, arguments):
    fingerprint = hashlib.sha256(json.dumps([operation, arguments], sort_keys=True, allow_nan=False).encode()).hexdigest()
    if key is not None:
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValueError("idempotency_key must be 1–200 characters")
        old = db.fetch_one("SELECT * FROM mcp_requests WHERE request_key=?", (key,))
        if old:
            if old["fingerprint"] != fingerprint:
                raise ValueError("Idempotency key already used for different input")
            return fingerprint, json.loads(old["response"])
    return fingerprint, None


def remember(key, fingerprint, response):
    if key is not None:
        db.execute("INSERT INTO mcp_requests VALUES (?,?,?)", (key, fingerprint, json.dumps(response, allow_nan=False)))
    return response


def check_revision(row, expected_revision):
    if expected_revision is not None:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not row or row.get("revision") != expected_revision:
            raise ValueError("Revision conflict; read the current record and merge before retrying")
