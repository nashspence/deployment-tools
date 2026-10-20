# Developer guide

Read [README.md](README.md) for an introduction to deployment-tools.

The README explains deployment-tools’ intended system boundaries. This guide explains how to work inside them, for human contributors and automated agents alike. It states repository-wide work rules and points to the sources that own detailed behavior.

The current implementation is a reference scaffold. Declared operations, reference models, and installed-package checks do not qualify native deployment or recovery. [Release policy](release.toml) owns that boundary.

## Start from the source that owns the answer

Before editing, read the relevant implementation, public contracts, and scoped policy tests. Enforceable repository policy belongs in clearly named tests.

Keep hand-maintained documentation focused on context that cannot be recovered easily from executable sources. Release reference belongs with the release it describes, not as a separate reference manual on `main`. [Licensing](LICENSE) keeps its own role.

Generated reports and audit findings describe declared contracts; they do not create new public promises. Do not introduce a separate registry to repeat rules already owned by contracts or tests.

Change `README.md` or `AGENTS.md` only to correct material drift or when the maintainer explicitly directs broader work.

## Keep work tracking outside the repository

Repository files describe the current system, not the work queue or the history of retired designs.

Do not store work-tracking information in source, tests, fixtures, configuration, or documentation. That includes TODO or FIXME reminders, progress checklists, future-work plans, issue or pull-request references, and descriptions of obsolete architecture. Explain current design reasons where useful, not the history of getting there.

GitHub issue bodies own scope, decisions, and acceptance. Preserve existing bodies; record later decisions in comments. Use native sub-issues, dependencies, and milestones rather than copying their state into parent checklists or title prefixes. Checklists cover only acceptance steps owned by that issue.

Additional work after completion belongs in a linked follow-up issue, not a reopened or rewritten completed issue.

## Put each change with its owner

Products, applications, and components integrate through public contracts, not imports of one another’s implementation code. Reusable packages must not import those implementations. Put shared behavior in a focused package.

Keep core behavior separate from how requests arrive, results are presented, and outside systems are called. Applications own their workflows and state. Components selected independently retain separate identities and authority even when they share packaging.

Keep interpretation with its owner, too. Generic code should not acquire file-format or provider-specific rules that belong to a component. Public code stays generic; provider selection, private deployment identity, and deployment layout belong with their owners.

The [component policy tests](tests/test_component_policy.py) and [component inventory](policy/components.toml) define the precise ownership and dependency rules.

## Preserve custody and independent recovery

Act only on the exact inputs and within the current permissions granted to the operation. A known identity or an earlier authorization does not replace the checks required by its contract.

Preserve confirmed outcomes across retries. A timeout does not prove that work failed, and a cancellation request does not prove execution has stopped. Release data or execution resources only when the owning contract says it is safe.

Local owners retain authority over their accepted work. Optional reporting, discovery, and client state must not become authorization or recovery prerequisites. The [contracts](packages/contracts/) and [owner-state tests](tests/test_engineering.py) define the maintained reference behavior.

Never expose secrets or private deployment identity in public code, fixtures, logs, examples, or generated contracts.

## Bound the work, not the collection

Large collections should not require processing everything at once. *Bounded work* handles a limited portion at a time; a page of results does not limit the total number of results. Long operations progress through bounded steps that can resume after interruption.

Do not introduce a total limit unless the owning contract declares it. Resource limits may explicitly reject, delay, or slow work, but must not silently omit data. Limits that depend on the deployment and affect accepted work belong in operator configuration.

The operation-owned declarations, [engineering projection](scripts/engineering.py), and [engineering tests](tests/test_engineering.py) identify the maintained bounds and extent proofs.

## Preserve declared growth bounds

Limiting the size of each step does not guarantee that the total work stays manageable.

A *growth bound* describes how an operation’s work increases as its input grows. Costs must be declared and justified by the operation’s owner. Keep reference-model scan costs explicit; do not present them as bounded native implementations.

Reason about these bounds and test them with the maintained implementation. Include difficult inputs, resource limits being reached, interruptions, and retries. Do not hide repeated whole-resource work behind batching, concurrency, or caches.

Violating a declared growth bound is a validation failure. Machine-dependent timing targets remain performance observations, not product guarantees. Neither permits incomplete results, weaker custody or authorization, or hidden total-size limits.

## Make clean pre-v1 changes

Before the first v1 release, make *hard cuts*: replace the previous model completely rather than supporting old and new forms together. For example, a renamed field should not leave its old spelling accepted.

Update affected code, contracts, tests, examples, documentation, and dependent components together. Do not preserve obsolete interfaces, aliases, old readers or writers, parallel formats, or upgrade paths for abandoned pre-v1 designs. If a dependency cannot change with the rest, report the blocker rather than retaining the obsolete design.

A hard cut does not permit deleting durable user data or changing protected commitments. Review contract changes explicitly. Regenerating contracts or updating expected test results does not authorize changing a [protected state boundary](tests/test_component_policy.py). Keep [maintained interfaces consistent](policy/interfaces.json) with the same operation.

## Change durable state deliberately

Each durable database belongs to one owner. The [state inventory](state/inventory.toml) identifies the owner, schema authority, and packaged Alembic history.

Keep the existing process and tests for upgrading databases between supported releases. Those tests must prove that existing data survives and interrupted upgrades can be handled safely.

New databases require explicit initialization. Store open checks identity and schema; it does not silently upgrade or erase existing data. Adopt and upgrade existing state explicitly through its owner’s migration history.

Preserved fixtures and introduced migration revisions are immutable. Transitions are forward-only; do not add downgrades or discard accepted work to make a schema change easier. The [component policy](scripts/project_policy.py) and [database qualification tests](tests/test_migrations.py) define the precise behavior.

## Measure performance without creating gates

A performance target is a nonbinding goal for measured speed or elapsed time. Unlike a declared growth bound, it is not a contract. Missing a target does not fail validation.

Use `make profile` as the shared entry point for maintained profiling scenarios. Add new scenarios there rather than creating separate developer-facing commands. Their implementations remain with their owners.

Report verified work, elapsed time, resource use, and measurement conditions. Compare only against matching measured references. An unavailable comparison is neither a pass nor a failure. Invalid evidence and failed execution remain errors.

The [measurement rules](scripts/run_stats.py), [profiling tests](tests/test_performance_measurement.py), and [maintained targets](policy/performance.toml) define the precise behavior.

## Account for validation evidence

Checks record their results and timing as they run, without repeating work just to measure it. The [validation runner](scripts/validation.py) records source identity, selection, results, and exit status without replacing contracts owned by individual operations.

Normal pytest runs must emit elapsed and CPU time, memory measurements, test phases, and fixture timings, with explicit measurement scopes and I/O availability. Missing or invalid required collection fails validation; numerical performance misses do not.

The [operation coverage map](scripts/operation_qualification.py) and [engineering projection](scripts/engineering.py) require actual passing proofs from a complete, clean, current-source unit receipt. Focused, dirty, stale, or skipped results cannot substitute for those proofs. Native operations remain unqualified until their own evidence exists.

Keep correctness, growth-contract conformance, and elapsed-time observations separate. Correctness failures, growth violations, and invalid required evidence fail validation. A valid timing miss does not.

Report elapsed time even when no numerical target or comparable reference exists. Use credible performance evidence to recommend investigation or improvement. The stronger the evidence and the more serious its likely consequences, the stronger the recommendation may be.

Record significant findings and recommendations in GitHub. Performance targets do not create approval-to-proceed conditions or authorize weakening required tests, workloads, or safety limits.

## Validate the change you actually deliver

Use the versions of tools and libraries fixed by the repository rather than relying on whatever happens to be installed locally. The [mise configuration](mise.toml), [workspace dependencies](pyproject.toml), and [uv lock](uv.lock) own those versions; ship only the runtime content the component needs.

From a trusted checkout, bootstrap the pinned tools, workspace dependencies, and local unit-test commit hook:

```bash
mise trust --yes
make setup
```

Run focused tests while working. Cover relevant failure, interruption, and retry paths as well as success. New components must enter the existing validation gates, not sit outside them. Check built and installed software where packaging, platform behavior, or handoffs are affected. A declared operation is not evidence that it ran correctly.

Before pushing, run:

```bash
make check
```

If Python source changed, also run `make compile`. Use `make database-qualification` for state changes, `make dist-smoke` for packaging, platform, or handoff changes, and `make audit` for dependency changes. Use `make help` for current command guidance.

Use `TESTS` for focused test paths or node IDs, `FILES` for Ruff/format paths, and `args` for tool flags. For example, `make unit TESTS=tests/test_engineering.py args='-k replay'` is an iteration run; the commit hook always runs the full unit suite. Do not bypass it.

When operation bindings or engineering declarations change, review their sources before explicitly regenerating their maps with `make operation-qualification args=generate` or `make engineering args=generate`. A generated map is not execution evidence.

After pushing, watch that exact commit’s required GitHub Actions checks through completion. Fix failures before handing work back. A skipped or blocked check is not a pass; report the gap rather than claiming complete validation. Do not remove required coverage or weaken a check merely to make a result green.

Include correctness results, required growth evidence, and timing observations in the handoff. Record significant performance concerns as evidence-backed recommendations, not validation failures or approval conditions.

## Keep authorization separate from validation

[Release governance](release.toml) and the [workflows](.github/workflows/) define branch, qualification, and publication requirements. Changes to `main` go through pull requests and gated squash auto-merge. Do not push directly to `main`, use administrator bypasses, or weaken required checks.

The repository is public; issue and pull-request creation are limited to collaborators. Keep private deployment identity and configuration outside it.

Successful validation supplies evidence; it does not authorize native operations or release publication. Native release publication remains disabled by release policy. Publication work requires separate authorization, the protected publication environment, and a complete governance check. Published release identities must remain immutable.
