"""Short-lived heavy-operation worker. Receives one request from its parent over stdin."""
from __future__ import annotations

import contextlib
import json
import math
import sys
from pathlib import Path


def normalise(value):
    if isinstance(value, dict):
        return {str(k): normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalise(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return normalise(value.item())
    return value


def main():
    import database
    from journal_mcp import operations
    allowed = {name: getattr(operations, name) for name in (
        "import_trades", "market_analysis", "reference_data", "replay_action", "ftp_sync")}
    request = json.load(sys.stdin)
    database.DB_PATH = Path(request["database"])
    try:
        # Legacy helpers can print progress. Only the result goes over stdout.
        with contextlib.redirect_stdout(sys.stderr):
            result = allowed[request["operation"]](**request["arguments"])
        response = {"ok": True, "result": normalise(result)}
    except Exception as exc:
        response = {"ok": False, "error": str(exc)}
    json.dump(response, sys.stdout, ensure_ascii=True, allow_nan=False)


if __name__ == "__main__":
    main()
