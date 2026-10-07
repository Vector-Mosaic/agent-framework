"""Optional private-human clipboard and pinned SSH downloader handoffs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_platform.credentials import clipboard, custody, terminal
from repo_platform.tooling.tool_handshake import (
    ToolSelector, ToolVerb, argparse_command_details, emit_payload, tool_result_payload, tool_usage_payload,
)

USAGE = {
    "id": "credential_adapters",
    "purpose": "Optional OS clipboard and guarded Linux tmux downloader integration with canonical custody.",
    "commands": {
        "usage": "Offline interface summary; no configuration, clipboard or credential access.",
        "clipboard-paste": "Interactive private human paste followed by safe owned-value cleanup; not an automatic browser paste.",
        "clipboard-intake": "Read a credential the owner explicitly supplied on the clipboard, into absent custody only.",
        "terminal-paste": "Verify an exact guarded downloader prompt on a configured pinned SSH host, then deliver via stdin."
    },
    "selectors": {
        "--config": "Account registry path, with the same defaults as agent_credentials.py.",
        "--account": "Exact active canonical account identifier.",
        "--origin": "Exact admitted HTTPS origin; registration does not itself authorize account use.",
        "--credential": "password/security_answer for clipboard intake; paste also supports active totp_code or pending totp_enrollment_code; terminal password only.",
        "--backend": "Explicit windows, wayland or x11 clipboard session; no automatic fallback.",
        "--targets": "User-owned JSON SSH target registry; no target defaults.",
        "--target": "Exact SSH target ID in that registry.",
        "--pane": "Observed tmux session:window.pane selector.",
        "--expected-prompt": "Exact current password prompt; metadata, never the password.",
        "--foreground-process": "curl or wget, subject to the documented closed option grammar.",
        "--format": "text or json, containing only safe status."
    },
    "security": [
        "Never supply a credential in argv, environment, redirected console input, shell text or files.",
        "Clipboard use requires explicit authorization for that account and immediate private human paste/intake.",
        "Windows cleanup compares under one clipboard lock; Linux delivery releases only its owned selection.",
        "Linux intake preserves an unowned clipboard; no unsafe read-then-clear operation is used.",
        "Headless Linux has no clipboard capability. Native browser handoff is a separate clipboard-free route.",
        "Terminal requires a trusted Linux tmux host, verified public host-key pin, and existing SSH identity.",
        "Delivery is not proof of sign-in or download success. Never automatically retry uncertain delivery."
    ],
    "docs": ["docs/integrations/clipboard.md", "docs/integrations/terminal.md"]
}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name, description in USAGE["commands"].items():
        command = commands.add_parser(name, help=description)
        command.add_argument("--format", choices=("text", "json"), default="text")
        if name == "usage":
            continue
        command.add_argument("--config", type=Path)
        command.add_argument("--account", required=True)
        command.add_argument("--origin", required=True)
        purposes = (("password",) if name == "terminal-paste" else
                    ("password", "security_answer", "totp_code", "totp_enrollment_code") if name == "clipboard-paste" else
                    ("password", "security_answer"))
        command.add_argument("--credential", choices=purposes, required=True)
        if name.startswith("clipboard-"):
            command.add_argument("--backend", choices=("windows", "wayland", "x11"), required=True)
        else:
            command.add_argument("--targets", type=Path, required=True)
            command.add_argument("--target", required=True)
            command.add_argument("--pane", required=True)
            command.add_argument("--expected-prompt", required=True)
            command.add_argument("--foreground-process", choices=("curl", "wget"), required=True)
    return result


def perform(args):
    if args.command == "usage":
        entrypoint = "python scripts/tools/credential_adapters.py"
        return tool_usage_payload(
            tool_id=USAGE["id"], purpose=USAGE["purpose"], entrypoint=entrypoint,
            safe_first_calls=[{"label": "Offline interface", "cmd": entrypoint + " usage --format json"}],
            verbs=[ToolVerb(name, "none" if name == "usage" else "protected credential or clipboard/terminal effect", description)
                   for name, description in USAGE["commands"].items()],
            selectors=[ToolSelector(name, name not in {"--config", "--format"}, description)
                       for name, description in USAGE["selectors"].items()],
            output_formats=["text", "json"], side_effects={"boundaries": USAGE["security"]},
            examples=[{"label": "Private human paste", "cmd": entrypoint + " clipboard-paste --account example --origin https://example.test --credential password --backend windows"}],
            docs=USAGE["docs"], command_details=argparse_command_details(parser()),
            errors=[{"code": "terminal_prompt_unverified", "meaning": "No credential read; reconcile the exact target."},
                    {"code": "terminal_delivery_uncertain", "meaning": "Inspect the target before any retry."},
                    {"code": "clipboard_backend_unavailable", "meaning": "Install the chosen desktop backend; no fallback."}],
        )
    selected = custody.resolve_selection(args.account, args.credential, args.origin, config=args.config)
    if args.command == "clipboard-paste":
        return clipboard.manual_handoff(selected, backend=args.backend)
    if args.command == "clipboard-intake":
        return clipboard.intake(selected, backend=args.backend)
    return terminal.deliver(selected, targets_path=args.targets, target_id=args.target,
                            pane=args.pane, prompt=args.expected_prompt, process_name=args.foreground_process)


def main(argv=None):
    supplied = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(supplied or ["usage"])
    try:
        result, code = perform(args), 0
    except (custody.CustodyError, clipboard.ClipboardError, terminal.TerminalError) as error:
        result, code = {"status": "error", "code": error.code}, 2
    except (KeyboardInterrupt, EOFError):
        result, code = {"status": "cancelled"}, 130
    except Exception:
        result, code = {"status": "error", "code": "credential_adapter_failed"}, 2
    if args.command != "usage" or code:
        result = tool_result_payload(tool_id=USAGE["id"], verb=args.command,
            status="error" if code else "ok", summary="Protected adapter operation completed." if not code else "Protected adapter operation did not complete.",
            data=result, errors=[{"code": result.get("code", "cancelled"), "message": "See the adapter runbook before retrying."}] if code else [])
    emit_payload(result, output_format=args.format, stream=sys.stdout)
    if args.format == "text" and result.get("schema_version") == "tool_result.v1":
        for key, value in result["data"].items():
            print(f"{key}: {json.dumps(value) if isinstance(value, (dict, list)) else value}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
