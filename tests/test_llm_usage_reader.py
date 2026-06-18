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

    def test_start_rejects_path_traversal_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            code = self.run_cli(data_dir, "start", "--run-id", "../escape")
            self.assertEqual(code, 2)
            self.assertFalse((root / "escape.json").exists())
            self.assertFalse((data_dir / "escape.json").exists())

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


if __name__ == "__main__":
    unittest.main()
