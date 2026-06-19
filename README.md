# LLM Usage Reader

Small local CLI for recording and summarizing LLM usage evidence.

It is built around the main point from `suggestions-20260618.md`: do not ask the LLM to report benchmark facts. This tool records local timing itself, imports structured provider usage/cost exports, and labels manual values as manual.

## What It Can Track

- Start time, finish time, duration, provider, model, exit code, and host details for local runs.
- The agent/tool that produced a run (for example `claude-desktop`, `grok-cli`, `codex`, or `antigravity`) and a best-effort shell name, captured under `host.client` and `host.shell`.
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

`provider` and `model` are free-form, so local runs from any vendor are first-class — for example `--provider anthropic --model claude-opus-4-8`, `--provider xai --model grok-4`, `--provider google --model gemini-3-pro`, or `--provider openai --model gpt-5.4`. (Structured provider imports below are currently OpenAI-only.)

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
`import-openai-usage` supports `organization.usage.completions.result` rows. Other OpenAI usage result families, such as image, audio, vector store, or file-search usage, are rejected until the ledger has fields for their native units.

Import Anthropic organization Cost Report and Messages Usage Report JSON responses/exports:

```powershell
python .\llm_usage_reader.py import-anthropic-costs --file .\samples\anthropic_costs_response.json
python .\llm_usage_reader.py import-anthropic-usage --file .\samples\anthropic_usage_response.json
```

Anthropic reports cost amounts in the lowest currency unit (cents); they are converted to USD when stored. For usage, Anthropic reports input token categories as disjoint counts (uncached, cache-read, and cache-creation); these are summed into the ledger's `usage.input_tokens`, with `usage.cached_input_tokens` set to the cache-read portion. The per-category breakdown is preserved in the retained raw export. Imports are idempotent and conflict-checked the same way as OpenAI imports.
Paginated OpenAI API pages must be complete before import. A page that still reports `has_more: true`, or a final page that still carries `next_page`, is rejected to avoid recording partial usage or cost evidence.

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

## Storage

The default data directory is `data`.

- `data/usage-ledger.jsonl`: append-only local ledger.
- `data/usage-ledger.lock`: process lock used while appending and deduplicating imports.
- `data/runs/*.json`: in-progress/completed run state from `start`/`finish`.
- `data/imported-files.json`: file hashes already imported by `watch`; malformed state is rejected.
- `data/imported-files.lock`: process lock used while updating watcher import state.
- `data/inbox`: optional directory for continuously imported provider export files.
- `data/openai-exports`: saved raw responses from `fetch-openai`; repeated fetches for the same period use suffixed filenames instead of overwriting prior evidence.

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

Each record also carries a `host` object describing where it ran: machine and OS details, plus a best-effort `host.shell` and an optional `host.client` naming the agent/tool that produced the run. These are covered by `record_hash`, so they cannot be altered without detection.

## Recommended Workflow

1. Use provider exports or API responses for trusted token/cost history.
2. Use `wrap`, `start`, and `finish` for local run boundaries and basic metadata.
3. Use manual token/cost values only when no structured source exists.
4. Use `summary --trusted-only` when comparing evidence-backed records.

## Current Boundary

Direct OpenAI collection requires an admin key with organization usage/cost permissions in `OPENAI_ADMIN_KEY` or another environment variable named with `--api-key-env`. Without that key, use exported JSON files or the inbox watcher.
