"""Run: python mcp_server.py --host 0.0.0.0 --port 8765 (or --transport stdio)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def lower_priority():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        return bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000))  # BELOW_NORMAL
    try:
        os.nice(5)
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="Trading Journal MCP server, no authentication, private LAN only")
    parser.add_argument("--transport", choices=("streamable-http", "stdio"), default="streamable-http")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, help="Optional database override; normally use the same data/journal.db as Streamlit")
    parser.add_argument("--allowed-host", action="append", default=[], help="Additional LAN hostname/IP without a port; repeatable")
    parser.add_argument("--allowed-origin", action="append", default=[], help="Explicit browser origin, if a browser-based MCP client needs one")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    for name in args.allowed_host:
        if any(c in name for c in ("*", "/", "\\", " ", "\r", "\n")):
            parser.error("allowed-host must be an exact hostname or IP")
    # Restrict computational libraries before any on-demand imports occur.
    for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ[variable] = "1"
    priority_lowered = lower_priority()
    import database
    if args.database:
        database.DB_PATH = args.database.expanduser().resolve()
    database.init_db()
    try:
        from journal_mcp.server import create_server, http_app
    except ModuleNotFoundError as exc:
        if exc.name in ("mcp", "anyio", "uvicorn", "starlette"):
            parser.exit(1, "Install server dependencies first: python -m pip install -r requirements-mcp.txt\n")
        raise
    server = create_server()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        import uvicorn
        print(f"Trading Journal MCP: http://{args.host}:{args.port}/mcp", file=sys.stderr)
        print(f"Database: {database.DB_PATH}; one tool at a time; below-normal priority: {priority_lowered}", file=sys.stderr)
        uvicorn.run(http_app(server, args.host, args.allowed_host, args.allowed_origin),
                    host=args.host, port=args.port, workers=1, limit_concurrency=16,
                    access_log=False, log_level="warning", proxy_headers=False, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
