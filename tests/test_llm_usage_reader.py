import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import llm_usage_reader as tool


class LlmUsageReaderTests(unittest.TestCase):
    def run_cli(self, data_dir: Path, *args: str) -> int:
        return tool.main(["--data-dir", str(data_dir), *args])

    def test_record_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
                "--output-tokens",
                "25",
                "--billed-tokens",
                "125",
            )
            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["usage"]["tokens_consumed"], 125)
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()
            summary = tool.summarize_records(
                records,
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )
            self.assertEqual(summary["totals"]["tokens_consumed"], 125)
            self.assertEqual(summary["totals"]["billed_tokens"], 125)

    def test_summary_marks_unknown_requests_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "100",
                    "--output-tokens",
                    "25",
                ),
                0,
            )
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                tool.read_ledger(data_dir),
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )

            self.assertIsNone(summary["rows"][0]["requests"])
            self.assertEqual(summary["rows"][0]["requests_known_records"], 0)
            self.assertIsNone(summary["totals"]["requests"])
            self.assertEqual(summary["totals"]["requests_known_records"], 0)

    def test_summary_marks_unknown_token_components_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--requests",
                    "1",
                ),
                0,
            )
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                tool.read_ledger(data_dir),
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )

            for row in (summary["rows"][0], summary["totals"]):
                self.assertIsNone(row["input_tokens"])
                self.assertEqual(row["input_tokens_known_records"], 0)
                self.assertIsNone(row["output_tokens"])
                self.assertEqual(row["output_tokens_known_records"], 0)
                self.assertIsNone(row["cached_input_tokens"])
                self.assertEqual(row["cached_input_tokens_known_records"], 0)
                self.assertIsNone(row["input_audio_tokens"])
                self.assertEqual(row["input_audio_tokens_known_records"], 0)
                self.assertIsNone(row["output_audio_tokens"])
                self.assertEqual(row["output_audio_tokens_known_records"], 0)

    def test_summary_preserves_known_zero_token_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "0",
                    "--output-tokens",
                    "0",
                    "--cached-input-tokens",
                    "0",
                    "--input-audio-tokens",
                    "0",
                    "--output-audio-tokens",
                    "0",
                ),
                0,
            )
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                tool.read_ledger(data_dir),
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )

            for row in (summary["rows"][0], summary["totals"]):
                self.assertEqual(row["input_tokens"], 0)
                self.assertEqual(row["input_tokens_known_records"], 1)
                self.assertEqual(row["output_tokens"], 0)
                self.assertEqual(row["output_tokens_known_records"], 1)
                self.assertEqual(row["cached_input_tokens"], 0)
                self.assertEqual(row["cached_input_tokens_known_records"], 1)
                self.assertEqual(row["input_audio_tokens"], 0)
                self.assertEqual(row["input_audio_tokens_known_records"], 1)
                self.assertEqual(row["output_audio_tokens"], 0)
                self.assertEqual(row["output_audio_tokens_known_records"], 1)

    def test_summary_preserves_known_zero_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--requests",
                    "0",
                ),
                0,
            )
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                tool.read_ledger(data_dir),
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )

            self.assertEqual(summary["rows"][0]["requests"], 0)
            self.assertEqual(summary["rows"][0]["requests_known_records"], 1)
            self.assertEqual(summary["totals"]["requests"], 0)
            self.assertEqual(summary["totals"]["requests_known_records"], 1)

    def test_summary_excludes_partial_overlap_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:10:00Z",
                "--input-tokens",
                "100",
                "--output-tokens",
                "50",
            )
            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                records,
                tool.parse_time("2026-06-18T20:05:00Z"),
                tool.parse_time("2026-06-18T20:06:00Z"),
                args,
            )

            self.assertEqual(summary["totals"]["records"], 0)
            self.assertIsNone(summary["totals"]["tokens_consumed"])
            self.assertEqual(summary["partial_overlap_skipped"], 1)

    def test_filtered_summary_ignores_partial_overlap_outside_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "anthropic",
                    "--model",
                    "claude",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:10:00Z",
                    "--input-tokens",
                    "100",
                ),
                0,
            )
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:05:00Z",
                    "--finished-at",
                    "2026-06-18T20:06:00Z",
                    "--input-tokens",
                    "10",
                ),
                0,
            )
            args = type(
                "Args",
                (),
                {
                    "provider": "openai",
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                tool.read_ledger(data_dir),
                tool.parse_time("2026-06-18T20:05:00Z"),
                tool.parse_time("2026-06-18T20:06:00Z"),
                args,
            )

            self.assertEqual(summary["totals"]["records"], 1)
            self.assertEqual(summary["totals"]["tokens_consumed"], 10)
            self.assertEqual(summary["partial_overlap_skipped"], 0)
            self.assertEqual(summary["skipped"], 1)

    def test_trusted_summary_excludes_manual_billing_on_trusted_usage_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"] = {
                "actual_cost_usd": "0.12",
                "currency": "usd",
                "source": "manual_attestation",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
            records = tool.read_ledger(data_dir)
            args = type(
                "Args",
                (),
                {
                    "provider": None,
                    "model": None,
                    "trusted_only": True,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                records,
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )

            self.assertEqual(summary["totals"]["records"], 1)
            self.assertEqual(summary["totals"]["tokens_consumed"], 15)
            self.assertIsNone(summary["totals"]["actual_cost_usd"])
            self.assertEqual(summary["totals"]["actual_cost_known_records"], 0)

    def test_start_rejects_path_traversal_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            code = self.run_cli(data_dir, "start", "--run-id", "../escape")
            self.assertEqual(code, 2)
            self.assertFalse((root / "escape.json").exists())
            self.assertFalse((data_dir / "escape.json").exists())

    def test_start_rejects_empty_provider_without_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_empty_provider"

            code = self.run_cli(data_dir, "start", "--run-id", run_id, "--provider", "")

            self.assertEqual(code, 2)
            self.assertFalse((data_dir / "runs" / f"{run_id}.json").exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_start_rejects_duplicate_ledger_run_id_without_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_duplicate_start"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                ),
                0,
            )

            code = self.run_cli(
                data_dir,
                "start",
                "--run-id",
                run_id,
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T21:00:00Z",
            )

            self.assertEqual(code, 2)
            self.assertFalse((data_dir / "runs" / f"{run_id}.json").exists())
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["run_id"], run_id)

    def test_record_rejects_duplicate_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            run_id = "run_duplicate_record"
            args = [
                "record",
                "--run-id",
                run_id,
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            ]

            self.assertEqual(self.run_cli(data_dir, *args), 0)
            self.assertEqual(self.run_cli(data_dir, *args), 2)

            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["run_id"], run_id)

    def test_record_rejects_existing_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            run_id = "run_existing_state"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                ),
                0,
            )

            code = self.run_cli(
                data_dir,
                "record",
                "--run-id",
                run_id,
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_empty_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_empty_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_marks_nonzero_exit_code_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--exit-code",
                "1",
            )
            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(records[0]["status"], "failed")
            self.assertEqual(records[0]["exit_code"], 1)

    def test_record_rejects_completed_status_with_nonzero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--status",
                "completed",
                "--exit-code",
                "1",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_failed_status_with_zero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--status",
                "failed",
                "--exit-code",
                "0",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_strips_provider_for_summary_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                " openai ",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "10",
            )
            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(records[0]["provider"], "openai")
            args = type(
                "Args",
                (),
                {
                    "provider": "openai",
                    "model": None,
                    "trusted_only": False,
                    "data_dir": data_dir,
                },
            )()

            summary = tool.summarize_records(
                records,
                tool.parse_time("2026-06-18T00:00:00Z"),
                tool.parse_time("2026-06-19T00:00:00Z"),
                args,
            )

            self.assertEqual(summary["totals"]["records"], 1)
            self.assertEqual(summary["totals"]["tokens_consumed"], 10)

    def test_record_rejects_non_finite_manual_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--cost-usd",
                "NaN",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_unavailable_billing_source_with_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--cost-usd",
                "0.12",
                "--billing-source",
                "unavailable",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_billing_source_without_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--billing-source",
                "manual_attestation",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_pricing_estimate_as_actual_cost_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            script = Path(tool.__file__).resolve()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--data-dir",
                    str(data_dir),
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--cost-usd",
                    "0.12",
                    "--billing-source",
                    "pricing_estimate",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("invalid choice", proc.stderr)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_provider_billing_source_for_manual_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            script = Path(tool.__file__).resolve()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--data-dir",
                    str(data_dir),
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--cost-usd",
                    "0.12",
                    "--billing-source",
                    "provider_export",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("invalid choice", proc.stderr)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_provider_export_source_for_manual_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            script = Path(tool.__file__).resolve()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--data-dir",
                    str(data_dir),
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "100",
                    "--source",
                    "provider_export",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("invalid choice", proc.stderr)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_unavailable_source_with_usage_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
                "--source",
                "unavailable",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_unavailable_source_includes_unavailable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--source",
                "unavailable",
            )

            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(records[0]["source"]["type"], "unavailable")
            self.assertEqual(
                records[0]["usage"]["unavailable_reason"],
                "usage telemetry source was explicitly marked unavailable",
            )

    def test_record_manual_without_usage_includes_unavailable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(records[0]["source"]["type"], "manual_attestation")
            self.assertEqual(
                records[0]["usage"]["unavailable_reason"],
                "usage telemetry was not provided with this manual attestation",
            )

    def test_record_rejects_out_of_range_epoch_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "999999999999999999999999999999",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_record_rejects_append_to_invalid_existing_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            ledger = tool.ledger_path(data_dir)
            ledger.write_text("{not-json}\n", encoding="utf-8")

            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "10",
                "--output-tokens",
                "5",
            )

            self.assertEqual(code, 2)
            self.assertEqual(ledger.read_text(encoding="utf-8"), "{not-json}\n")

    def test_append_ledger_rejects_invalid_pending_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)

            with self.assertRaisesRegex(tool.CliError, "missing record_hash"):
                tool.append_ledger(data_dir, [{"bad": "record"}])

            self.assertFalse(tool.ledger_path(data_dir).exists())

    def test_summary_rejects_invalid_last_durations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            for duration in ["NaN", "Infinity", "0", "-1h", "1e1000000d"]:
                with self.subTest(duration=duration):
                    code = self.run_cli(data_dir, "summary", f"--last={duration}")
                    self.assertEqual(code, 2)

    def test_show_rejects_negative_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                ),
                0,
            )

            code = self.run_cli(data_dir, "show", "--limit", "-1")

            self.assertEqual(code, 2)
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

    def test_read_ledger_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
                "--output-tokens",
                "25",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"]["tokens_consumed"] = 999
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "record_hash mismatch"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_unsupported_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["schema_version"] = tool.SCHEMA_VERSION + 1
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "unsupported schema_version"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_missing_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["schema_version"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "schema_version"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_non_string_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["provider"] = {"name": "openai"}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_whitespace_padded_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["provider"] = " openai "
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_whitespace_padded_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["model"] = " gpt-5.4 "
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "model"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_record_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["record_id"] = " not-a-real-record-id "
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "record_id"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_unknown_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["status"] = "definitely-not-a-status"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "status"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_completed_nonzero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--status",
                "failed",
                "--exit-code",
                "1",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["status"] = "completed"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "exit_code"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_failed_zero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--status",
                "failed",
                "--exit-code",
                "1",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["exit_code"] = 0
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "exit_code"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_audit_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["created_at"] = "not-a-time"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "created_at"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_host_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["host"] = "not-an-object"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "field 'host' must be object"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_host_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["host"]["python"] = {"version": record["host"]["python"]}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "host.python"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_notes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["notes"] = {"text": "manual note"}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "notes"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_duplicate_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            run_id = "run_duplicate_ledger"
            code = self.run_cli(
                data_dir,
                "record",
                "--run-id",
                run_id,
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            duplicate = dict(record)
            duplicate["record_id"] = "rec_0000000000000000"
            tool.refresh_record_hash(duplicate)
            ledger.write_text(json.dumps(record) + "\n" + json.dumps(duplicate) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "duplicate run_id"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_duplicate_import_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            duplicate = dict(record)
            duplicate["record_id"] = "rec_0000000000000001"
            tool.refresh_record_hash(duplicate)
            ledger.write_text(json.dumps(record) + "\n" + json.dumps(duplicate) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "duplicate import_key"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_provider_bucket_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["run_id"] = "run_provider_bucket"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "run_id"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_provider_bucket_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["exit_code"] = 0
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "exit_code"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_provider_bucket_failed_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["status"] = "failed"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "status"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_duplicate_record_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            duplicate = dict(record)
            duplicate["started_at"] = "2026-06-18T20:02:00Z"
            duplicate["finished_at"] = "2026-06-18T20:03:00Z"
            tool.refresh_record_hash(duplicate)
            ledger.write_text(json.dumps(record) + "\n" + json.dumps(duplicate) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "duplicate record_id"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_usage_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"] = "not-an-object"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "field 'usage' must be object"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_usage_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"]["input_tokens"] = "many"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "usage.input_tokens"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_inconsistent_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["duration_ms"] = 999999
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "duration_ms"):
                tool.read_ledger(data_dir)

    def test_read_ledger_allows_monotonic_local_recorder_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                sys.executable,
                "--version",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(record["source"]["type"], "local_recorder")
            self.assertEqual(record["source"]["duration_clock"], "monotonic")
            record["duration_ms"] = 999999
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            records = tool.read_ledger(data_dir)

            self.assertEqual(records[0]["duration_ms"], 999999)

    def test_read_ledger_rejects_monotonic_clock_on_non_local_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["duration_clock"] = "monotonic"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.duration_clock"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_local_recorder_without_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                sys.executable,
                "--version",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["source"]["command"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.command"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_local_recorder_without_monotonic_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                sys.executable,
                "--version",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["source"]["duration_clock"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.duration_clock"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_local_recorder_without_unavailable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                sys.executable,
                "--version",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["usage"]["unavailable_reason"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "usage.unavailable_reason"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_inconsistent_tokens_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
                "--output-tokens",
                "25",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"]["tokens_consumed"] = 999
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "usage.tokens_consumed"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_missing_tokens_consumed_with_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"]["tokens_consumed"] = None
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "usage.tokens_consumed"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_missing_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"] = {}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.type"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_unknown_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["type"] = "unknown"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "unsupported value 'unknown'"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_native_telemetry_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"] = {"type": "native_telemetry"}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.adapter"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_vendor_session_store_bad_evidence_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"] = {
                "type": "vendor_session_store",
                "adapter": "test-adapter",
                "adapter_version": "1.0.0",
                "evidence_sha256": "not-a-sha",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.evidence_sha256"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_provider_export_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"] = {"type": "provider_export"}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.file"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_provider_export_bad_hash_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["file_sha256"] = "not-a-sha"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.file_sha256"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_provider_export_when_evidence_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            sample.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.file_sha256"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_provider_export_when_evidence_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            sample.unlink()

            with self.assertRaisesRegex(tool.CliError, "source.file"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_padded_provider_export_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["file"] = f" {record['source']['file']} "
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.file"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_relative_provider_export_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            sample = root / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["file"] = sample.name
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with self.assertRaisesRegex(tool.CliError, "absolute evidence file path"):
                    tool.read_ledger(data_dir)
            finally:
                os.chdir(previous_cwd)

    def test_read_ledger_rejects_usage_bucket_with_cost_provider_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["provider_object"] = "organization.costs.result"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider_object"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_cost_bucket_with_usage_provider_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["provider_object"] = "organization.usage.completions.result"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider_object"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_openai_usage_export_retagged_to_other_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["provider"] = "anthropic"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_openai_cost_export_retagged_to_other_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["provider"] = "anthropic"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_usage_bucket_retagged_to_manual_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["type"] = "manual_attestation"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.type"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_cost_bucket_retagged_to_manual_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["type"] = "manual_attestation"
            record["billing"] = {
                "actual_cost_usd": "0.06",
                "currency": "usd",
                "source": "manual_attestation",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.type"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_usage_bucket_with_provider_cost_billing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"] = {
                "actual_cost_usd": "0.06",
                "currency": "usd",
                "source": "provider_cost_api",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.source"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_usage_bucket_without_usage_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"] = tool.blank_usage()
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider_usage_bucket"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_cost_bucket_with_usage_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["usage"]["input_tokens"] = 10
            record["usage"]["tokens_consumed"] = 10
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "provider_cost_bucket"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_cost_bucket_with_manual_billing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["source"] = "manual_attestation"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.source"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_cost_bucket_with_unavailable_billing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"] = {
                "actual_cost_usd": None,
                "currency": None,
                "source": "unavailable",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.source"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_cost_bucket_with_model_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["model"] = "gpt-5.4"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "model"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_local_recorder_with_usage_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"] = {
                "type": "local_recorder",
                "command": [sys.executable, "--version"],
                "duration_clock": "monotonic",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.type is local_recorder"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_local_recorder_with_billing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                sys.executable,
                "--version",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"] = {
                "actual_cost_usd": "0.12",
                "currency": "usd",
                "source": "manual_attestation",
            }
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.source"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_pricing_estimate_actual_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--cost-usd",
                "0.12",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["source"] = "pricing_estimate"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.source"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_provider_billing_on_manual_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--cost-usd",
                "0.12",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["source"] = "provider_export"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "source.type is provider_export"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_billing_source_without_actual_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["source"] = "manual_attestation"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "actual_cost_usd is null"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_currency_without_actual_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["currency"] = "usd"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.currency"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_provider_billing_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                        "project_id": "proj_123",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["project_id"] = {"id": "proj_123"}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.project_id"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_malformed_provider_billing_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                        "quantity": 1200,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["billing"]["quantity"] = {"tokens": 1200}
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "billing.quantity"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_unavailable_source_with_usage_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--input-tokens",
                "100",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            record["source"]["type"] = "unavailable"
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "usage values must be null"):
                tool.read_ledger(data_dir)

    def test_read_ledger_rejects_hash_valid_unavailable_source_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "record",
                "--provider",
                "openai",
                "--model",
                "gpt-5.4",
                "--started-at",
                "2026-06-18T20:00:00Z",
                "--finished-at",
                "2026-06-18T20:01:00Z",
                "--source",
                "unavailable",
            )
            self.assertEqual(code, 0)
            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["usage"]["unavailable_reason"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(tool.CliError, "usage.unavailable_reason"):
                tool.read_ledger(data_dir)

    def test_import_openai_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "input_cached_tokens": 2,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"]["type"], "provider_export")
            self.assertIn("result_identity", records[0]["source"])
            self.assertEqual(records[0]["usage"]["tokens_consumed"], 15)

    def test_import_openai_usage_records_absolute_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            exports_dir = root / "exports"
            exports_dir.mkdir()
            sample = exports_dir / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            script = Path(tool.__file__).resolve()

            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--data-dir",
                    str(data_dir),
                    "import-openai-usage",
                    "--file",
                    str(Path("exports") / "usage.json"),
                ],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            source_file = Path(records[0]["source"]["file"])
            self.assertTrue(source_file.is_absolute())
            self.assertEqual(source_file, sample.resolve())

    def test_openai_usage_import_rejects_corrected_bucket_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            first = root / "usage-first.json"
            corrected = root / "usage-corrected.json"

            def payload(input_tokens: int) -> dict[str, object]:
                return {
                    "object": "page",
                    "data": [
                        {
                            "object": "bucket",
                            "start_time": 1781740800,
                            "end_time": 1781827200,
                            "results": [
                                {
                                    "object": "organization.usage.completions.result",
                                    "input_tokens": input_tokens,
                                    "output_tokens": 5,
                                    "num_model_requests": 1,
                                    "model": "gpt-5.4",
                                }
                            ],
                        }
                    ],
                }

            first.write_text(json.dumps(payload(10)), encoding="utf-8")
            corrected.write_text(json.dumps(payload(11)), encoding="utf-8")

            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(first)), 0)
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(corrected))

            self.assertEqual(code, 2)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["usage"]["tokens_consumed"], 15)

    def test_openai_usage_import_rejects_corrected_legacy_bucket_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            first = root / "usage-first.json"
            corrected = root / "usage-corrected.json"

            def payload(input_tokens: int) -> dict[str, object]:
                return {
                    "object": "page",
                    "data": [
                        {
                            "object": "bucket",
                            "start_time": 1781740800,
                            "end_time": 1781827200,
                            "results": [
                                {
                                    "object": "organization.usage.completions.result",
                                    "input_tokens": input_tokens,
                                    "output_tokens": 5,
                                    "num_model_requests": 1,
                                    "model": "gpt-5.4",
                                }
                            ],
                        }
                    ],
                }

            first.write_text(json.dumps(payload(10)), encoding="utf-8")
            corrected.write_text(json.dumps(payload(11)), encoding="utf-8")
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(first)), 0)

            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["source"]["result_identity"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertNotIn("result_identity", tool.read_ledger(data_dir)[0]["source"])

            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(corrected))

            self.assertEqual(code, 2)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["usage"]["tokens_consumed"], 15)

    def test_openai_usage_import_rejects_malformed_numeric_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": "not-a-number",
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_non_string_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": {"id": "gpt-5.4"},
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_whitespace_padded_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": " gpt-5.4 ",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_malformed_bucket_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": "1781740800",
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_out_of_range_bucket_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 999999999999999999999999999999,
                                "end_time": 1000000000000000000000000000000,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_missing_results_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_non_object_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": ["not-a-result"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_unknown_result_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.telemetry.future.result",
                                        "total_tokens": 15,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_usage_result_without_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_usage_import_rejects_cost_only_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_reversed_bucket_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781827200,
                                "end_time": 1781740800,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_missing_results_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_import_rejects_malformed_page_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_import_rejects_incomplete_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                        "has_more": True,
                        "next_page": "cursor-2",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_import_rejects_next_page_on_final_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                        "has_more": False,
                        "next_page": "cursor-2",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_usage_only_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_direct_openai_usage_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(sample)), 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertIn("import_key", records[0]["source"])

    def test_openai_usage_import_rejects_duplicate_result_rows_in_same_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            result = {
                "object": "organization.usage.completions.result",
                "input_tokens": 10,
                "output_tokens": 5,
                "num_model_requests": 1,
                "model": "gpt-5.4",
            }
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [result, dict(result)],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-usage", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_concurrent_openai_usage_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "usage.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            script = Path(tool.__file__).resolve()
            procs = [
                subprocess.Popen(
                    [
                        sys.executable,
                        str(script),
                        "--data-dir",
                        str(data_dir),
                        "import-openai-usage",
                        "--file",
                        str(sample),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(4)
            ]
            results = [proc.communicate(timeout=10) for proc in procs]
            failures = [
                (proc.returncode, stdout, stderr)
                for proc, (stdout, stderr) in zip(procs, results)
                if proc.returncode != 0
            ]
            self.assertEqual(failures, [])
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)

    def test_concurrent_start_creates_one_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_concurrent_start"
            script = Path(tool.__file__).resolve()
            procs = [
                subprocess.Popen(
                    [
                        sys.executable,
                        str(script),
                        "--data-dir",
                        str(data_dir),
                        "start",
                        "--run-id",
                        run_id,
                        "--provider",
                        "openai",
                        "--model",
                        "gpt-5.4",
                        "--started-at",
                        "2026-06-18T20:00:00Z",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(6)
            ]
            results = [proc.communicate(timeout=10) for proc in procs]
            success_count = sum(1 for proc in procs if proc.returncode == 0)
            self.assertEqual(
                success_count,
                1,
                [(proc.returncode, stdout, stderr) for proc, (stdout, stderr) in zip(procs, results)],
            )
            run_path = data_dir / "runs" / f"{run_id}.json"
            self.assertTrue(run_path.exists())
            run_state = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(run_state["run_id"], run_id)
            self.assertEqual(run_state["status"], "running")

    def test_concurrent_finish_appends_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_concurrent_finish"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )
            script = Path(tool.__file__).resolve()
            procs = [
                subprocess.Popen(
                    [
                        sys.executable,
                        str(script),
                        "--data-dir",
                        str(data_dir),
                        "finish",
                        "--run-id",
                        run_id,
                        "--finished-at",
                        "2026-06-18T20:01:00Z",
                        "--input-tokens",
                        "10",
                        "--output-tokens",
                        "5",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(6)
            ]
            results = [proc.communicate(timeout=10) for proc in procs]
            success_count = sum(1 for proc in procs if proc.returncode == 0)
            self.assertEqual(
                success_count,
                len(procs),
                [(proc.returncode, stdout, stderr) for proc, (stdout, stderr) in zip(procs, results)],
            )
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["run_id"], run_id)
            run_state = json.loads((data_dir / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "completed")
            self.assertEqual(run_state["record_id"], records[0]["record_id"])

    def test_finish_is_idempotent_after_completed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_retry_completed"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "10",
                    "--output-tokens",
                    "5",
                ),
                0,
            )

            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            first_record_id = records[0]["record_id"]
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "10",
                    "--output-tokens",
                    "5",
                ),
                0,
            )

            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["record_id"], first_record_id)

    def test_finish_repairs_stale_run_state_without_duplicate_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_retry_after_crash"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "10",
                    "--output-tokens",
                    "5",
                ),
                0,
            )
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            first_record_id = records[0]["record_id"]
            run_path = data_dir / "runs" / f"{run_id}.json"
            stale_state = json.loads(run_path.read_text(encoding="utf-8"))
            stale_state["status"] = "running"
            stale_state.pop("finished_at", None)
            stale_state.pop("record_id", None)
            run_path.write_text(json.dumps(stale_state) + "\n", encoding="utf-8")

            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "10",
                    "--output-tokens",
                    "5",
                ),
                0,
            )

            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["record_id"], first_record_id)
            repaired_state = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(repaired_state["status"], "completed")
            self.assertEqual(repaired_state["record_id"], first_record_id)

    def test_finish_marks_nonzero_exit_code_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_finish_failed"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )

            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--exit-code",
                    "1",
                ),
                0,
            )

            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "failed")
            self.assertEqual(records[0]["exit_code"], 1)
            run_state = json.loads((data_dir / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "completed")

    def test_start_rejects_empty_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            code = self.run_cli(
                data_dir,
                "start",
                "--run-id",
                "run_empty_model",
                "--provider",
                "openai",
                "--model",
                "",
                "--started-at",
                "2026-06-18T20:00:00Z",
            )

            self.assertEqual(code, 2)
            self.assertFalse((data_dir / "runs" / "run_empty_model.json").exists())

    def test_finish_rejects_non_object_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runs_dir = data_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "bad.json").write_text("[1]\n", encoding="utf-8")

            code = self.run_cli(data_dir, "finish", "--run-id", "bad", "--finished-at", "2026-06-18T20:01:00Z")

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_empty_provider_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_empty_provider_override"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )

            code = self.run_cli(
                data_dir,
                "finish",
                "--run-id",
                run_id,
                "--provider",
                "",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])
            run_state = json.loads((data_dir / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "running")

    def test_finish_rejects_empty_model_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_empty_model_override"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )

            code = self.run_cli(
                data_dir,
                "finish",
                "--run-id",
                run_id,
                "--model",
                "",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])
            run_state = json.loads((data_dir / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "running")

    def test_finish_rejects_mismatched_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runs_dir = data_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "bad.json").write_text(
                json.dumps(
                    {
                        "schema_version": tool.SCHEMA_VERSION,
                        "run_id": "other",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "started_at": "2026-06-18T20:00:00Z",
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "finish", "--run-id", "bad", "--finished-at", "2026-06-18T20:01:00Z")

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_malformed_run_state_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runs_dir = data_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "badtime.json").write_text(
                json.dumps(
                    {
                        "schema_version": tool.SCHEMA_VERSION,
                        "run_id": "badtime",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "started_at": {"at": "2026-06-18T20:00:00Z"},
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(
                data_dir,
                "finish",
                "--run-id",
                "badtime",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_malformed_run_state_identity_metadata(self) -> None:
        cases = [
            ("badprovider", {"provider": ""}, "provider"),
            ("badmodel", {"model": " gpt-5.4 "}, "model"),
        ]
        for run_id, overrides, expected_error in cases:
            with self.subTest(run_id=run_id), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp) / "data"
                runs_dir = data_dir / "runs"
                runs_dir.mkdir(parents=True)
                state = {
                    "schema_version": tool.SCHEMA_VERSION,
                    "run_id": run_id,
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "started_at": "2026-06-18T20:00:00Z",
                    "status": "running",
                }
                state.update(overrides)
                (runs_dir / f"{run_id}.json").write_text(json.dumps(state), encoding="utf-8")

                code = self.run_cli(
                    data_dir,
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                )

                self.assertEqual(code, 2, expected_error)
                self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_completed_state_without_ledger_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runs_dir = data_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "orphan.json").write_text(
                json.dumps(
                    {
                        "schema_version": tool.SCHEMA_VERSION,
                        "run_id": "orphan",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "started_at": "2026-06-18T20:00:00Z",
                        "finished_at": "2026-06-18T20:01:00Z",
                        "status": "completed",
                        "record_id": "rec_missing",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(
                data_dir,
                "finish",
                "--run-id",
                "orphan",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_unsupported_run_state_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runs_dir = data_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "future.json").write_text(
                json.dumps(
                    {
                        "schema_version": tool.SCHEMA_VERSION + 1,
                        "run_id": "future",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "started_at": "2026-06-18T20:00:00Z",
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(
                data_dir,
                "finish",
                "--run-id",
                "future",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_missing_run_state_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runs_dir = data_dir / "runs"
            runs_dir.mkdir(parents=True)
            (runs_dir / "unversioned.json").write_text(
                json.dumps(
                    {
                        "run_id": "unversioned",
                        "provider": "openai",
                        "model": "gpt-5.4",
                        "started_at": "2026-06-18T20:00:00Z",
                        "status": "running",
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(
                data_dir,
                "finish",
                "--run-id",
                "unversioned",
                "--finished-at",
                "2026-06-18T20:01:00Z",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_finish_rejects_provider_export_source_for_manual_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            run_id = "run_manual_source"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                ),
                0,
            )
            script = Path(tool.__file__).resolve()
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--data-dir",
                    str(data_dir),
                    "finish",
                    "--run-id",
                    run_id,
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                    "--input-tokens",
                    "100",
                    "--source",
                    "provider_export",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("invalid choice", proc.stderr)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_direct_openai_cost_import_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                        "project_id": None,
                                        "api_key_id": None,
                                        "quantity": None,
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(sample)), 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertIn("import_key", records[0]["source"])
            self.assertIn("result_identity", records[0]["source"])

    def test_openai_cost_import_rejects_duplicate_result_rows_in_same_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            result = {
                "object": "organization.costs.result",
                "amount": {"value": 0.06, "currency": "usd"},
                "line_item": "Completions",
                "project_id": None,
                "api_key_id": None,
                "quantity": None,
            }
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [result, dict(result)],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_corrected_bucket_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            first = root / "costs-first.json"
            corrected = root / "costs-corrected.json"

            def payload(amount: float) -> dict[str, object]:
                return {
                    "object": "page",
                    "data": [
                        {
                            "object": "bucket",
                            "start_time": 1781740800,
                            "end_time": 1781827200,
                            "results": [
                                {
                                    "object": "organization.costs.result",
                                    "amount": {"value": amount, "currency": "usd"},
                                    "line_item": "Completions",
                                    "project_id": "proj_123",
                                    "api_key_id": None,
                                    "quantity": None,
                                }
                            ],
                        }
                    ],
                }

            first.write_text(json.dumps(payload(0.06)), encoding="utf-8")
            corrected.write_text(json.dumps(payload(0.07)), encoding="utf-8")

            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(first)), 0)
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(corrected))

            self.assertEqual(code, 2)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["billing"]["actual_cost_usd"], "0.06")

    def test_openai_cost_import_rejects_corrected_legacy_bucket_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            first = root / "costs-first.json"
            corrected = root / "costs-corrected.json"

            def payload(amount: float) -> dict[str, object]:
                return {
                    "object": "page",
                    "data": [
                        {
                            "object": "bucket",
                            "start_time": 1781740800,
                            "end_time": 1781827200,
                            "results": [
                                {
                                    "object": "organization.costs.result",
                                    "amount": {"value": amount, "currency": "usd"},
                                    "line_item": "Completions",
                                    "project_id": "proj_123",
                                    "api_key_id": None,
                                    "quantity": None,
                                }
                            ],
                        }
                    ],
                }

            first.write_text(json.dumps(payload(0.06)), encoding="utf-8")
            corrected.write_text(json.dumps(payload(0.07)), encoding="utf-8")
            self.assertEqual(self.run_cli(data_dir, "import-openai-costs", "--file", str(first)), 0)

            ledger = tool.ledger_path(data_dir)
            record = json.loads(ledger.read_text(encoding="utf-8"))
            del record["source"]["result_identity"]
            tool.refresh_record_hash(record)
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
            self.assertNotIn("result_identity", tool.read_ledger(data_dir)[0]["source"])

            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(corrected))

            self.assertEqual(code, 2)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["billing"]["actual_cost_usd"], "0.06")

    def test_mixed_openai_import_appends_usage_and_costs_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "mixed.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    },
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(tool.import_file_by_type(data_dir, sample), (True, 2))

            records = tool.read_ledger(data_dir)
            self.assertEqual([record["kind"] for record in records], ["provider_usage_bucket", "provider_cost_bucket"])

    def test_mixed_openai_import_rejects_malformed_cost_without_partial_usage_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "mixed.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    },
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": "not-a-cost", "currency": "usd"},
                                        "line_item": "Completions",
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(tool.CliError, "amount.value"):
                tool.import_file_by_type(data_dir, sample)

            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_fetch_openai_imports_admin_usage_and_costs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            calls = []

            def fake_get_json(base_url: str, endpoint: str, params: dict[str, object], api_key: str) -> object:
                calls.append((base_url, endpoint, dict(params), api_key))
                self.assertEqual(base_url, "https://api.openai.com/v1")
                self.assertEqual(api_key, "sk-admin-test")
                self.assertEqual(params["start_time"], 1781740800)
                self.assertEqual(params["end_time"], 1781827200)
                self.assertEqual(params["bucket_width"], "1d")
                if endpoint == "/organization/usage/completions":
                    self.assertEqual(params["group_by"], ["model"])
                    return {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 10,
                                        "output_tokens": 5,
                                        "input_cached_tokens": 2,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                        "has_more": False,
                        "next_page": None,
                    }
                if endpoint == "/organization/costs":
                    self.assertEqual(params["group_by"], ["line_item"])
                    return {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "usd"},
                                        "line_item": "Completions",
                                        "quantity": 15,
                                    }
                                ],
                            }
                        ],
                        "has_more": False,
                        "next_page": None,
                    }
                raise AssertionError(f"unexpected endpoint {endpoint}")

            with mock.patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "sk-admin-test"}), mock.patch.object(
                tool,
                "openai_admin_get_json",
                side_effect=fake_get_json,
            ):
                code = self.run_cli(
                    data_dir,
                    "fetch-openai",
                    "--from",
                    "2026-06-18",
                    "--to",
                    "2026-06-19",
                )

            self.assertEqual(code, 0)
            self.assertEqual([call[1] for call in calls], ["/organization/usage/completions", "/organization/costs"])
            records = tool.read_ledger(data_dir)
            self.assertEqual([record["kind"] for record in records], ["provider_usage_bucket", "provider_cost_bucket"])
            self.assertEqual(records[0]["model"], "gpt-5.4")
            self.assertEqual(records[0]["usage"]["tokens_consumed"], 15)
            self.assertEqual(records[1]["billing"]["actual_cost_usd"], "0.06")
            self.assertEqual(records[1]["billing"]["quantity"], 15)
            self.assertEqual(len(list((data_dir / "openai-exports").glob("openai-*.json"))), 2)

    def test_fetch_openai_rejects_repeated_next_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"

            def fake_get_json(base_url: str, endpoint: str, params: dict[str, object], api_key: str) -> object:
                self.assertEqual(endpoint, "/organization/usage/completions")
                return {
                    "object": "page",
                    "data": [],
                    "has_more": True,
                    "next_page": "stuck-cursor",
                }

            with mock.patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "sk-admin-test"}), mock.patch.object(
                tool,
                "openai_admin_get_json",
                side_effect=fake_get_json,
            ):
                code = self.run_cli(
                    data_dir,
                    "fetch-openai",
                    "--from",
                    "2026-06-18",
                    "--to",
                    "2026-06-19",
                    "--kind",
                    "usage",
                )

            self.assertEqual(code, 2)
            self.assertFalse((data_dir / "openai-exports").exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_fetch_openai_rejects_non_boolean_has_more(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"

            def fake_get_json(base_url: str, endpoint: str, params: dict[str, object], api_key: str) -> object:
                self.assertEqual(endpoint, "/organization/usage/completions")
                return {
                    "object": "page",
                    "data": [],
                    "has_more": "false",
                    "next_page": None,
                }

            with mock.patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "sk-admin-test"}), mock.patch.object(
                tool,
                "openai_admin_get_json",
                side_effect=fake_get_json,
            ):
                code = self.run_cli(
                    data_dir,
                    "fetch-openai",
                    "--from",
                    "2026-06-18",
                    "--to",
                    "2026-06-19",
                    "--kind",
                    "usage",
                )

            self.assertEqual(code, 2)
            self.assertFalse((data_dir / "openai-exports").exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_fetch_openai_rejects_next_page_without_has_more(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"

            def fake_get_json(base_url: str, endpoint: str, params: dict[str, object], api_key: str) -> object:
                self.assertEqual(endpoint, "/organization/usage/completions")
                return {
                    "object": "page",
                    "data": [],
                    "has_more": False,
                    "next_page": "unexpected-cursor",
                }

            with mock.patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "sk-admin-test"}), mock.patch.object(
                tool,
                "openai_admin_get_json",
                side_effect=fake_get_json,
            ):
                code = self.run_cli(
                    data_dir,
                    "fetch-openai",
                    "--from",
                    "2026-06-18",
                    "--to",
                    "2026-06-19",
                    "--kind",
                    "usage",
                )

            self.assertEqual(code, 2)
            self.assertFalse((data_dir / "openai-exports").exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_fetch_openai_requires_admin_key_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(tool, "openai_admin_get_json") as get_json:
                code = self.run_cli(
                    data_dir,
                    "fetch-openai",
                    "--from",
                    "2026-06-18",
                    "--to",
                    "2026-06-19",
                )

            self.assertEqual(code, 2)
            get_json.assert_not_called()
            self.assertFalse((data_dir / "openai-exports").exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_fetch_openai_rejects_corrected_bucket_without_overwriting_raw_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            input_tokens = [10, 20]

            def fake_get_json(base_url: str, endpoint: str, params: dict[str, object], api_key: str) -> object:
                self.assertEqual(endpoint, "/organization/usage/completions")
                return {
                    "object": "page",
                    "data": [
                        {
                            "object": "bucket",
                            "start_time": 1781740800,
                            "end_time": 1781827200,
                            "results": [
                                {
                                    "object": "organization.usage.completions.result",
                                    "input_tokens": input_tokens.pop(0),
                                    "output_tokens": 5,
                                    "num_model_requests": 1,
                                    "model": "gpt-5.4",
                                }
                            ],
                        }
                    ],
                    "has_more": False,
                    "next_page": None,
                }

            with mock.patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "sk-admin-test"}), mock.patch.object(
                tool,
                "openai_admin_get_json",
                side_effect=fake_get_json,
            ):
                fetch_args = [
                    "fetch-openai",
                    "--from",
                    "2026-06-18",
                    "--to",
                    "2026-06-19",
                    "--kind",
                    "usage",
                ]
                self.assertEqual(self.run_cli(data_dir, *fetch_args), 0)
                self.assertEqual(self.run_cli(data_dir, *fetch_args), 2)

            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            first_path = Path(records[0]["source"]["file"])
            second_path = data_dir / "openai-exports" / "openai-usage-20260618-000000Z-20260619-000000Z-1.json"
            self.assertNotEqual(first_path, second_path)
            self.assertTrue(first_path.exists())
            self.assertTrue(second_path.exists())
            self.assertEqual(tool.file_sha256(first_path), records[0]["source"]["file_sha256"])
            self.assertEqual(records[0]["usage"]["input_tokens"], 10)
            self.assertEqual(json.loads(second_path.read_text(encoding="utf-8"))["data"][0]["results"][0]["input_tokens"], 20)

    def test_atomic_write_json_uses_unique_temp_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state" / "imported-files.json"
            replace_sources: list[Path] = []
            real_replace = Path.replace

            def tracking_replace(self: Path, target_path: Path) -> Path:
                replace_sources.append(self)
                return real_replace(self, target_path)

            with mock.patch.object(Path, "replace", autospec=True, side_effect=tracking_replace):
                tool.atomic_write_json(target, {"index": 1})
                tool.atomic_write_json(target, {"index": 2})

            self.assertEqual(len(replace_sources), 2)
            self.assertEqual(len({path.name for path in replace_sources}), 2)
            self.assertTrue(
                all(
                    path.name.startswith(".imported-files.json.") and path.name.endswith(".tmp")
                    for path in replace_sources
                )
            )
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["index"], 2)
            self.assertEqual([path for path in target.parent.iterdir() if path.name.endswith(".tmp")], [])

    def test_atomic_write_json_retries_transient_windows_replace_denial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state" / "imported-files.json"
            real_replace = Path.replace
            attempts = 0

            def flaky_replace(self: Path, target_path: Path) -> Path:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("destination is temporarily busy")
                return real_replace(self, target_path)

            with mock.patch.object(Path, "replace", autospec=True, side_effect=flaky_replace), mock.patch.object(
                tool.os,
                "name",
                "nt",
            ), mock.patch.object(tool.time, "sleep") as sleep:
                tool.atomic_write_json(target, {"index": 1})

            self.assertEqual(attempts, 2)
            sleep.assert_called_once()
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["index"], 1)
            self.assertEqual([path for path in target.parent.iterdir() if path.name.endswith(".tmp")], [])

    def test_atomic_write_json_survives_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "state" / "imported-files.json"

            def write_state(index: int) -> None:
                tool.atomic_write_json(target, {"index": index})

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(write_state, range(16)))

            stored = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(stored["index"], range(16))
            self.assertEqual([path for path in target.parent.iterdir() if path.name.endswith(".tmp")], [])

    def test_write_new_json_allocates_unique_paths_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "evidence" / "openai-usage.json"

            def write_payload(index: int) -> Path:
                return tool.write_new_json(base, {"index": index})

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                paths = list(executor.map(write_payload, range(8)))

            self.assertEqual(len(set(paths)), 8)
            self.assertIn("openai-usage.json", {path.name for path in paths})
            self.assertEqual([path for path in base.parent.iterdir() if path.name.endswith(".tmp")], [])
            stored_indexes = sorted(json.loads(path.read_text(encoding="utf-8"))["index"] for path in paths)
            self.assertEqual(stored_indexes, list(range(8)))

    def test_write_new_json_cleans_temp_file_after_publish_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "evidence" / "openai-usage.json"
            staged_paths: list[Path] = []
            real_link = os.link

            def racing_link(src: Path, dst: Path) -> None:
                staged_paths.append(src)
                if dst == base and not base.exists():
                    base.write_text(json.dumps({"index": "winner"}), encoding="utf-8")
                    raise FileExistsError
                real_link(src, dst)

            with mock.patch.object(tool.os, "link", side_effect=racing_link):
                path = tool.write_new_json(base, {"index": 1})

            self.assertEqual(path.name, "openai-usage-1.json")
            self.assertEqual(json.loads(base.read_text(encoding="utf-8"))["index"], "winner")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["index"], 1)
            self.assertTrue(staged_paths)
            self.assertFalse(any(path.exists() for path in staged_paths))

    def test_openai_cost_import_rejects_malformed_amount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": "not-a-cost", "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_missing_amount_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_null_amount_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": None, "currency": "usd"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_missing_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_openai_cost_import_rejects_non_usd_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            sample = Path(tmp) / "costs.json"
            sample.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.costs.result",
                                        "amount": {"value": 0.06, "currency": "eur"},
                                        "line_item": "Completions",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code = self.run_cli(data_dir, "import-openai-costs", "--file", str(sample))
            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_imports_once_and_deduplicates_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "usage.json").write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 7,
                                        "output_tokens": 3,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4-mini",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()
            self.assertEqual(tool.scan_inbox_once(args), 1)
            self.assertEqual(tool.scan_inbox_once(args), 0)
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

    def test_watch_tracks_imported_files_by_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            usage_export = inbox / "usage.json"
            usage_export.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 7,
                                        "output_tokens": 3,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4-mini",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                relative_args = type("Args", (), {"data_dir": data_dir, "inbox": Path("inbox"), "notes": None})()
                absolute_args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()
                self.assertEqual(tool.scan_inbox_once(relative_args), 1)
                self.assertEqual(tool.scan_inbox_once(absolute_args), 0)
            finally:
                os.chdir(previous_cwd)

            state = tool.load_imported_state(data_dir)
            self.assertEqual(set(state["files"]), {str(usage_export.resolve())})
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

    def test_watch_marks_recognized_duplicate_exports_as_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            payload = {
                "object": "page",
                "data": [
                    {
                        "object": "bucket",
                        "start_time": 1781740800,
                        "end_time": 1781827200,
                        "results": [
                            {
                                "object": "organization.usage.completions.result",
                                "input_tokens": 7,
                                "output_tokens": 3,
                                "num_model_requests": 1,
                                "model": "gpt-5.4-mini",
                            }
                        ],
                    }
                ],
            }
            source = root / "source.json"
            duplicate = inbox / "duplicate.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            duplicate.write_text(json.dumps(payload), encoding="utf-8")

            self.assertEqual(self.run_cli(data_dir, "import-openai-usage", "--file", str(source)), 0)
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()
            self.assertEqual(tool.scan_inbox_once(args), 0)
            self.assertEqual(tool.scan_inbox_once(args), 0)

            state = tool.load_imported_state(data_dir)
            self.assertEqual(state["files"][str(duplicate.resolve())]["records"], 0)
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

    def test_concurrent_watch_scans_preserve_imported_state_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox_a = root / "inbox-a"
            inbox_b = root / "inbox-b"
            inbox_a.mkdir()
            inbox_b.mkdir()
            file_a = inbox_a / "usage-a.json"
            file_b = inbox_b / "usage-b.json"
            file_a.write_text("{}", encoding="utf-8")
            file_b.write_text("{}", encoding="utf-8")
            barrier = threading.Barrier(2)

            def fake_import(data_dir_arg: Path, path: Path, notes: str | None = None) -> tuple[bool, int]:
                self.assertEqual(data_dir_arg, data_dir)
                self.assertIsNone(notes)
                try:
                    barrier.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    pass
                return True, 1

            args_a = type("Args", (), {"data_dir": data_dir, "inbox": inbox_a, "notes": None})()
            args_b = type("Args", (), {"data_dir": data_dir, "inbox": inbox_b, "notes": None})()
            with mock.patch.object(tool, "import_file_by_type", side_effect=fake_import):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    imported = list(executor.map(tool.scan_inbox_once, [args_a, args_b]))

            self.assertEqual(imported, [1, 1])
            state = tool.load_imported_state(data_dir)
            self.assertEqual(set(state["files"]), {str(file_a.resolve()), str(file_b.resolve())})
            self.assertEqual(state["files"][str(file_a.resolve())]["records"], 1)
            self.assertEqual(state["files"][str(file_b.resolve())]["records"], 1)

    def test_watch_rejects_malformed_imported_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            data_dir.mkdir()
            state_path = tool.imported_state_path(data_dir)
            state_path.write_text("[]\n", encoding="utf-8")
            (inbox / "usage.json").write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.usage.completions.result",
                                        "input_tokens": 7,
                                        "output_tokens": 3,
                                        "num_model_requests": 1,
                                        "model": "gpt-5.4-mini",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()

            with self.assertRaisesRegex(tool.CliError, "invalid imported state"):
                tool.scan_inbox_once(args)

            self.assertEqual(state_path.read_text(encoding="utf-8"), "[]\n")
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_rejects_malformed_imported_state_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            tool.imported_state_path(data_dir).write_text(
                json.dumps(
                    {
                        "files": {
                            "usage.json": {
                                "sha256": "not-a-sha",
                                "imported_at": "2026-06-18T20:00:00Z",
                                "records": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(tool.CliError, "invalid sha256"):
                tool.load_imported_state(data_dir)

    def test_watch_rejects_relative_imported_state_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            tool.imported_state_path(data_dir).write_text(
                json.dumps(
                    {
                        "files": {
                            "usage.json": {
                                "sha256": "0" * 64,
                                "imported_at": "now",
                                "records": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(tool.CliError, "non-canonical imported_at"):
                tool.load_imported_state(data_dir)

    def test_watch_rejects_offset_imported_state_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            tool.imported_state_path(data_dir).write_text(
                json.dumps(
                    {
                        "files": {
                            "usage.json": {
                                "sha256": "0" * 64,
                                "imported_at": "2026-06-18T20:00:00+00:00",
                                "records": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(tool.CliError, "non-canonical imported_at"):
                tool.load_imported_state(data_dir)

    def test_watch_rejects_malformed_openai_export_instead_of_ignoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "bad-openai.json").write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()

            with self.assertRaisesRegex(tool.CliError, "results"):
                tool.scan_inbox_once(args)

            self.assertEqual(tool.load_imported_state(data_dir), {"files": {}})
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_rejects_malformed_openai_page_envelope_instead_of_ignoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "bad-page.json").write_text(
                json.dumps({"object": "page", "data": {"not": "a list"}}),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()

            with self.assertRaisesRegex(tool.CliError, "OpenAI page object"):
                tool.scan_inbox_once(args)

            self.assertEqual(tool.load_imported_state(data_dir), {"files": {}})
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_rejects_unknown_openai_result_instead_of_marking_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "future-openai.json").write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [
                                    {
                                        "object": "organization.telemetry.future.result",
                                        "total_tokens": 15,
                                        "model": "gpt-5.4",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()

            with self.assertRaisesRegex(tool.CliError, "unsupported OpenAI bucket result object"):
                tool.scan_inbox_once(args)

            self.assertEqual(tool.load_imported_state(data_dir), {"files": {}})
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_marks_empty_openai_export_as_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            empty_export = inbox / "empty-openai.json"
            empty_export.write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": 1781740800,
                                "end_time": 1781827200,
                                "results": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()

            self.assertEqual(tool.scan_inbox_once(args), 0)
            self.assertEqual(tool.scan_inbox_once(args), 0)

            state = tool.load_imported_state(data_dir)
            self.assertEqual(state["files"][str(empty_export.resolve())]["records"], 0)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_rejects_empty_openai_export_with_malformed_bucket_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "bad-empty-openai.json").write_text(
                json.dumps(
                    {
                        "object": "page",
                        "data": [
                            {
                                "object": "bucket",
                                "start_time": "1781740800",
                                "end_time": 1781827200,
                                "results": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            args = type("Args", (), {"data_dir": data_dir, "inbox": inbox, "notes": None})()

            with self.assertRaisesRegex(tool.CliError, "start_time"):
                tool.scan_inbox_once(args)

            self.assertEqual(tool.load_imported_state(data_dir), {"files": {}})
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_watch_rejects_invalid_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            inbox = Path(tmp) / "inbox"
            for interval in ["NaN", "Infinity", "0", "-1"]:
                with self.subTest(interval=interval):
                    code = self.run_cli(data_dir, "watch", "--once", "--inbox", str(inbox), f"--interval={interval}")
                    self.assertEqual(code, 2)

    def test_wrap_strips_separator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                sys.executable,
                "--version",
            )
            self.assertEqual(code, 0)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "completed")
            self.assertEqual(records[0]["record_hash"], tool.record_hash(records[0]))

    def test_wrap_rejects_missing_cwd_without_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            missing_cwd = root / "missing"

            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--cwd",
                str(missing_cwd),
                "--",
                sys.executable,
                "--version",
            )

            self.assertEqual(code, 2)
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_wrap_rejects_empty_provider_before_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            marker = root / "marker.txt"

            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "",
                "--",
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            )

            self.assertEqual(code, 2)
            self.assertFalse(marker.exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_wrap_rejects_empty_model_before_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            marker = root / "marker.txt"

            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "",
                "--",
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            )

            self.assertEqual(code, 2)
            self.assertFalse(marker.exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_wrap_rejects_duplicate_run_id_before_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            marker = root / "marker.txt"
            run_id = "run_duplicate_wrap"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "record",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                    "--started-at",
                    "2026-06-18T20:00:00Z",
                    "--finished-at",
                    "2026-06-18T20:01:00Z",
                ),
                0,
            )

            code = self.run_cli(
                data_dir,
                "wrap",
                "--run-id",
                run_id,
                "--provider",
                "local",
                "--",
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            )

            self.assertEqual(code, 2)
            self.assertFalse(marker.exists())
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

    def test_wrap_rejects_existing_run_state_before_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            marker = root / "marker.txt"
            run_id = "run_existing_wrap_state"
            self.assertEqual(
                self.run_cli(
                    data_dir,
                    "start",
                    "--run-id",
                    run_id,
                    "--provider",
                    "openai",
                    "--model",
                    "gpt-5.4",
                ),
                0,
            )

            code = self.run_cli(
                data_dir,
                "wrap",
                "--run-id",
                run_id,
                "--provider",
                "local",
                "--",
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
            )

            self.assertEqual(code, 2)
            self.assertFalse(marker.exists())
            self.assertEqual(tool.read_ledger(data_dir), [])

    def test_concurrent_wrap_same_run_id_runs_command_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            marker = root / "marker.txt"
            run_id = "run_concurrent_wrap"
            script = Path(tool.__file__).resolve()
            wrapped_code = (
                "from pathlib import Path; import time; "
                f"Path({str(marker)!r}).open('a', encoding='utf-8').write('ran\\n'); "
                "time.sleep(0.2)"
            )
            procs = [
                subprocess.Popen(
                    [
                        sys.executable,
                        str(script),
                        "--data-dir",
                        str(data_dir),
                        "wrap",
                        "--run-id",
                        run_id,
                        "--provider",
                        "local",
                        "--",
                        sys.executable,
                        "-c",
                        wrapped_code,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]

            results = [proc.communicate(timeout=10) for proc in procs]
            success_count = sum(1 for proc in procs if proc.returncode == 0)
            self.assertEqual(
                success_count,
                1,
                [(proc.returncode, stdout, stderr) for proc, (stdout, stderr) in zip(procs, results)],
            )
            self.assertEqual(marker.read_text(encoding="utf-8").splitlines(), ["ran"])
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["run_id"], run_id)

    def test_wrap_returns_command_not_found_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code = self.run_cli(
                data_dir,
                "wrap",
                "--provider",
                "local",
                "--model",
                "test",
                "--",
                "definitely-not-a-real-command-xyz",
            )
            self.assertEqual(code, 127)
            records = tool.read_ledger(data_dir)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["status"], "failed")
            self.assertEqual(records[0]["exit_code"], 127)
            self.assertEqual(records[0]["record_hash"], tool.record_hash(records[0]))


if __name__ == "__main__":
    unittest.main()
