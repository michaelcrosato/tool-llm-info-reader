# LLM Usage Reader

Small local CLI for recording and summarizing LLM usage evidence.

It is built around the main point from `suggestions-20260618.md`: do not ask the LLM to report benchmark facts. This tool records local timing itself, imports structured provider usage/cost exports, and labels manual values as manual.

## What It Can Track

- Start time, finish time, duration, provider, model, exit code, and host details for local runs.
- The agent/tool that produced a run (for example `claude-desktop`, `grok-cli`, `codex`, or `antigravity`) and a best-effort shell name, captured under `host.client` and `host.shell`.
- Real per-message token usage parsed directly from local Claude Code session transcripts (`import-claude-code`), recorded as `vendor_session_store` adapter evidence.
- Imported OpenAI organization completions usage buckets, including model name when the export was grouped by model.
- Imported OpenAI organization cost buckets.
- Imported Anthropic organization Cost Report buckets (the per-bucket amounts, reported in cents, are converted to USD).
- Imported Anthropic organization Messages Usage Report buckets, including model name when the export was grouped by model.
- Direct OpenAI Admin API usage/cost fetches for a requested period when `OPENAI_ADMIN_KEY` is available.
- Idempotent OpenAI imports that skip previously imported bucket rows.
- Conflict detection for corrected OpenAI bucket rows so repeated imports do not double-count changed token or cost values.
- Fail-closed OpenAI imports for malformed or unsupported usage/cost export fields.
- Manual token/cost entries, clearly marked as `manual_attestation`.
- Period summaries by provider and model.
- Continuous polling of an inbox directory for provider export JSON files.
- A 1-shot benchmarking layer (`oneshot`) that runs a defined prompt against a real model, grades the answer with a deterministic, program-owned check, and records the run with machine-observed latency and provider-reported usage — so the efficiency numbers are trustworthy and the pass/fail is objective, not self-reported.

Actual per-period billing is only as good as the source. For OpenAI, use the organization Usage and Costs API/dashboard export JSON as the evidence source. Token totals and actual billing are stored separately because cached tokens, service tiers, subscriptions, and credits can make "tokens consumed" different from "actual charged cost".

## Install

The tool is a single dependency-free module, so you can run it directly from a checkout:

```powershell
python .\llm_usage_reader.py --help
```

Or install it to get the `llm-usage-reader` console command on your PATH:

```powershell
pip install .
llm-usage-reader --help
llm-usage-reader --version
```

Installing requires Python 3.10 or newer. The examples below use `python .\llm_usage_reader.py`; once installed, `llm-usage-reader` is equivalent.

## Quick Start

Show help:

```powershell
python .\llm_usage_reader.py --help
python .\llm_usage_reader.py --version
```

Record one manual run:

```powershell
python .\llm_usage_reader.py record `
  --provider openai `
  --model gpt-5.4 `
  --started-at 2026-06-18T20:00:00Z `
  --finished-at 2026-06-18T20:05:00Z `
  --input-tokens 1200 `
  --output-tokens 300 `
  --billed-tokens 1500 `
  --source manual_attestation
```

Start and finish a run:

```powershell
python .\llm_usage_reader.py start --provider openai --model gpt-5.4
python .\llm_usage_reader.py finish --run-id run_xxxxxxxxxxxxxxxx --input-tokens 1200 --output-tokens 300
```

Wrap any command to capture machine-observed start/finish/duration:

```powershell
python .\llm_usage_reader.py wrap --provider local --model unknown -- python --version
```

`provider` and `model` are free-form, so local runs from any vendor are first-class — for example `--provider anthropic --model claude-opus-4-8`, `--provider xai --model grok-4`, `--provider google --model gemini-3-pro`, or `--provider openai --model gpt-5.4`. (Structured imports below cover OpenAI and Anthropic organization exports plus local Claude Code session transcripts.)

Record which agent/tool produced a run with `--client` on `start`, `finish`, `record`, and `wrap`:

```powershell
python .\llm_usage_reader.py record --provider anthropic --model claude-opus-4-8 --client claude-desktop `
  --started-at 2026-06-18T20:00:00Z --finished-at 2026-06-18T20:05:00Z --input-tokens 1200 --output-tokens 300
```

Instead of passing `--client` every time, a harness can set it once via the `LLM_USAGE_CLIENT` environment variable (the `--client` flag wins when both are present). The shell is auto-detected best-effort from `SHELL` (bash, zsh, Git Bash) or from PowerShell 6+; native Windows shells cannot be told apart reliably from the environment, so set `LLM_USAGE_SHELL` to record the shell explicitly (the operating system is always captured under `host.os`). For a `start`/`finish` pair, the client captured at `start` carries through to the finished record unless `finish` supplies its own; the precedence at finish is the `--client` flag, then an `LLM_USAGE_CLIENT` value present at finish time, then the start client.

Import OpenAI organization completions usage and costs JSON responses:

```powershell
python .\llm_usage_reader.py import-openai-usage --file .\samples\openai_usage_response.json
python .\llm_usage_reader.py import-openai-costs --file .\samples\openai_costs_response.json
```

Use the matching import command for the export family; a cost-only export passed to `import-openai-usage`, or a usage-only export passed to `import-openai-costs`, is rejected rather than treated as an empty import.
`import-openai-usage` supports only `organization.usage.completions.result` rows. Every other `organization.usage.*` result family — such as image, audio, vector store, or file-search usage — is rejected. This is an intentional scope limitation to completions usage, not a temporary gap awaiting schema fields (the ledger already defines audio token fields, for example).

Import Anthropic organization Cost Report and Messages Usage Report JSON responses/exports:

```powershell
python .\llm_usage_reader.py import-anthropic-costs --file .\samples\anthropic_costs_response.json
python .\llm_usage_reader.py import-anthropic-usage --file .\samples\anthropic_usage_response.json
```

Anthropic reports cost amounts in the lowest currency unit (cents); they are converted to USD when stored. For usage, Anthropic reports input token categories as disjoint counts (uncached, cache-read, and cache-creation); these are summed into the ledger's `usage.input_tokens`, with `usage.cached_input_tokens` set to the cache-read portion. The per-category breakdown is preserved in the retained raw export. Imports are idempotent and conflict-checked the same way as OpenAI imports.
Paginated OpenAI API pages must be complete before import. A page that still reports `has_more: true`, or a final page that still carries `next_page`, is rejected to avoid recording partial usage or cost evidence.

Import token usage from local Claude Code session transcripts:

```powershell
# A single session transcript
python .\llm_usage_reader.py import-claude-code --file $env:USERPROFILE\.claude\projects\<project>\<session>.jsonl

# Every transcript under a Claude Code projects directory (scanned recursively)
python .\llm_usage_reader.py import-claude-code --projects-dir $env:USERPROFILE\.claude\projects
```

This is the first local-agent *adapter*: rather than an organization export, it reads the transcript JSONL that Claude Code writes locally and records each assistant API message's token usage as `vendor_session_store` evidence (`source.adapter = claude-code`). Token categories follow the Anthropic convention — `usage.input_tokens` is the total input (plain + cache-read + cache-creation) and `usage.cached_input_tokens` is the cache-read subset. Claude Code writes one transcript line per assistant content block, and each line repeats the same message-level usage, so each API message (`message.id`) is counted exactly once. Imports are idempotent (re-importing a growing session only appends genuinely new messages), and records carry `host.client = claude-code`. Transcripts do not contain billed cost, so `billing` is recorded as `unavailable`; use the Anthropic Cost Report import or `fetch-anthropic` for actual charges. Lines that are not assistant usage — and a partially written trailing line for an in-progress session — are skipped, while malformed token counts are rejected.

Fetch OpenAI organization usage and costs directly when an admin key is present:

```powershell
$env:OPENAI_ADMIN_KEY = "sk-admin-..."
python .\llm_usage_reader.py fetch-openai --from 2026-06-18 --to 2026-06-19
```

`fetch-openai` saves the raw OpenAI responses under `data/openai-exports` and then imports those saved files through the same validation path as manual exports. Usage is grouped by model by default so period summaries include model names when OpenAI returns them.

Fetch Anthropic organization usage and costs directly when an Admin API key is present:

```powershell
$env:ANTHROPIC_ADMIN_KEY = "sk-ant-admin-..."
python .\llm_usage_reader.py fetch-anthropic --from 2026-06-18 --to 2026-06-19
```

`fetch-anthropic` calls the Anthropic Admin Usage and Cost Report endpoints (authenticating with the `x-api-key` header), saves the raw responses under `data/anthropic-exports`, and imports them through the same validation path. The cost report is always daily; `--bucket-width` applies to the usage report only.

Summarize a period:

```powershell
python .\llm_usage_reader.py summary --from 2026-06-18 --to 2026-06-19
python .\llm_usage_reader.py summary --last 24h --json
```

Verify the whole ledger and print a health summary:

```powershell
python .\llm_usage_reader.py verify
python .\llm_usage_reader.py verify --json
```

`verify` reads every record through the same validation path as the other commands, so it re-checks each `record_hash` and re-verifies provider-export source files against their recorded hashes. It exits `0` when the ledger is intact and prints a summary (record counts by source type, kind, provider, and status; the covered period; and provider-export/manual/trusted counts). It exits non-zero with an `error:` message on the first integrity problem, so it can be used as a scripted or CI integrity gate.

Summaries include only records fully contained in the requested period. Records that only partially overlap the period are skipped and reported separately, because their token and billing totals cannot be safely attributed to the smaller window.

Run a 24/7-style local collector that imports any new JSON exports copied into `data/inbox`:

```powershell
python .\llm_usage_reader.py watch --inbox .\data\inbox --interval 300
```

The watcher recognizes OpenAI usage/cost exports and Anthropic Cost Report and Messages Usage Report exports; unrecognized files are left in place and skipped.

Print raw ledger records as JSON for inspection or piping into other tools:

```powershell
python .\llm_usage_reader.py show
python .\llm_usage_reader.py show --limit 50
python .\llm_usage_reader.py show --limit 0
```

`show` prints the most recent records (oldest first within the window), defaulting to the last 10 (`--limit 10`). A larger `--limit` prints that many of the most recent records, and `--limit 0` prints the entire ledger. The output is the same JSON object stored on each ledger line, so it round-trips and can be piped into `jq` or another tool.

## 1-Shot Benchmarking

The same principle that governs usage collection — *the model never authors its own telemetry* — extends naturally to benchmarking. A **1-shot** is a single-prompt task paired with a deterministic **grader**. The tool sends the prompt to a model, the grader (not the model) decides pass or fail, and the run is recorded with machine-observed latency and provider-reported token usage. The result is a reproducible benchmark whose efficiency numbers you can trust and whose pass/fail is objective.

The library ships with a curated, growing set of 1-shots across common professional situations — extraction, coding (graded by executing the model's code against hidden cases), reasoning/math, classification, instruction-following, and format constraints. Each one teaches by example: it carries a known-good answer and an explanation of what it tests and why.

Browse and learn from the library:

```powershell
python .\llm_usage_reader.py oneshot list
python .\llm_usage_reader.py oneshot list --category coding
python .\llm_usage_reader.py oneshot show code-is-palindrome
```

`oneshot show` prints the prompt, the grader, a good example answer, and why the task matters — so you can understand quickly what a model is being asked to do and what "good" looks like.

Run one 1-shot against a model. Without any API key you can still **playtest** the whole flow with the offline `sim` adapter, which replays the reference answer (clearly labelled simulated and never benchmark-eligible):

```powershell
# Offline demo — no key required; shows what a passing answer looks like
python .\llm_usage_reader.py oneshot run math-multiply --adapter sim

# Against a real model via an authenticated local agent CLI
python .\llm_usage_reader.py oneshot run math-multiply --adapter claude-code
python .\llm_usage_reader.py oneshot run code-is-palindrome --adapter codex
```

Benchmark many 1-shots across one or more model families and print an efficiency comparison for professionals — pass rate, average score, latency, tokens, and cost:

```powershell
python .\llm_usage_reader.py oneshot bench --adapter claude-code,codex --category coding
python .\llm_usage_reader.py oneshot bench --adapter claude-code --all --json
```

### Adapters

An adapter is the channel used to reach a model. Each records whatever trustworthy telemetry that channel exposes; it never fabricates usage.

| Adapter | Family | Channel | Needs |
| --- | --- | --- | --- |
| `sim` | — | Offline replay of the reference answer (for playtesting) | nothing |
| `claude-code` | Anthropic | the `claude` CLI in print mode (`claude -p --output-format json`) | an authenticated `claude` CLI |
| `codex` | OpenAI | the `codex` CLI (`codex exec --json`) | an authenticated `codex` CLI |
| `gemini` | Google | the `gemini` CLI | `GEMINI_API_KEY` / `gemini` auth |
| `llm` | various | Simon Willison's `llm` CLI | `llm` with a configured key |
| `anthropic-api` | Anthropic | direct Messages API call | `ANTHROPIC_API_KEY` |
| `openai-api` | OpenAI | direct Chat Completions call | `OPENAI_API_KEY` |

Pick a model with `--model` (e.g. `--adapter anthropic-api --model claude-opus-4-8`); each adapter falls back to its own default when you omit it.

### What gets recorded

Each run is a normal `run` ledger record, so it flows through `summary`, `show`, and `verify` like any other, plus a `benchmark` object (covered by `record_hash`, so it cannot be altered without detection):

- `passed` / `score` from the deterministic grader, and `detail` explaining the verdict. A pass is a `completed` run with exit code `0`; a failure is a `failed` run with exit code `1`.
- `latency_ms` measured by the recorder with a monotonic clock.
- `evidence_level` — `native_telemetry` for a real agent-CLI run, `provider_reconciled` for a direct API call, or `simulated`/`unavailable` otherwise.
- `benchmark_eligible` — only real, measured runs are eligible; simulated and usage-less runs are explicitly excluded from trustworthy comparisons.
- `reported_cost_usd` (the channel's own cost figure when it provides one, e.g. Claude Code's `total_cost_usd`) is kept separate from the authoritative `billing` block, which stays `unavailable` for runs. `estimated_cost_usd` is a clearly-labelled estimate from a dated pricing snapshot, never treated as actual billing.

Observed token usage and any cost figure are recorded separately, and agent-CLI adapters include the agent's own harness overhead in their token counts — useful when measuring the real cost of *using that tool*, but not a like-for-like comparison of raw model economics (use a direct `*-api` adapter for that). Raw evidence bundles are saved under `data/oneshot-evidence/` for audit.

## Storage

The default data directory is `data`.

- `data/usage-ledger.jsonl`: append-only local ledger.
- `data/usage-ledger.lock`: process lock used while appending and deduplicating imports.
- `data/runs/*.json`: in-progress/completed run state from `start`/`finish`.
- `data/imported-files.json`: file hashes already imported by `watch`; malformed state is rejected.
- `data/imported-files.lock`: process lock used while updating watcher import state.
- `data/inbox`: optional directory for continuously imported provider export files.
- `data/openai-exports`: saved raw responses from `fetch-openai`; repeated fetches for the same period use suffixed filenames instead of overwriting prior evidence.
- `data/oneshot-evidence/*.json`: raw evidence bundles from `oneshot run`/`oneshot bench` (prompt, response, grade, raw adapter output) saved for human audit.

Provider export records are verified against their recorded source file when the ledger is read. Keep imported JSON exports available, or use `fetch-openai` so the tool saves raw OpenAI evidence under `data/openai-exports`.

Each ledger record includes:

- `started_at`
- `finished_at`
- `duration_ms`
- `provider`
- `model`
- `usage.input_tokens`
- `usage.output_tokens`
- `usage.cached_input_tokens`
- `usage.tokens_consumed`
- `usage.billed_tokens`
- `usage.unavailable_reason`
- `billing.actual_cost_usd`
- `source.type`
- `record_hash`
- `benchmark` (only on 1-shot runs): the grader verdict (`passed`, `score`, `detail`), `latency_ms`, `evidence_level`, `benchmark_eligible`, and separate `reported_cost_usd` / `estimated_cost_usd`

Each record also carries a `host` object describing where it ran: machine and OS details, plus a best-effort `host.shell` and an optional `host.client` naming the agent/tool that produced the run. These are covered by `record_hash`, so they cannot be altered without detection.

## Recommended Workflow

1. Use provider exports or API responses for trusted token/cost history.
2. Use `wrap`, `start`, and `finish` for local run boundaries and basic metadata.
3. Use manual token/cost values only when no structured source exists.
4. Use `summary --trusted-only` when comparing evidence-backed records.

## Current Boundary

Direct OpenAI collection requires an admin key with organization usage/cost permissions in `OPENAI_ADMIN_KEY` or another environment variable named with `--api-key-env`. Without that key, use exported JSON files or the inbox watcher.

## License

Released under the [MIT License](LICENSE).
