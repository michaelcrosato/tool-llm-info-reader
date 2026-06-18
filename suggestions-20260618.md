# Model Council Synthesis

Seven distinct positions were retained. The repeated Supervisor Harness response and repeated evidence-graded telemetry response were collapsed rather than counted as independent votes. The packet’s inconsistent numbering was normalized as Model 1 through Model 7. This synthesis is based solely on the uploaded material. 

## 1. Where Models Agree

| Finding                                                                                                                                              | Supporting Models                        | Strength of Consensus | Evidence / Reasoning                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | --------------------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The LLM must never be the source of benchmark telemetry.                                                                                             | Models 1–7                               |             Unanimous | Every model identifies self-reported model names, tokens, timing, platform versions, and environment data as unreliable or fabricated.                        |
| A process outside the coding agent should own measurement.                                                                                           | Models 1–7                               |             Unanimous | Proposed forms include a supervisor harness, parent runner, start/finish CLI, shell wrapper, dashboard endpoint, or external recorder.                        |
| Start time, end time, duration, OS information, and tool version should be machine-observed.                                                         | Models 1–7                               |                Strong | The models consistently recommend operating-system clocks, system probes, process monitoring, and executable metadata rather than generated text.             |
| Token and model data should come from structured artifacts.                                                                                          | Models 1–7                               |             Unanimous | Candidate sources include native OpenTelemetry, local JSONL or SQLite records, API responses, provider records, proxy logs, and version-specific adapters.    |
| A hybrid collector architecture is necessary.                                                                                                        | Models 1–7                               |                Strong | No proposed source covers every agent and platform. Each design ultimately requires a priority order and tool-specific fallbacks.                             |
| A proxy is useful but should not be the universal default.                                                                                           | Models 3–7; Models 1–2 indirectly        |                Strong | Proxies can provide strong API-level usage data but do not cover closed subscription tools, proprietary routing, or agents that cannot change their base URL. |
| Existing manifest-writing and locking logic may be retained, but it should consume recorder-generated data rather than user- or LLM-supplied values. | Models 1–7                               |                Strong | The common architectural distinction is between safe file writing and trustworthy measurement. The current writer can become a finalizer or importer.         |
| Missing telemetry should remain unavailable rather than being silently invented.                                                                     | Models 2, 5, 7; partially Models 1 and 6 |     Partial consensus | The most evidence-conscious models require explicit provenance and confidence classes. Other models permit manual fallback but still reject LLM invention.    |
| Observed token usage and actual billing should be represented separately.                                                                            | Models 2, 5, 7; partially Model 6        |     Partial consensus | Several models note that caches, reasoning tokens, subscription plans, credits, and pricing tables prevent a single token total from proving actual cost.     |
| Community parsers can accelerate implementation but require version pinning and validation.                                                          | Models 1, 2, 5, 7                        |     Partial consensus | TokenTelemetry, Tokscale, ccusage, and similar projects are proposed as useful adapters, not uniformly reliable billing authorities.                          |

## 2. Where Models Disagree

| Topic                    | Position A                                                                             | Supporting Models                                          | Position B / Alternative                                                                                                                 | Supporting Models                                                     | Why They Differ                                                                                                                                           |
| ------------------------ | -------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Best MVP                 | Install a community tracker and bridge its output into the repository.                 | Models 1 and 2; Models 5 and 7 support this as a bootstrap | Build or extend a repository-native wrapper immediately.                                                                                 | Models 3, 4, and 6; Models 5 and 7 support this as the long-term core | The tradeoff is implementation speed versus control over session boundaries, provenance, and failure behavior.                                            |
| Initial third-party tool | TokenTelemetry is the fastest drop-in option.                                          | Model 1                                                    | Tokscale is the stronger bootstrap, particularly for reported Antigravity coverage.                                                      | Models 2 and 7                                                        | The recommendations rely on different unverified claims about current platform coverage and parser quality.                                               |
| Primary token source     | Prefer native telemetry or structured local session records.                           | Models 1, 2, 3, 5, and 7                                   | Prefer API interception or provider billing records.                                                                                     | Models 4 and 6; Model 3 presents this as an alternative               | Native telemetry is better for reproducing an end-user coding tool, while API or billing data is potentially more authoritative for direct-API economics. |
| Definition of duration   | Measure parent-process start to agent exit or session finish.                          | Models 3, 4, 5, and 6                                      | Record several boundaries and use prompt submission to verified goal completion as the benchmark duration.                               | Model 7; Model 2 partially                                            | The simpler definition measures tool runtime. The more rigorous definition measures time to a successfully verified outcome.                              |
| Human fallback           | Allow a human to paste or attest token totals when parsing fails.                      | Models 2, 3, and 6                                         | Store `null` or mark the value as manual and exclude it from trusted benchmarks.                                                         | Models 5 and 7                                                        | The disagreement is usability versus benchmark integrity.                                                                                                 |
| Meaning of “accurate”    | Logs, proxies, or billing records can provide “100% accurate” or “absolute truth.”     | Models 1, 3, 4, and 6                                      | Accuracy must be qualified by source because observed usage, estimated cost, and provider-reconciled billing are different measurements. | Models 5 and 7                                                        | The first group uses “accuracy” broadly. The second distinguishes technical observation from billing authority and session attribution.                   |
| Schema scope             | Continue using a compact manifest with model, tool, tokens, duration, and environment. | Models 1, 3, 4, and 6                                      | Introduce a schema with field-level provenance, evidence classes, hashes, billing mode, multiple models, and completion authority.       | Models 2, 5, and 7                                                    | The compact design is faster to deploy; the larger schema supports public or defensible benchmarking.                                                     |
| Evidence storage         | Write the normalized attempt directly into the project manifest.                       | Models 1–6                                                 | Store raw evidence outside the agent-writable worktree and append only a summary and hash.                                               | Model 7                                                               | Model 7 is optimizing for tamper resistance, while the other proposals mainly optimize for convenience and repository integration.                        |
| Role of the proxy        | Make a proxy the seamless long-term collection layer.                                  | Model 4; Model 6 gives it high priority                    | Use it only for controlled API or BYOK benchmark mode.                                                                                   | Models 5 and 7                                                        | Proxying may alter the product, authentication path, latency, subscription economics, or routing being benchmarked.                                       |

## 3. Unique Discoveries

| Model   | Unique Finding                                                                                                                           | Why It Matters                                                                                                                                    | Confidence |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ---------: |
| Model 1 | Proposed using a local TokenTelemetry API as a bridge and augmenting its output with OS build data before appending to `oneshot.json`.   | It is a narrowly scoped migration path that avoids initially rewriting parsers.                                                                   | Medium-low |
| Model 1 | Suggested Agentlytics’ SQLite cache as a searchable normalized history layer rather than only a telemetry dashboard.                     | This could support later analytics and cross-agent session search, although it is not necessary for the recorder MVP.                             |        Low |
| Model 2 | Proposed explicit start/complete commands with UUID isolation, raw snapshots, checksums, partial-run handling, and dashboard CSV export. | It connects collection to downstream teaching and economic-analysis workflows.                                                                    |     Medium |
| Model 4 | Asserted that the existing `record-attempt.js` already parses session logs, follows an append-only schema, and is fully tested.          | This could significantly reduce implementation work if confirmed, but the packet contains no repository evidence supporting the assertion.        |        Low |
| Model 6 | Proposed a pragmatic `session-input.json` workflow using machine timestamps followed by delayed human entry from a billing dashboard.    | This provides coverage for closed tools that expose neither telemetry nor configurable API routing. Such records should remain manually attested. | Medium-low |
| Model 7 | Recommended marking historical attempts as `legacy_self_reported` and `benchmarkEligible: false`.                                        | This prevents old, unverifiable records from contaminating new trusted comparisons without deleting historical data.                              |       High |
| Model 7 | Distinguished prompt submission, first model request, agent exit, verified goal completion, and run end.                                 | This resolves ambiguity around “duration” and supports both operational and outcome-based analysis.                                               |       High |
| Model 7 | Required verification, rather than an agent saying “done,” to determine successful completion.                                           | It links telemetry to an objective result rather than merely to process termination.                                                              |       High |
| Model 7 | Added support for mixed-model runs, separate host and execution environments, resumed-session deltas, and concurrent-session refusal.    | These cases materially affect attribution and are absent from the simpler proposals.                                                              |       High |
| Model 7 | Recommended version-gated adapters that fail closed when an upstream format changes.                                                     | This prevents generic parsers from silently producing plausible but incorrect measurements.                                                       |       High |

## 4. Access / Evidence Quality Check

No model demonstrated enough direct evidence for a true code review. The packet contains model-written descriptions and links, but not the actual repository files, command output, test results, telemetry samples, screenshots, or retrieved documentation contents.

Models 4 and 6 make the clearest unsupported access claims. Model 4 describes `record-attempt.js` as already implemented, schema-conformant, and test-passed. Model 6 states that it read the full repository. Neither provides excerpts, diffs, test output, or citations sufficient to establish that access. Model 3 also makes repository-specific assumptions and supplies schematic code rather than an implementation validated against the project.

Models 1 and 2 make numerous current claims about repository activity, supported tools, install commands, API endpoints, release timing, platform limitations, and project statistics without source citations. Those claims should be treated as leads for verification, not implementation facts.

Models 5 and 7 have the strongest evidence bases in the packet because they cite official documentation and repository sources, distinguish official telemetry from community parsers, and qualify uncertainty. Model 7 is the strongest overall: it separates observed tokens from billing, identifies attribution failure modes, and avoids claiming that one collector is universally authoritative. Its repository-specific findings still require confirmation against an actual checkout.

The following claims are especially low-confidence until checked:

* Current TokenTelemetry, Tokscale, ccusage, Agentlytics, and Antigravity support matrices.
* Exact install commands, API ports, database paths, and log locations.
* Star counts, commit counts, release dates, and operating-system support.
* Specific telemetry environment variables and emitted attributes.
* Claims that any local parser or proxy provides universal “100% accuracy.”
* Claims about the present behavior or test status of `record-attempt.js`.
* Availability and granularity of provider usage or billing endpoints.

The two repeated responses were not counted as separate corroboration. Counting them independently would have overstated support for the Supervisor Harness and evidence-graded telemetry positions.

## 5. Comprehensive Analysis

The decisive consensus is architectural: telemetry must be measured outside the coding agent. The coding agent can produce code and request completion, but it cannot author benchmark facts. A trusted parent process should control timing, inspect the environment, collect structured usage evidence, run verification, and finalize the attempt record.

The most important disagreement is not really “wrapper versus telemetry versus proxy.” Those are different layers:

1. The **recorder** defines the run, timing, environment, completion, and evidence policy.
2. A **collector adapter** obtains model and usage data from native telemetry, session records, or a third-party parser.
3. A **provider reconciliation layer** can later establish actual direct-API cost.
4. The **manifest finalizer** validates and appends the normalized summary.

Treating one community dashboard or one proxy as the whole architecture would couple benchmark integrity to an external tool’s current coverage and undocumented formats.

The tiebreaker is therefore to adopt Model 7’s external-recorder architecture, Model 5’s source/confidence discipline, and Models 1–2’s suggestion to bootstrap rather than immediately reimplement every parser. A pinned Tokscale adapter is the most plausible first community integration from the packet because two models specifically report Antigravity support, but that claim must be verified before it becomes a dependency. Native OpenTelemetry should take precedence wherever an agent officially emits sufficient fields.

The current manifest writer should be retained only for its schema validation, locking, and atomic append behavior, assuming those features exist after repository inspection. It should become a finalizer that accepts a recorder-generated run bundle. Generic searches for fields named `tokens`, `time`, or `model` should not be used for trusted records.

The initial implementation should capture:

* Prompt hash, recorder version, tool executable and version, Git starting state, and host/execution environment.
* Monotonic duration plus UTC display timestamps.
* Prompt submission, verified goal completion, agent exit, and run end as separate events.
* Field-level source and evidence class.
* Raw evidence and hashes.
* Observed usage separately from billing.
* Multiple models where telemetry reports model switching.
* `null` and an explicit reason when attribution is unsafe.
* Verification status and completion authority.
* A legacy classification for existing self-reported attempts.

A proxy should be added later as a controlled API benchmark mode. It is valuable when direct API economics are the subject of the benchmark, but it should not silently replace the economics or routing of subscription products.

## 6. Final Recommendation

**Recommended path:** Build a repository-native external recorder in Node or TypeScript. It should own session boundaries, environment capture, verification, evidence storage, and finalization. Use official native telemetry as the first usage source, a pinned and version-gated Tokscale adapter as the initial broad fallback after verifying its actual support, and provider reconciliation only for controlled direct-API runs. Convert the existing manifest writer into a validator/finalizer, preserve legacy records as untrusted, and introduce field-level provenance.

**Use this model/tool:** A custom Node/TypeScript recorder with native OpenTelemetry collectors and a pinned Tokscale adapter; use Codex CLI as the repository implementation agent.

**Run it here:** A terminal-based repo agent with the actual `agy-1shots` checkout, its package manager, schema files, and complete test suite available.

**Do not use:** Browser chat as the telemetry source, LLM-authored measurement fields, a proxy-only architecture, unversioned community parsers, generic regex extraction for trusted attempts, or manually entered metrics in benchmark-eligible records.

**Reason:** This path separates stable benchmark semantics from unstable telemetry formats. It provides immediate coverage through an adapter while preserving a trustworthy recorder core, explicit uncertainty, reproducible completion criteria, and a later path to provider-reconciled economics.

## 7. Priority-Ranked Action Items

1. **P0**: Inspect the actual repository and add characterization tests for `AGENTS.md`, the attempt schema, `record-attempt.js`, locking behavior, manifest paths, and dashboard ingestion before modifying anything.
2. **P0**: Remove all instructions and trusted code paths that permit the LLM to supply model, tokens, duration, platform version, or environment data. Mark existing self-reported attempts as legacy and non-benchmark-eligible.
3. **P1**: Implement `oneshot run`, `oneshot attempt start`, and `oneshot attempt finish` around a recorder-owned run directory, monotonic timing, environment probes, prompt/Git hashes, verification, evidence hashes, and schema-v2 finalization.
4. **P1**: Add a collector interface with official telemetry adapters first and a pinned Tokscale adapter second. Require supported-version declarations, fixture tests, deduplication, resumed-session deltas, and fail-closed behavior.
5. **P2**: Add dashboard evidence badges and filtering, followed by an optional provider-reconciled BYOK/API benchmark mode that stores actual cost separately from observed token usage.

## 8. Reusable Prompt / Artifact

### Coding-agent implementation brief


Implement a trusted attempt-recording pipeline for the agy-1shots repository.

Normalized source map for this brief:
1 = TokenTelemetry-first proposal
2 = Tokscale-first proposal
3 = Supervisor Harness proposal
4 = Existing session-log scraper proposal
5 = Evidence-graded native telemetry proposal
6 = Billing/manual hybrid proposal
7 = Trusted external recorder proposal

OBJECTIVE

Replace LLM-authored benchmark metadata with an external recorder that creates evidence-backed attempt records. The coding agent may implement code and request completion, but it must never provide trusted model, token, timing, platform-version, OS, or billing values. (Sources: 1, 2, 3, 4, 5, 6, 7)

FIRST STEP

Inspect the actual repository before editing. Report the current oneshot schema, AGENTS.md instructions, record-attempt.js behavior, dashboard ingestion path, package scripts, and relevant tests. Do not assume the council’s repo-specific claims are correct. (Sources: 4, 6, 7)

NON-NEGOTIABLE INVARIANTS

- Start/end timestamps and duration must be produced by the recorder process, not by the LLM. Use a monotonic clock for calculated durations and UTC timestamps for display. (Sources: 1, 2, 3, 4, 5, 6, 7)
- OS, OS build, architecture, runtime, executable path, and tool version must come from system probes or executable metadata. (Sources: 1, 2, 3, 5, 7)
- Model and token data must come from official telemetry, provider/API responses, structured local session data, or a version-gated collector. (Sources: 1, 2, 3, 4, 5, 7)
- Missing or unsafe-to-attribute values must be null with an unavailableReason. Never substitute zero, a guess, or LLM-generated text. (Sources: 2, 5, 7)
- Manually entered values must be labeled manual_attestation and excluded from benchmark-eligible comparisons by default. (Sources: 3, 5, 6, 7)
- Observed usage and actual billing must be separate objects. Do not treat a token total as proof of actual cost. (Sources: 2, 5, 7)
- Each trusted field must identify its source or evidence class. (Sources: 2, 5, 7)
- The recorder must fail closed when an adapter encounters an unsupported tool version or unknown data format. (Source: 7)
- The agent saying “done” is not completion evidence. Run the one-shot’s verification command and record its exit status. (Source: 7)

TARGET WORKFLOW

Support these commands or equivalent repository-native commands:

oneshot attempt start --id <one-shot-id> --tool <tool>
oneshot attempt finish --id <one-shot-id> --attempt <run-id>
oneshot run <one-shot-id> --agent <adapter> -- <agent-command>

The parent recorder must:
1. Create a unique run ID.
2. Capture preflight evidence.
3. Record prompt submission.
4. Start the selected telemetry collector.
5. Launch or observe the coding agent.
6. Run acceptance verification when completion is requested.
7. Finalize usage collection.
8. Validate the normalized record.
9. Append only the normalized summary to oneshot.json.
10. Retain raw evidence separately with hashes. (Sources: 2, 3, 5, 7)

TIMING

Record distinct fields when available:

- processStartedAt
- promptSubmittedAt
- firstModelRequestAt
- goalCompletedAt
- agentExitedAt
- runEndedAt
- durationToGoalMs
- totalRunDurationMs

Use promptSubmittedAt to goalCompletedAt as the primary outcome duration. Preserve agent exit and total run time separately. (Source: 7)

COLLECTOR PRIORITY

Use this priority order:

1. Provider-reconciled usage or cost, when the run uses an isolated direct-API project or key.
2. Official native telemetry emitted by the coding client.
3. Version-specific vendor session database or structured log.
4. Pinned third-party parser adapter.
5. Manual attestation.
6. Unavailable.

Do not claim that a lower layer has the authority of a higher layer. (Sources: 1, 2, 5, 7)

INITIAL ADAPTERS

- Add official native-telemetry collectors for tools that expose sufficient structured telemetry after confirming their current documentation.
- Evaluate Tokscale as the first pinned community adapter, especially for Antigravity, but verify its current support and output format before integrating it.
- Optionally use another parser only as a cross-check; do not silently merge disagreeing totals.
- Record adapter name, adapter version, upstream tool version, and evidence hash. (Sources: 1, 2, 5, 7)

SCHEMA REQUIREMENTS

Introduce a schema version that supports at least:

{
  "schemaVersion": 2,
  "runId": "run_...",
  "oneShotId": "...",
  "prompt": {
    "path": "...",
    "sha256": "..."
  },
  "timing": {
    "promptSubmittedAt": "...",
    "goalCompletedAt": "...",
    "agentExitedAt": "...",
    "runEndedAt": "...",
    "durationToGoalMs": 0,
    "clock": "monotonic"
  },
  "agentClient": {
    "name": "...",
    "version": "...",
    "executablePath": "...",
    "executableSha256": "..."
  },
  "models": [
    {
      "id": "...",
      "displayName": "...",
      "variant": "...",
      "tokensObserved": 0,
      "source": "native_telemetry"
    }
  ],
  "usage": {
    "totalTokensObserved": null,
    "source": "native_telemetry | vendor_session_store | third_party_adapter | manual_attestation | unavailable",
    "evidenceLevel": "provider_reconciled | native_telemetry | vendor_session_store | system_probe | manual_attestation | legacy_self_reported | unavailable",
    "unavailableReason": null
  },
  "billing": {
    "mode": "api | subscription | unknown",
    "actualCostUsd": null,
    "estimatedEquivalentCostUsd": null,
    "source": "provider_cost_api | pricing_snapshot | unavailable",
    "authoritative": false
  },
  "environment": {
    "host": {},
    "execution": {}
  },
  "completion": {
    "status": "passed | failed | incomplete",
    "authority": "program | human",
    "verificationExitCode": null,
    "verificationSha256": null
  },
  "git": {
    "beforeCommit": null,
    "afterCommit": null,
    "patchSha256": null
  },
  "evidence": {
    "recorderVersion": "...",
    "adapterVersion": "...",
    "bundleSha256": "..."
  },
  "benchmarkEligible": false
}

Support multiple models in one run and derive a display-only primary model without discarding the underlying model list. (Sources: 5, 7)

EVIDENCE STORAGE

Store raw run evidence in a recorder-controlled directory. Prefer a location outside the agent-writable project worktree. Append only the normalized summary and evidence hash to the one-shot manifest. (Source: 7)

LEGACY MIGRATION

Preserve existing attempts, but classify records that cannot be traced to machine evidence as:

{
  "evidenceLevel": "legacy_self_reported",
  "benchmarkEligible": false
}

Do not rewrite historical values as though they were measured. (Source: 7)

RECORD-ATTEMPT FINALIZER

Retain existing locking, atomic-write, and schema-validation behavior if repository inspection confirms it is sound. Convert record-attempt.js into a finalizer/importer that accepts only a recorder-generated and validated run record for trusted attempts. Put any manual-entry mode behind an explicitly untrusted command. (Sources: 4, 5, 7)

REQUIRED TESTS

Add fixtures and tests for:

- A successful single-model run.
- A failed acceptance test.
- Missing usage data.
- An unsupported adapter version.
- A changed or corrupted upstream log format.
- A resumed session where only the new usage delta counts.
- Concurrent sessions that cannot be safely attributed.
- Multiple models in one run.
- Cached and reasoning-token categories.
- A subscription run with observed tokens but no actual per-run bill.
- A direct-API run with later provider reconciliation.
- WSL or container execution with different host and execution environments.
- Legacy self-reported records.
- Atomic append and concurrent finalization.

A parser must return unavailable rather than guessing when a fixture is ambiguous. (Sources: 2, 5, 7)

DASHBOARD

Display evidence level, billing mode, completion authority, tool version, execution environment, adapter version, and benchmark eligibility. Allow comparisons to exclude manual, legacy, estimated, or unavailable records. (Sources: 2, 5, 7)

OUT OF SCOPE FOR THE FIRST MILESTONE

- Do not build a full observability platform.
- Do not make a proxy mandatory.
- Do not calculate authoritative cost from a pricing table.
- Do not trust model-generated JSON as telemetry.
- Do not claim universal support for any third-party tracker.
- Do not discard existing records.

DELIVERABLES

1. Repository assessment.
2. Schema migration.
3. Recorder CLI.
4. Finalizer integration.
5. At least one verified native collector.
6. One pinned fallback adapter after support verification.
7. Evidence storage and hashing.
8. Fixture-based tests.
9. Documentation removing all LLM self-report instructions.
10. Dashboard support for provenance and eligibility.

Run the complete repository test suite and include the exact commands and results in the implementation report.
