"""Use the configured journal's live MCP schema when a chat has cached old tools.

Prefer native MCP tools when their parameters are current. This stdlib bridge
does no polling, automatic retries, authentication changes or broker execution.
"""

import argparse
import base64
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


DEFAULT_URL = "http://mt5tools:8765/mcp"
ALLOWED_TOOLS = {
    "journal_schema", "list_records", "get_record", "save_record",
    "trade_action", "assign_playbook", "evaluate_playbook", "risk_calculator",
    "generate_report", "save_report", "save_session", "save_candidate",
    "append_management_event", "record_fill", "save_account_snapshot",
    "save_risk_regime", "save_instrument_spec", "size_candidate",
    "upload_evidence", "read_evidence", "reconcile_candidate",
    "save_psychology_pattern", "save_pattern_entry", "append_pattern_followup",
    "generate_pattern_report", "migrate_pattern_entries",
}


class JournalError(RuntimeError):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload


def decode_response(response, request_id):
    """Read JSON or one matching JSON-RPC event from a streaming HTTP response."""
    if response.status in (202, 204):
        return None
    if "text/event-stream" not in response.headers.get("Content-Type", ""):
        return json.load(response)
    data = []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif not line and data:
            event = json.loads("\n".join(data))
            data = []
            if event.get("id") == request_id:
                return event
    if data:
        event = json.loads("\n".join(data))
        if event.get("id") == request_id:
            return event
    raise JournalError("MCP stream ended without the matching response; do not blindly retry a write.")


def unpack(result):
    if result.get("isError"):
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and isinstance(structured.get("error"), dict):
            error = structured["error"]
            detail = str(error.get("code", "journal_error")) + ": " + str(error.get("message", "Journal tool returned an error."))
            if "current_revision" in error:
                detail += " (current_revision=" + str(error["current_revision"]) + ")"
            raise JournalError(detail, structured)
        detail = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        raise JournalError(detail or "Journal tool returned an error.")
    if "structuredContent" in result:
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except (ValueError, KeyError):
                pass
    return result


class JournalMCP:
    def __init__(self, url=DEFAULT_URL, timeout=30):
        self.url = url
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        self.request_id = 0
        self.tools = None
        init = self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "neo-journal-bridge", "version": "0.5.0"}})
        protocol = init.get("protocolVersion")
        if protocol:
            self.headers["MCP-Protocol-Version"] = protocol
        self.rpc("notifications/initialized", notification=True)

    def rpc(self, method, params=None, notification=False):
        self.request_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self.request_id
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(self.url, json.dumps(payload).encode("utf-8"), self.headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.headers["Mcp-Session-Id"] = session
                message = decode_response(response, payload.get("id"))
        except (OSError, urllib.error.URLError) as error:
            raise JournalError("MCP request failed; write outcome may be uncertain. Reconcile keys/revisions before retrying. " + str(error)) from error
        if notification:
            return None
        if not message or message.get("id") != payload["id"]:
            raise JournalError("MCP returned a missing or mismatched request ID.")
        if "error" in message:
            raise JournalError("MCP protocol error: " + json.dumps(message["error"]))
        return message["result"]

    def list_tools(self):
        found = {}
        cursor = None
        while True:
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            for tool in page.get("tools", []):
                found[tool["name"]] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = found
        return found

    def call(self, name, arguments):
        if name not in ALLOWED_TOOLS:
            raise JournalError("Tool is outside the Neo bridge scope: " + name)
        if self.tools is None:
            self.list_tools()
        if name not in self.tools:
            raise JournalError("Tool is not exposed by the current MCP server: " + name)
        schema = self.tools[name]["inputSchema"]
        missing = set(schema.get("required", [])) - set(arguments)
        extra = set(arguments) - set(schema.get("properties", {}))
        if missing or (extra and schema.get("additionalProperties") is not True):
            raise JournalError("Arguments do not match live tool schema: missing=" + str(sorted(missing)) + ", extra=" + str(sorted(extra)))
        return unpack(self.rpc("tools/call", {"name": name, "arguments": arguments}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Use only the user's verified configured MCP endpoint.")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--out", required=True)
    describe = sub.add_parser("describe")
    describe.add_argument("tool")
    call = sub.add_parser("call")
    call.add_argument("tool", choices=sorted(ALLOWED_TOOLS))
    call.add_argument("--args-file", required=True, help="UTF-8 JSON containing the exact live tool arguments.")
    call.add_argument("--out", required=True, help="Save full result locally; image bytes are never printed.")
    call.add_argument("--image", help="Actual local image for upload_evidence only; encoded without printing bytes.")
    args = parser.parse_args()
    try:
        if args.command == "call":
            payload = json.loads(Path(args.args_file).read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise JournalError("Arguments must be a JSON object.")
            if args.image:
                if args.tool != "upload_evidence":
                    raise JournalError("--image is only valid with upload_evidence.")
                content = Path(args.image).read_bytes()
                if len(content) > 8 * 1024 * 1024:
                    raise JournalError("Image exceeds the journal's 8 MiB limit.")
                if "content_base64" in payload:
                    raise JournalError("Supply either --image or content_base64, not both.")
                payload["content_base64"] = base64.b64encode(content).decode("ascii")
        client = JournalMCP(args.url)
        tools = client.list_tools()
        if args.command == "describe":
            if args.tool not in tools:
                raise JournalError("Tool is not exposed by the current server.")
            print(json.dumps(tools[args.tool], indent=2))
            return
        result = {"tools": list(tools.values())} if args.command == "list" else client.call(args.tool, payload)
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "operation": args.command, "output": str(path.resolve())}))
    except (JournalError, OSError, ValueError) as error:
        if isinstance(error, JournalError) and error.payload is not None and args.command == "call":
            try:
                path = Path(args.out)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(error.payload, indent=2) + "\n", encoding="utf-8")
            except OSError as output_error:
                print("Could not save structured error: " + str(output_error), file=sys.stderr)
        print(str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
