"""Official MCP transport, one tool at a time, with no background jobs."""
from __future__ import annotations

import functools
import inspect
import ipaddress
import json
import math
import socket
import sqlite3
import subprocess
import sys
import os
from pathlib import Path
from typing import Any

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.responses import FileResponse, JSONResponse

from journal_mcp import operations, records, reporting


def isolated_call(operation: str, arguments: dict) -> dict:
    import database
    request = {"database": str(Path(database.DB_PATH).resolve()), "operation": operation, "arguments": arguments}
    environment = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        environment[key] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "journal_mcp.worker"],
            input=json.dumps(request, allow_nan=False), text=True, encoding="utf-8", capture_output=True,
            cwd=Path(__file__).resolve().parents[1], env=environment, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Operation exceeded five minutes. Check the journal before retrying a write; narrow large imports/reference queries.") from exc
    if completed.returncode:
        raise ValueError("Journal helper failed. Check that the journal's optional dependencies are installed.")
    response = json.loads(completed.stdout)
    if not response["ok"]:
        raise ValueError(response["error"])
    return response["result"]


def clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return clean_json(value.item())
    return value


def create_server() -> MCPServer:
    server = MCPServer(
        "Trading Journal", version="1.0.0", log_level="WARNING",
        instructions=(
            "Full access to a personal trading journal. Start with journal_schema and list_records(accounts). "
            "Use existing account IDs and patch only the fields requested. Page through results with next_offset. "
            "Use trade_action for closes, position_action for merges, and assign_playbook for rule scores. "
            "Generate reports with journal calculations; amounts are separated by currency. "
            "This server records journal activity and simulated replay trades; it cannot place live broker orders. "
            "Market data and reports run only when requested. Do not poll in the background."
        ),
    )
    limiter = anyio.CapacityLimiter(1)

    descriptions = {
        "journal_schema": "Discover record fields, required inputs and supported entities. Call before creating/editing records.",
        "list_records": "List/search/filter journal data with pagination (max 500). Entity: accounts, trades, positions, journal_entries, journal_templates, playbooks, playbook_rules, playbook_risk_rules, risk_setups, replay_sessions, import_history, day_features. Date filters are inclusive YYYY-MM-DD; filters are exact field/value matches. Use next_offset until null.",
        "get_record": "Get a record by entity and ID; positions include their trades and playbooks include rules/thresholds.",
        "save_record": "Create a record or patch an existing one (record_id). Discover fields with journal_schema. Omissions preserve values; null clears nullable fields. Supports journal pre/post text, psychology, categories, templates, accounts, saved risk setups, playbooks/rules and replay state. Use dedicated tools for closes/merges/rule checks.",
        "delete_record": "Delete a record by entity/ID. Linked journal text is retained and unlinked; deleting a position preserves its trades. Deleting an account unlinks data without deleting trading history.",
        "trade_action": "Close, partial_close, or reopen a JOURNAL trade; no live orders. Close requires exit_price; partial_close also requires close_quantity. Optional pnl overrides price-based P&L for contract instruments. Updates merged totals and risk metadata atomically.",
        "position_action": "Merge 2–500 same-symbol, same-direction trades from one account, or unmerge an existing position. action: merge/unmerge. Unmerge retains individual trades and journal text.",
        "assign_playbook": "Assign/clear a playbook on exactly one trade or position. rules_met maps rule IDs (strings) to booleans. Calculates scores and propagates assignment to all position trades. Null playbook_id clears assignment.",
        "evaluate_playbook": "Evaluate required/optional/bonus rules, OR groups and risk thresholds without saving. rules_met maps rule ID strings to true/false.",
        "generate_report": "Calculate performance, categories (Menaker), discipline, playbook compliance, risk-threshold outcomes and open holdings. kind: all/performance/categories/discipline/playbooks/risk/open_positions. Filter by account, inclusive dates, symbol or playbook. Different currencies are separated. Max 20,000 trades; equity curves sampled to 500 points.",
        "save_report": "Save a calculated report as markdown or JSON. Returns download_path and an MCP resource URI; optional filters match generate_report.",
        "export_records": "Export one page of records to CSV/JSON on the journal machine. Max 500 rows; next_offset indicates more pages. Returns a download path and resource URI.",
        "read_export": "Read a saved export in base64 chunks (max 256 KiB); follow next_offset. Use for clients without HTTP downloads/resources.",
        "import_trades": "Import broker CSV/HTML text or base64 bytes into an existing account. Supports TradingView, IBKR, CMC, MT5, IC Markets. Defaults to dry_run=true preview; repeat with false to save. Deduplication is scoped to account/broker/ID. Limit 8 MiB / 5,000 rows; all writes roll back together on error.",
        "settings": "Get/update journal app settings (AI, FTP, risk defaults, display and network). action get/update; values is key/value map. Credentials are redacted unless include_secrets=true. MCP startup options are separate.",
        "risk_calculator": "Calculate position quantity from entry, stop, balance, risk percentage, point value and quantity step. Optional playbook rules adjust risk. Returns sizing only; never places orders.",
        "market_analysis": "On-demand market data: action quote/history/analysis/holders/backfill_holders. Symbol and exchange_hint identify yfinance/ASX instrument. History is daily; max 500 bars/notices. External market sources may be delayed/unavailable.",
        "reference_data": "Reference/replay data: action instruments/days/bars/rebuild_index/update_caches/update_log. Uses configured School Run caches. days filters match gap/news/bar-pattern criteria. bars supports pagination. Rebuild/update are explicitly requested heavy maintenance, never automatic.",
        "replay_action": "Replay trainer: create_session with data {name,instrument,day,balance,currency?}; stats for a session; open_trade with data {side,price,qty,bar_time,sl?,tp?,symbol?}, validated against historical bars; delete_session retains demo account/history. Update replay state through save_record; close trades through trade_action.",
        "ftp_sync": "Explicitly run the journal's configured MT5 FTP import once. Does not start a polling thread or connect directly to trading terminals.",
        "backup_database": "Create a consistent SQLite online backup in data/mcp/backups, including committed WAL changes. External caches/files are separate.",
        "diagnostics": "Get this server's memory, CPU time, database location and lightweight runtime configuration.",
    }

    def register(function, name=None, read_only=False, destructive=False):
        @functools.wraps(function)
        async def call(**kwargs):
            try:
                # Capacity includes queued tools; cancellation doesn't abandon a write midway.
                heavy = function in (operations.import_trades, operations.market_analysis, operations.replay_action, operations.ftp_sync)
                heavy = heavy or (function is operations.reference_data and kwargs.get("action", "days") not in ("update_caches", "update_log"))
                work = functools.partial(isolated_call, function.__name__, kwargs) if heavy else functools.partial(function, **kwargs)
                result = await anyio.to_thread.run_sync(work, limiter=limiter)
                return clean_json(result)
            except (ValueError, TypeError, KeyError) as exc:
                raise ToolError(str(exc)) from exc
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    raise ToolError("Journal is busy; retry this operation shortly") from exc
                raise
        call.__signature__ = inspect.signature(function, eval_str=True).replace(return_annotation=dict[str, Any])
        call.__annotations__ = {key: param.annotation for key, param in call.__signature__.parameters.items()}
        call.__annotations__["return"] = dict[str, Any]
        server.tool(name=name, description=descriptions[name or function.__name__], annotations=ToolAnnotations(
            read_only_hint=read_only, destructive_hint=destructive,
            idempotent_hint=read_only, open_world_hint=function in (operations.market_analysis, operations.reference_data, operations.ftp_sync),
        ))(call)

    register(records.schema, "journal_schema", read_only=True)
    register(records.list_records, read_only=True)
    register(records.details, "get_record", read_only=True)
    register(records.save_record)
    register(records.delete_record, destructive=True)
    register(records.trade_action)
    register(records.position_action)
    register(records.assign_playbook)
    register(records.evaluate_playbook, read_only=True)
    register(reporting.generate_report, read_only=True)
    register(operations.save_report)
    register(operations.export_records)
    register(operations.read_export, read_only=True)
    register(operations.import_trades)
    register(operations.settings)
    register(operations.risk_calculator, read_only=True)
    register(operations.market_analysis)
    register(operations.reference_data)
    register(operations.replay_action)
    register(operations.ftp_sync)
    register(operations.backup_database)
    register(operations.diagnostics, read_only=True)

    @server.resource("journal://schema", mime_type="application/json")
    async def schema_resource() -> str:
        result = await anyio.to_thread.run_sync(records.schema, limiter=limiter)
        return json.dumps(result)

    @server.resource("journal://exports/{filename}", mime_type="text/plain")
    async def export_resource(filename: str) -> str:
        def read():
            path = operations.export_path(filename)
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Export exceeds 1 MiB; use read_export to retrieve it in chunks")
            return path.read_text(encoding="utf-8-sig")
        return await anyio.to_thread.run_sync(read, limiter=limiter)

    @server.custom_route("/health", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok", "service": "trading-journal-mcp", "version": "1.0.0", "authentication": False, "pid": os.getpid()})

    @server.custom_route("/exports/{filename}", methods=["GET"])
    async def download(request):
        try:
            path = operations.export_path(request.path_params["filename"])
            return FileResponse(path, filename=path.name)
        except ValueError:
            return JSONResponse({"error": "Export not found"}, status_code=404)
    return server


def local_hosts(extra: list[str] | None = None) -> list[str]:
    names = {"localhost", "127.0.0.1", "[::1]", socket.gethostname().lower(), socket.getfqdn().lower()}
    try:
        names.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None))
    except OSError:
        pass
    names.update(extra or [])
    # Entries are names/IPs, never arbitrary header wildcards.
    names = {("[" + name + "]") if ":" in name and not name.startswith("[") else name.lower()
             for name in names if name and name not in ("0.0.0.0", "::")}
    return sorted({entry for name in names for entry in (name, name + ":*")})


class PrivateNetworkOnly:
    """Keep unauthenticated HTTP on loopback / private LAN; trust no proxy headers."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            client = scope.get("client")
            try:
                address = ipaddress.ip_address(client[0]) if client else None
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                    address = address.ipv4_mapped
                allowed = address and (address.is_private or address.is_loopback or address.is_link_local)
            except ValueError:
                allowed = False
            if not allowed:
                await JSONResponse({"error": "Private LAN clients only"}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def http_app(server: MCPServer, host: str, allowed_hosts: list[str] | None = None,
             allowed_origins: list[str] | None = None):
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=local_hosts([*(allowed_hosts or []), host] if host != "0.0.0.0" else allowed_hosts),
        allowed_origins=allowed_origins or [],
    )
    app = server.streamable_http_app(
        host=host, stateless_http=True, json_response=True, max_request_body_size=12 * 1024 * 1024,
        transport_security=security,
    )
    # Apply host/origin validation to health/download routes as well as MCP.
    from mcp.server.transport_security import TransportSecurityMiddleware
    validator = TransportSecurityMiddleware(security)
    class ValidateHeaders:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                from starlette.requests import Request
                response = await validator.validate_request(Request(scope, receive), is_post=scope["method"] == "POST")
                if response is not None:
                    await response(scope, receive, send)
                    return
            await self.wrapped(scope, receive, send)
    return PrivateNetworkOnly(ValidateHeaders(app))
