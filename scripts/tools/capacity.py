#!/usr/bin/env python3
"""Discover Linux capacity operations from source or run the selected native client."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from repo_platform.tooling.tool_handshake import emit_payload, tool_result_payload


def main(args: list[str]) -> int:
    front = args[:args.index("--")] if "--" in args else args
    tokens = iter(front)
    verb = None
    show_help = not args
    output_format = "text"
    for token in tokens:
        if token in {"--state-root", "--allocation-id", "--revision", "--step-id",
                     "--target-ref", "--timeout-ms", "--supervisor-ref"}:
            next(tokens, None)
        elif token == "--format":
            output_format = next(tokens, "")
        elif token.startswith("--format="):
            output_format = token.partition("=")[2]
        elif token in {"--help", "-h"}:
            show_help = True
        elif verb is None and not token.startswith("-"):
            verb = token

    if show_help or verb == "usage":
        if output_format not in {"json", "text"}:
            print("--format requires json or text", file=sys.stderr)
            return 2
        usage = json.loads((ROOT / "repo_platform/capacity/usage.json").read_text(encoding="utf-8"))
        emit_payload(usage, output_format=output_format, stream=sys.stdout)
        return 0

    configured = os.environ.get("APEIROS_CAPACITY_EXECUTABLE")
    executable = configured if configured is not None else shutil.which("apeiros-capacity")
    if sys.platform != "linux":
        reason = "Native capacity operations require Linux."
    elif not executable or (configured is not None and not Path(configured).is_absolute()):
        reason = "Install apeiros-capacity on PATH or select an absolute APEIROS_CAPACITY_EXECUTABLE."
    else:
        try:
            os.execv(executable, [executable, *args])
        except OSError as error:
            reason = f"The selected capacity executable could not start ({error.strerror})."

    failure = tool_result_payload(tool_id="tool.capacity", verb=verb or "request", status="error", summary=reason,
                                  errors=[{"code": "capacity_executable_unavailable", "message": reason}])
    emit_payload(failure, output_format="json", stream=sys.stderr)
    return 125 if verb in {"dispatch", "guard"} else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
