"""Agent-facing source integration commands for independently owned workspaces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence, TextIO

from repo_platform.tooling.tool_handshake import (
    ToolSelector,
    ToolVerb,
    argparse_command_details,
    emit_payload,
    tool_result_payload,
    tool_usage_payload,
)


TOOL_ID = "tool.concurrent_development"
ENTRYPOINT = "python scripts/tools/concurrent_development.py"
GLOBAL_OPTIONS = {"--root", "--state-root", "--remote", "--shared-branch", "--format"}
INCOMPLETE_STATUSES = {"stale", "not_published", "not_exposed", "uncertain"}


class _UsageError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=ENTRYPOINT, description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", default=".", help="Exact owning Git workspace; defaults to the current directory.")
    parser.add_argument("--state-root", help="Explicit local integration-state directory; default is the core owner's managed local state.")
    parser.add_argument("--remote", default="origin", help="Configured remote name, default origin. Must match the binding on every command.")
    parser.add_argument("--shared-branch", default="main", help="Shared branch name, default main. Must match the binding on every command; no implicit default-branch detection.")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format; works before or after the verb.")
    verbs = parser.add_subparsers(dest="verb")

    verbs.add_parser("usage", help="Describe commands without inspecting Git, writing state, or using the network.", allow_abbrev=False)
    status = verbs.add_parser("status", help="Read one exact workspace binding and its candidate state.", allow_abbrev=False)
    status.add_argument("--binding-id", required=True, help="Exact locally registered binding identity.")

    relocate = verbs.add_parser("relocate-common-dir", help="Preview or reconcile binding paths after a same-volume main-root rename; never move source or change refs.", allow_abbrev=False)
    relocate.add_argument("--from-common-dir", required=True, help="Exact absolute original main-workspace .git directory from the pre-move preview.")
    relocate.add_argument("--to-common-dir", required=True, help="Exact absolute destination main-workspace .git directory; roots must not overlap.")
    relocate.add_argument("--dry-run", action="store_true", help="Read-only preview: no locks, local writes, Git mutation or network access.")
    relocate.add_argument("--expected-state", help="Exact pre-move preview digest; required for mutation and checked against bindings, retained refs and physical directory identity.")
    relocate.add_argument("--outcome", choices=("finish", "rollback"), default="finish", help="Finish the binding transition at the destination, or reverse an interrupted transition after physically returning and repairing Git at the source.")

    bind = verbs.add_parser("bind", help="Register an owned workspace without taking over its external, native or Runner lifecycle.", allow_abbrev=False)
    bind.add_argument("--binding-id", required=True, help="Stable identity for this exact workspace binding.")
    bind.add_argument("--owner-id", required=True, help="Actual task, person or worker attempt identity; never a title or latest-task guess.")
    bind.add_argument("--base", required=True, help="Exact incorporated shared commit identity.")
    bind.add_argument("--lifecycle-owner", choices=("external_workspace", "codex_native", "runner"), default="external_workspace", help="Owner responsible for workspace creation and removal. external_workspace accepts an existing user-owned clone or worktree on any supported platform.")
    bind.add_argument("--owned-path", action="append", default=[], help="Repository-relative path explicitly owned by this workspace; repeat for multiple paths.")

    release_binding = verbs.add_parser("release-binding", help="Release an empty integration binding after verified lifecycle handoff; leave the workspace to its external, native or Runner owner.", allow_abbrev=False)
    release_binding.add_argument("--binding-id", required=True, help="Exact locally registered binding identity.")
    release_binding.add_argument("--owner-id", required=True, help="Exact lifecycle owner identity originally supplied to bind.")

    prepare = verbs.add_parser("prepare-integration", help="Construct an immutable combined candidate while preserving the working source and index.", allow_abbrev=False)
    prepare.add_argument("--binding-id", required=True, help="Exact workspace binding.")
    selection = prepare.add_mutually_exclusive_group(required=True)
    selection.add_argument("--path", action="append", default=[], help="Repository-relative source path ready for integration; repeat. Selects the complete change at that path.")
    selection.add_argument("--ready-tree", help="Exact Git tree representing the deliberately selected ready changes relative to the binding base; permits hunk-level selection instead of whole paths.")
    prepare.add_argument("--message", required=True, help="Commit message describing the selected ready contribution.")
    prepare.add_argument("--expected-generation", type=_nonnegative_int, required=True, help="Exact binding generation from status; stale generations are rejected.")
    prepare.add_argument("--resolved-candidate-tree", help="Exact manually resolved combined candidate tree after examining the reported conflict.")
    prepare.add_argument("--resolved-residual-tree", help="Exact manually resolved residual tree preserving unfinished source after candidate combination.")
    prepare.add_argument("--resolved-carry-tree", help="Exact resolved complete tree carrying newer unfinished edits onto an earlier resolved candidate/residual after a shared-ref race.")
    prepare.add_argument("--expected-shared", help="Exact shared commit this preparation/resolution expects; an advanced shared branch is rejected before accepting a supplied resolution.")
    prepare.add_argument("--expected-base", help="Exact incorporated binding base used by an explicit resolution; required together with --expected-shared and --ready-tree for every preparation resolution.")
    prepare.add_argument("--expected-source-tree", help="Exact complete local source tree used by a carry/residual resolution; required for those resolutions and rejected if unfinished source changed.")

    expose = verbs.add_parser("expose-candidate", help="Expose exact candidate source for owned remote qualification without moving the shared branch.", allow_abbrev=False)
    _candidate_arguments(expose)
    expose.add_argument("--expected-remote", help="Exact expected old candidate-ref commit; omit only when the remote candidate ref must be absent.")

    publish = verbs.add_parser("publish", help="Publish an already checked current candidate only against the exact expected shared revision.", allow_abbrev=False)
    _candidate_arguments(publish)
    publish.add_argument("--expected-shared", required=True, help="Exact expected shared-branch commit from the prepared candidate; publication cannot rewrite shared history.")

    refresh = verbs.add_parser("refresh", help="Incorporate an exact shared revision at a safe boundary, preserving unfinished changes.", allow_abbrev=False)
    refresh.add_argument("--binding-id", required=True, help="Exact workspace binding.")
    refresh.add_argument("--target", required=True, help="Exact desired shared commit identity; no guessed latest ref.")
    refresh.add_argument("--expected-generation", type=_nonnegative_int, required=True, help="Exact current binding generation from status.")
    refresh.add_argument("--resolved-source-tree", help="Exact deliberately resolved complete working-source tree after an actual refresh conflict.")
    refresh.add_argument("--resolved-index-tree", help="Exact deliberately resolved index tree preserving the intended staged selection after an actual index conflict.")
    refresh.add_argument("--resolved-head-tree", help="Exact deliberately resolved tree combining local commits with shared history after an actual HEAD conflict.")
    refresh.add_argument("--expected-base", help="Exact incorporated base reported by the conflict; required for every explicit refresh resolution.")
    refresh.add_argument("--expected-source-tree", help="Exact complete working-source input reported by the conflict; required for an explicit source resolution.")
    refresh.add_argument("--expected-head", help="Exact local HEAD reported by the conflict; required for a HEAD or index resolution.")
    refresh.add_argument("--expected-index-tree", help="Exact staged tree reported by the conflict; required for an index resolution.")

    reconcile = verbs.add_parser("reconcile-refresh", help="Finish or roll back one exact interrupted source refresh, preserving newer edits and foreign locks.", allow_abbrev=False)
    reconcile.add_argument("--binding-id", required=True, help="Exact integration binding with an interrupted refresh.")
    reconcile.add_argument("--outcome", choices=("finish", "rollback"), required=True, help="Choose the prepared combined result or restore the exact pre-operation source, index and HEAD.")

    close = verbs.add_parser("close", help="Close one candidate operation when its actual consumers have finished.", allow_abbrev=False)
    _candidate_arguments(close)
    retract = verbs.add_parser("retract-candidate", help="Withdraw one candidate's readiness so it cannot be published.", allow_abbrev=False)
    _candidate_arguments(retract)
    for verb, description in (
        ("retain-candidate", "Register an actual qualification job that still needs this candidate."),
        ("release-candidate", "Release the exact finished qualification job's candidate hold."),
    ):
        command = verbs.add_parser(verb, help=description, allow_abbrev=False)
        _candidate_arguments(command)
        command.add_argument("--job-id", required=True, help="Exact consuming qualification job identity; release only after the consumer has finished.")
    return parser


def _candidate_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--binding-id", required=True, help="Exact locally registered workspace binding.")
    parser.add_argument("--candidate-id", required=True, help="Immutable candidate identity returned by prepare-integration.")


def usage_payload() -> dict[str, Any]:
    verbs = [
        ToolVerb("usage", "none", "Return complete command information without loading workspace state."),
        ToolVerb("status", "local read only", "Inspect one exact binding, generation, and current candidate."),
        ToolVerb("relocate-common-dir", "local binding-path writes unless --dry-run; no source/ref/network changes", "Reconcile all exact common-directory bindings after an authorized same-volume main-root rename, retaining candidates and consumers."),
        ToolVerb("bind", "local integration-state write", "Bind the real owner and workspace to its exact incorporated base."),
        ToolVerb("release-binding", "local integration-state removal", "Remove an empty binding after its exact owner has completed the source handoff; retain workspace and ordinary Git history."),
        ToolVerb("prepare-integration", "fetch configured remote; write owned Git objects, candidate refs, and local operation state", "Combine explicitly selected ready source with the current shared branch; preparation is not a claim of assurance."),
        ToolVerb("expose-candidate", "network; update the fixed owned remote candidate ref", "Make immutable candidate source available for eligible remote qualification; the shared branch is unchanged."),
        ToolVerb("publish", "network; conditionally advance the configured remote shared branch; update owned local state", "Publish only the current candidate against its exact checked shared base."),
        ToolVerb("refresh", "read shared Git state; write only the bound workspace and its local state", "Incorporate a selected shared commit while preserving unfinished source and rejecting stale state."),
        ToolVerb("reconcile-refresh", "write only exact bound workspace and interrupted operation state", "Finish or undo an interrupted refresh only while affected source/index/HEAD still have its known identities."),
        ToolVerb("close", "remove owned candidate refs and operation state when unused", "End this integration operation, respecting qualification holds; never delete the workspace."),
        ToolVerb("retract-candidate", "local candidate-state write", "Withdraw readiness before further publication."),
        ToolVerb("retain-candidate", "local consumer-hold write", "Keep candidate resources available for one real consuming job."),
        ToolVerb("release-candidate", "local consumer-hold write", "Remove the exact completed job's hold; this does not cancel a running job."),
    ]
    return tool_usage_payload(
        tool_id=TOOL_ID,
        entrypoint=ENTRYPOINT,
        purpose="Prepare, expose, integrate, and refresh explicitly owned source changes against the configured remote shared branch without publishing unrelated or unfinished work.",
        safe_first_calls=[
            {"label": "usage", "cmd": ENTRYPOINT},
            {"label": "usage-json", "cmd": f"{ENTRYPOINT} usage --format json"},
        ],
        verbs=verbs,
        selectors=[
            ToolSelector("--root", False, "Exact owned workspace; defaults to the current directory. Global options work before or after the verb."),
            ToolSelector("--state-root", False, "Explicit integration-state directory; otherwise use the core owner's default local state."),
            ToolSelector("--remote", False, "Configured Git remote name, default origin; retained in the binding and checked on subsequent operations."),
            ToolSelector("--shared-branch", False, "Shared Git branch, default main; retained in the binding and checked on subsequent operations."),
            ToolSelector("--binding-id", True, "Required by individual binding operations; relocation instead selects exact common-directory paths."),
            ToolSelector("--owner-id", True, "Required by bind; actual task, person or worker identity obtained from its owner."),
            ToolSelector("--candidate-id", True, "Required by candidate operations; use the immutable ID returned by preparation."),
            ToolSelector("--path", False, "Repeatable whole-path ready selection for prepare-integration; mutually exclusive with --ready-tree."),
            ToolSelector("--ready-tree", False, "Explicit ready tree for hunk-level or other deliberate tree selection; never implies the entire dirty tree is ready."),
        ],
        output_formats=["text", "json"],
        side_effects={
            "writes_repo": True,
            "modifies_remote": True,
            "network_access": "Configured remote Git transport for fetching, candidate exposure, publication, and candidate-ref cleanup",
            "writes_paths": ["bound workspace only when refresh or integration finalization requires it", "owned Git object/ref state", "managed local integration state or --state-root"],
            "danger_level": "medium",
            "supports_dry_run": True,
            "preview_boundary": "status and relocate-common-dir --dry-run are read only; only relocation supports --dry-run. Preparation preserves source/index but may fetch and write Git/local state; exposure and publication are explicit remote mutations.",
            "workspace_lifecycle": "The external, native Codex or Runner owner retains workspace creation/removal ownership",
        },
        command_details=argparse_command_details(_parser(), semantics={
            "relocate-common-dir": {"prerequisites": ["Coordinate a quiet boundary for the selected repository and use this owner version for all cooperating writers.", "Preview before physical movement; resolve active refresh/ref operations and Git locks through their owners.", "For mutation, physically rename the main root on the same volume and repair surviving Git worktrees first."], "constraints": ["Changes only git_common_dir and workspace paths beneath the renamed primary root; absent historical workspaces remain absent.", "The original expected-state digest covers complete binding records, retained local refs and physical Git-directory identity.", "No source/index/ref mutation, remote access, workspace reconstruction or candidate/consumer release."], "retry": "Keep the exact paths and expected-state after failure. A pending marker fences affected cooperating mutations; dry-run reports partial progress. Retry finish at the repaired destination, or physically return and repair Git before rollback. Newer state is preserved and blocks reconciliation. After completed relocation, a later reversal needs a fresh reverse preview."},
            "bind": {"prerequisites": ["The exact workspace and owner identity are known.", "The base is the actual shared revision incorporated into this workspace."], "retry": "Read status after an interrupted bind; do not silently rebind another owner."},
            "release-binding": {"prerequisites": ["Candidate closeout and verified source handoff are complete.", "The exact workspace owner identity is known; the workspace still exists."], "retry": "Missing binding metadata after interruption means there is no binding left to release; workspace lifecycle remains with its owner."},
            "prepare-integration": {"prerequisites": ["Read the binding generation from status.", "Establish readiness of the explicitly selected source and inspect relevant secret/publication boundaries."], "constraints": ["Choose either one or more --path options or one --ready-tree.", "Every explicit resolution requires --ready-tree, --expected-base and --expected-shared from the reported conflict; carry/residual resolutions also require --expected-source-tree.", "Resolution trees are explicit conflict resolutions, not permission to discard residual source."], "retry": "On stale generation or changed shared/base/source state, recombine and resolve the current inputs; never relabel an old resolution with newer expected identities."},
            "expose-candidate": {"prerequisites": ["The selected candidate is current and source transfer is authorized.", "The exact expected remote candidate ref is known, or its absence is required."], "retry": "After uncertain transport, inspect remote/candidate state before attempting another exposure. Candidate availability does not authorize deployment."},
            "publish": {"prerequisites": ["The exact candidate is still the intended ready contribution.", "Material combined properties have been established using applicable evidence.", "The expected shared revision equals the candidate's checked base."], "retry": "A moved shared ref requires recombination and reconsideration of affected evidence. An uncertain outcome requires readback; never force-rewrite the shared branch."},
            "refresh": {"prerequisites": ["Use a safe boundary with no concurrent writer in this workspace.", "Retain unfinished source and inspect any reported conflicts before retrying."], "constraints": ["Explicit resolutions require --expected-base; source resolution also requires --expected-source-tree, HEAD resolution requires --expected-head, and index resolution requires --expected-head plus --expected-index-tree.", "Use the identities reported with the conflict; unrelated unchanged input checks are not required."], "retry": "Refresh status/generation after a stale-state rejection; resolve changed inputs again rather than relabeling an older resolution."},
            "reconcile-refresh": {"prerequisites": ["Read status to identify the exact interrupted operation.", "Ensure no concurrent writer or active Git operation owns this workspace."], "retry": "Newer source, modes, staging, HEAD or another process's index lock are preserved and block automatic reconciliation; inspect their owner before retrying."},
            "close": {"prerequisites": ["Actual candidate consumers have finished and their exact job holds are released.", "Any contribution still needed has reached its coordinator or ordinary source owner."], "retry": "Held candidates require consumer completion, not a forced cleanup; inspect status before retrying an uncertain close."},
            "retract-candidate": {"prerequisites": ["The named candidate is no longer ready for publication."], "retry": "Inspect status after an interrupted retraction; existing consumers still retain their lifecycle holds."},
            "retain-candidate": {"prerequisites": ["A real consuming job needs the exact immutable candidate."], "retry": "Reuse the same job identity; do not invent duplicate holds."},
            "release-candidate": {"prerequisites": ["The exact job has stopped using this candidate."], "retry": "Release only that job's hold; a stale process lease alone does not establish completion."},
        }),
        examples=[
            {"label": "Preview a main-root rename without writes", "cmd": f"{ENTRYPOINT} --root <old-main-root> relocate-common-dir --from-common-dir <old-main-root>/.git --to-common-dir <new-main-root>/.git --dry-run --format json"},
            {"label": "Inspect an owned binding", "cmd": f"{ENTRYPOINT} --root <workspace> status --binding-id <binding> --format json"},
            {"label": "Bind an existing task workspace", "cmd": f"{ENTRYPOINT} bind --root <workspace> --binding-id <binding> --owner-id <actual-task-id> --base <shared-sha> --owned-path <source-path>"},
            {"label": "Prepare selected complete file changes", "cmd": f"{ENTRYPOINT} prepare-integration --binding-id <binding> --path <source-path> --message \"Describe ready change\" --expected-generation <generation>"},
            {"label": "Prepare deliberate hunk selection", "cmd": f"{ENTRYPOINT} prepare-integration --binding-id <binding> --ready-tree <selected-tree-oid> --message \"Describe ready subset\" --expected-generation <generation>"},
            {"label": "Expose a candidate when its remote ref must not exist", "cmd": f"{ENTRYPOINT} expose-candidate --binding-id <binding> --candidate-id <candidate>"},
            {"label": "Publish an appropriately checked candidate", "cmd": f"{ENTRYPOINT} publish --binding-id <binding> --candidate-id <candidate> --expected-shared <checked-base-sha>"},
            {"label": "Refresh at a safe boundary", "cmd": f"{ENTRYPOINT} refresh --binding-id <binding> --target <shared-sha> --expected-generation <generation>"},
            {"label": "Close an unused candidate operation", "cmd": f"{ENTRYPOINT} close --binding-id <binding> --candidate-id <candidate>"},
        ],
        docs=["docs/ops/concurrent_development.md", "docs/instructions/agent_workflow/concurrent_development.md"],
        errors=[
            {"code": "invalid_arguments", "meaning": "A required selector, valid choice, or compatible option combination is missing; read the command details."},
            {"code": "core_error", "meaning": "Core errors preserve their precise code, message, and details, including stale identities, conflicts, held candidates, and uncertain transport; resolve the stated condition before retrying."},
            {"code": "io_error", "meaning": "A local file or process operation failed; inspect the stated failure before retrying a mutation."},
        ],
        extra={
            "shared_destination_defaults": {"remote": "origin", "ref": "refs/heads/main"},
            "candidate_namespaces": "Candidate refs use refs/heads/agent-framework/candidates/<binding-id>. Remote and shared branch are configurable but must match the binding and descriptor on every operation.",
            "assurance": "The agent owns semantic readiness and relevant verification; preparing or exposing a candidate establishes neither.",
            "source_selection": "Whole paths select their complete changes. --ready-tree represents a deliberately authored ready tree relative to the binding base and supports mixed readiness within a file. Unselected working source and the real index are preserved during preparation.",
            "excluded": ["backup or save/restore service", "recovery-ref rotation or archive retention", "workspace creation/removal", "automatic global workflow activation", "deployment authorization"],
            "exit_codes": {"0": "operation completed or help returned; a published candidate can still require an explicit workspace refresh", "1": "operation is stale, not published/exposed, or uncertain; inspect exact state before continuing", "2": "invalid arguments or core/IO operation error; inspect the structured error and current state"},
        },
    )


def _normalize_globals(raw: Sequence[str]) -> list[str]:
    """Move only declared global options before the verb for argparse."""
    globals_: list[str] = []
    remainder: list[str] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        name = token.split("=", 1)[0]
        if name in GLOBAL_OPTIONS:
            globals_.append(token)
            if "=" not in token:
                if index + 1 >= len(raw) or raw[index + 1].startswith("--"):
                    raise _UsageError(f"{name} requires a value")
                globals_.append(raw[index + 1])
                index += 1
        else:
            remainder.append(token)
        index += 1
    return globals_ + remainder


def _requested_format(raw: Sequence[str]) -> str:
    """Preserve requested JSON for argument failures as well as successful calls."""
    result = "text"
    for index, token in enumerate(raw):
        value = token.partition("=")[2] if token.startswith("--format=") else (
            raw[index + 1] if token == "--format" and index + 1 < len(raw) else None
        )
        if value in {"text", "json"}:
            result = value
    return result


def _emit(payload: dict[str, Any], output_format: str, *, stream: TextIO) -> None:
    emit_payload(payload, output_format=output_format, stream=stream)
    if output_format == "text" and payload.get("schema_version") == "tool_result.v1":
        # The shared compact result renderer omits data and retry metadata.
        # Keep identities, conflicts, and recovery actions available in text too.
        if payload.get("data"):
            stream.write("data:\n" + json.dumps(payload["data"], indent=2, sort_keys=True) + "\n")
        for error in payload.get("errors", []):
            if error.get("retry_hint"):
                stream.write(f"retry: {error['retry_hint']}\n")
            if error.get("details"):
                stream.write("details:\n" + json.dumps(error["details"], indent=2, sort_keys=True) + "\n")


def _dispatch(service: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.verb == "status":
        return service.status(args.binding_id)
    if args.verb == "relocate-common-dir":
        return service.relocate_common_dir(args.from_common_dir, args.to_common_dir,
                                           dry_run=args.dry_run, expected_state=args.expected_state,
                                           outcome=args.outcome)
    if args.verb == "bind":
        return service.bind(args.binding_id, args.owner_id, args.base, lifecycle_owner=args.lifecycle_owner, owned_paths=args.owned_path)
    if args.verb == "release-binding":
        return service.release_binding(args.binding_id, args.owner_id)
    if args.verb == "prepare-integration":
        return service.prepare(args.binding_id, args.path, args.message, args.expected_generation,
                               ready_tree=args.ready_tree,
                               resolved_candidate_tree=args.resolved_candidate_tree,
                               resolved_residual_tree=args.resolved_residual_tree,
                               resolved_carry_tree=args.resolved_carry_tree,
                               expected_shared=args.expected_shared, expected_base=args.expected_base,
                               expected_source_tree=args.expected_source_tree)
    if args.verb == "expose-candidate":
        return service.expose(args.binding_id, args.candidate_id, args.expected_remote)
    if args.verb == "publish":
        return service.publish(args.binding_id, args.candidate_id, args.expected_shared)
    if args.verb == "refresh":
        return service.refresh(args.binding_id, args.target, args.expected_generation,
                               resolved_source_tree=args.resolved_source_tree,
                               resolved_index_tree=args.resolved_index_tree,
                               resolved_head_tree=args.resolved_head_tree,
                               expected_base=args.expected_base, expected_source_tree=args.expected_source_tree,
                               expected_head=args.expected_head, expected_index_tree=args.expected_index_tree)
    if args.verb == "reconcile-refresh":
        return service.reconcile_refresh(args.binding_id, args.outcome)
    if args.verb == "close":
        return service.close(args.binding_id, args.candidate_id)
    if args.verb == "retract-candidate":
        return service.retract(args.binding_id, args.candidate_id)
    if args.verb == "retain-candidate":
        return service.retain(args.binding_id, args.candidate_id, args.job_id)
    if args.verb == "release-candidate":
        return service.release(args.binding_id, args.candidate_id, args.job_id)
    raise _UsageError(f"Unknown command: {args.verb}")


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    output_format = _requested_format(raw)
    verb = "usage"
    try:
        normalized = _normalize_globals(raw)
        index = 0
        while index < len(normalized) and normalized[index].split("=", 1)[0] in GLOBAL_OPTIONS:
            index += 1 if "=" in normalized[index] else 2
        if index < len(normalized) and not normalized[index].startswith("-"):
            verb = normalized[index]
        args = _parser().parse_args(normalized)
        output_format = args.format
        verb = args.verb or "usage"
        if verb == "usage":
            _emit(usage_payload(), output_format, stream=sys.stdout)
            return 0

        # Usage/help never constructs the state owner or inspects a repository.
        from .core import ConcurrentDevelopment, ConcurrentDevelopmentError

        try:
            service = ConcurrentDevelopment(Path(args.root), state_root=Path(args.state_root) if args.state_root else None,
                                            remote=args.remote, shared_branch=args.shared_branch)
            data = _dispatch(service, args)
        except ConcurrentDevelopmentError as error:
            payload = tool_result_payload(
                tool_id=TOOL_ID, verb=verb, status="error", summary=error.message,
                errors=[{"code": error.code, "message": error.message,
                         "details": error.details,
                         "retry_hint": "Inspect the reported identity/state or conflict, then status and the command's usage before retrying; do not force a stale operation."}],
            )
            _emit(payload, output_format, stream=sys.stdout if output_format == "json" else sys.stderr)
            return 2
        operation_status = data.get("status")
        incomplete = operation_status in INCOMPLETE_STATUSES
        summary = data.get("summary")
        if not summary:
            if incomplete:
                summary = f"{verb}: {operation_status}; inspect the returned exact state before retrying."
            elif data.get("refresh_required"):
                summary = f"{verb}: {operation_status or 'completed'}; the bound workspace still requires refresh at a safe boundary."
            else:
                summary = f"{verb} completed; inspect the returned exact identities and state."
        payload = tool_result_payload(
            tool_id=TOOL_ID, verb=verb, status="error" if incomplete else "ok", summary=str(summary), data=data,
            errors=[{"code": str(operation_status), "message": str(summary),
                     "retry_hint": "Read back exact shared/candidate state; recombine stale candidates and reconsider affected evidence before publishing."}] if incomplete else [],
        )
        _emit(payload, output_format, stream=sys.stdout)
        return 1 if incomplete else 0
    except (_UsageError, OSError) as error:
        payload = tool_result_payload(
            tool_id=TOOL_ID, verb=verb, status="error", summary=str(error),
            errors=[{"code": "invalid_arguments" if isinstance(error, _UsageError) else "io_error",
                     "message": str(error), "retry_hint": f"Read `{ENTRYPOINT} usage --format json`; inspect current state before retrying an interrupted mutation."}],
        )
        _emit(payload, output_format, stream=sys.stdout if output_format == "json" else sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
