"""Check a journal MCP endpoint without changing any journal records."""
import argparse
import asyncio
import json
import time

from mcp import Client


async def check(url, sample_seconds, verify_journal=False):
    async with Client(url, read_timeout_seconds=20) as client:
        tools = (await client.list_tools()).tools
        first = await client.call_tool("diagnostics")
        if first.is_error:
            raise RuntimeError(str(first))
        result = {"endpoint": url, "tools": len(tools), "runtime": first.structured_content}
        if verify_journal:
            counts = {}
            for entity in ("accounts", "trades", "journal_entries", "playbooks"):
                page = await client.call_tool("list_records", {"entity": entity, "limit": 1})
                if page.is_error:
                    raise RuntimeError(str(page))
                counts[entity] = page.structured_content["total"]
            start_report = time.monotonic()
            report = await client.call_tool("generate_report", {"kind": "all"})
            if report.is_error:
                raise RuntimeError(str(report))
            result["journal_verification"] = {
                "record_counts": counts, "report_trade_count": report.structured_content["trade_count"],
                "report_seconds": round(time.monotonic() - start_report, 3),
                "currency_groups": len(report.structured_content["currencies"]),
                "records_changed": False,
            }
        if sample_seconds:
            start = time.monotonic()
            await asyncio.sleep(sample_seconds)
            second = (await client.call_tool("diagnostics")).structured_content
            result["idle_sample_seconds"] = round(time.monotonic() - start, 2)
            result["cpu_seconds_during_idle_sample"] = round(second["cpu_seconds"] - first.structured_content["cpu_seconds"], 3)
            result["runtime_after_sample"] = second
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--sample-seconds", type=float, default=0)
    parser.add_argument("--verify-journal", action="store_true", help="Read record counts and generate a report without changing records")
    args = parser.parse_args()
    if not 0 <= args.sample_seconds <= 30:
        parser.error("sample-seconds must be 0–30")
    asyncio.run(check(args.url, args.sample_seconds, args.verify_journal))
