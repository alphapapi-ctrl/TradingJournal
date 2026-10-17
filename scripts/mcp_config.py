"""Read/validate/write MCP startup JSON for PowerShell, without loading the app."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.mcp_config import load_config, normalise, save_config


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--patch-stdin", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.patch_stdin:
            patch = json.load(sys.stdin)
            config = normalise({**config, **patch})
        if args.save:
            config = save_config(config, args.config)
        print(json.dumps(config))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
