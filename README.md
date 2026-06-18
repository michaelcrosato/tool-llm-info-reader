# LLM Usage Reader

Small local CLI for recording and summarizing LLM usage evidence.

It is built around the main point from `suggestions-20260618.md`: do not ask the LLM to report benchmark facts. This tool records local timing itself, imports structured provider usage/cost exports, and labels manual values as manual.

## What It Can Track

- Start time, finish time, duration, provider, model, exit code, and host details for local runs.
- Imported OpenAI organization usage buckets, including model name when the export was grouped by model.
- Imported OpenAI organization cost buckets.
- Idempotent OpenAI imports that skip previously imported bucket rows.
- Manual token/cost entries, clearly marked as `manual_attestation`.
- Period summaries by provider and model.
- Continuous polling of an inbox directory for provider export JSON files.

Actual per-period billing is only as good as the source. For OpenAI, use the organization Usage and Costs API/dashboard export JSON as the evidence source. Token totals and actual billing are stored separately because cached tokens, service tiers, subscriptions, and credits can make "tokens consumed" different from "actual charged cost".

## Quick Start

Show help:

```powershell
python .\llm_usage_reader.py --help
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

Import OpenAI organization usage and costs JSON responses:

```powershell
python .\llm_usage_reader.py import-openai-usage --file .\samples\openai_usage_response.json
python .\llm_usage_reader.py import-openai-costs --file .\samples\openai_costs_response.json
```

Summarize a period:

```powershell
python .\llm_usage_reader.py summary --from 2026-06-18 --to 2026-06-19
python .\llm_usage_reader.py summary --last 24h --json
```

Run a 24/7-style local collector that imports any new JSON exports copied into `data/inbox`:

```powershell
python .\llm_usage_reader.py watch --inbox .\data\inbox --interval 300
```

## Storage

The default data directory is `data`.

- `data/usage-ledger.jsonl`: append-only local ledger.
- `data/runs/*.json`: in-progress/completed run state from `start`/`finish`.
- `data/imported-files.json`: file hashes already imported by `watch`.
- `data/inbox`: optional directory for continuously imported provider export files.

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
- `billing.actual_cost_usd`
- `source.type`
- `record_hash`

## Recommended Workflow

1. Use provider exports or API responses for trusted token/cost history.
2. Use `wrap`, `start`, and `finish` for local run boundaries and basic metadata.
3. Use manual token/cost values only when no structured source exists.
4. Use `summary --trusted-only` when comparing evidence-backed records.

## Current Boundary

This version does not call the OpenAI API directly because no OpenAI Admin key is configured in this workspace. It imports JSON responses/exports instead. A live OpenAI collector can be added once a key with organization usage/cost permissions is approved.
