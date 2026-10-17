"""Package MCP application changes for copying onto an existing journal install.

Includes explicitly selected source files only: never data, credentials, .git,
virtual environments, or broker exports. Does not deploy or overwrite anything.
"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "database.py", "utils/trade_ops.py", "utils/playbook_logic.py", "mcp_server.py", "requirements-mcp.txt",
    "launch_mcp.bat", "scripts/launch_mcp.ps1", "scripts/stop_mcp.ps1",
    "scripts/check_mcp.py", "scripts/start_mcp_background.py", "scripts/package_mcp.py", "MCP_SETUP.md", "README.md",
)


def main():
    files = [ROOT / name for name in FILES]
    files += sorted((ROOT / "journal_mcp").glob("*.py"))
    files += sorted((ROOT / "tests").glob("test_journal_mcp.py"))
    for path in files:
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise RuntimeError(f"Missing or invalid source file: {path}")
    output = ROOT / "deploy_package" / "trading-journal-mcp.zip"
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(f"Created {output} ({output.stat().st_size:,} bytes; {len(files)} source files; no personal data)")


if __name__ == "__main__":
    main()
