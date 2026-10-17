"""Start MCP without inheriting terminal/API pipe handles on Windows."""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("server_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.server_args[1:] if args.server_args and args.server_args[0] == "--" else args.server_args
    if not command:
        parser.error("Server command is required")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    with (args.log_dir / "server.stdout.log").open("w") as output, (args.log_dir / "server.stderr.log").open("w") as errors:
        process = subprocess.Popen(
            [sys.executable, *command], cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL, stdout=output, stderr=errors, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
        )
    print(process.pid)


if __name__ == "__main__":
    main()
