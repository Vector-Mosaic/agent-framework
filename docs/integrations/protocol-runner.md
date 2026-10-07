# Protocol Runner integration

[Protocol Runner](https://github.com/Vector-Mosaic/protocol-runner) — **Swarm + Structure** —
is an optional, separately maintained tool for explicit agent work plans. The
framework remains useful without it. Use Runner when ordered or repeated work
benefits from durable execution state and selective recovery; ordinary small
tasks do not need a run.

This integration selects commit
`f2041eb8b60171a3d847e86e4f2b6b43427cc9ec`. It does not vendor Runner's source,
create a service during framework setup, or reuse an existing backend implicitly.
Review a later Runner version before changing that selection.

## Install separately

Use an ordinary separate checkout and Runner's supported Node.js/pnpm versions:

```sh
git clone https://github.com/Vector-Mosaic/protocol-runner.git
cd protocol-runner
git checkout --detach f2041eb8b60171a3d847e86e4f2b6b43427cc9ec
pnpm install --frozen-lockfile
pnpm build
pnpm demo
```

`pnpm demo` uses the real API, store and executor with simulated workers. It
demonstrates selective recovery; it does not call a model or establish the
quality of a real agent's analysis. Follow the pinned
[README](https://github.com/Vector-Mosaic/protocol-runner/blob/f2041eb8b60171a3d847e86e4f2b6b43427cc9ec/README.md)
and [execution guide](https://github.com/Vector-Mosaic/protocol-runner/blob/f2041eb8b60171a3d847e86e4f2b6b43427cc9ec/docs/execution.md).

## A small framework assignment

The included [work plan](../../examples/protocol-runner/work_plan.json) and
[contract](../../examples/protocol-runner/contract.md) ask two independent workers
to examine fictional engineering decisions. Each uses the relevant framework
owner, states a conclusion and explains a meaningful limitation. This is a
read-only source assignment with sealed output; it does not administer accounts,
change this repository or accept a release.

Real execution uses your installed, authenticated Codex CLI and consumes its
account usage. Select this framework checkout as the explicit worker workspace.
From the separate Runner checkout:

```sh
pnpm start -- --live --workspace "/absolute/path/to/agent-framework"
```

On Windows, use the corresponding absolute Windows path. The command starts a
foreground stack; Ctrl+C stops that stack's workers and services. It does not
stop another Runner instance. If the ports are occupied, select free ports through
Runner's documented environment settings rather than stopping the other service.

Use the CLI from the same selected Runner checkout and explicitly pass the API
URL printed by that startup. The following URL and run ID are examples; confirm
the actual API ownership and choose a new ID for every run:

```sh
python scripts/tools/protocol_runner.py usage --format json
python scripts/tools/protocol_runner.py doctor --base-url http://127.0.0.1:14831 --format json
python scripts/tools/protocol_runner.py create --base-url http://127.0.0.1:14831 --work-plan "/absolute/path/to/agent-framework/examples/protocol-runner/work_plan.json" --run-instance-id framework-example-001
python scripts/tools/protocol_runner.py bind --base-url http://127.0.0.1:14831 --run-instance-id framework-example-001 --binding-kind parallel_only
python scripts/tools/protocol_runner.py validate --base-url http://127.0.0.1:14831 --run-instance-id framework-example-001
python scripts/tools/protocol_runner.py start --base-url http://127.0.0.1:14831 --run-instance-id framework-example-001
python scripts/tools/protocol_runner.py diagnose --base-url http://127.0.0.1:14831 --run-instance-id framework-example-001
```

The contract and input paths are relative to the chosen **framework workspace**,
not the CLI checkout. Output is kept under that workspace's ignored
`.protocol-runner/` directory. Read the actual outputs and retained attempts;
`completed` means the declared procedural conditions were met, not that the
conclusions are correct. Preserve useful results before explicit Runner closeout.

Workers still receive the applicable Codex home and repository instructions.
Select a dedicated Codex home when deliberate configuration separation is needed
and authenticate it normally. Do not copy an existing home's credentials. A
different workspace alone does not isolate global instructions or same-user files.

## Platform and ownership

The integrated API/executor path is portable and uses `codex exec`. Runner's
historical Desktop/Discord adapter is an optional Windows integration in its own
repository. This framework integration does not depend on it or claim to port
Windows UI automation to Linux.

The coordinator chooses the backend, plan, runtime, workspace and lifecycle.
Workers carry out their assigned contract. Neither a worker nor the integration
guide grants permission to launch other runs, change accounts or publish source.
The [qualification record](../qualification.md) names the checks performed for
this integration and does not inherit every historical Runner claim as a new test.
