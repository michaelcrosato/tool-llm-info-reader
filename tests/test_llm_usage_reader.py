import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
            self.assertEqual(state["files"][str(duplicate)]["records"], 0)
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

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
            self.assertEqual(state["files"][str(empty_export)]["records"], 0)
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
