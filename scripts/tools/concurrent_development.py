#!/usr/bin/env python3
"""Portable source-integration CLI entrypoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
# This check precedes every import from a checkout that can be refreshed.
# Never execute a replacement tool path supplied by mutable repository state.
if __name__ == "__main__" and (REPO_ROOT / ".git/agent-framework/main-upkeep/state.json").exists():
    message = ("This toolkit checkout is configured for maintained refresh. Run "
               "python <separate-toolkit>/scripts/tools/concurrent_development.py --root <selected-workspace> "
               "with the same command and selectors.")
    if "--format=json" in sys.argv or any(a == "--format" and b == "json" for a, b in zip(sys.argv, sys.argv[1:])):
        print(json.dumps({"schema_version": "tool_result.v1", "tool_id": "tool.concurrent_development",
                          "status": "error", "summary": message, "errors": [{"code": "main_upkeep_external_toolkit_required",
                                                                             "message": message}]}))
    else:
        print(message, file=sys.stderr)
    raise SystemExit(2)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repo_platform.concurrent_development.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
