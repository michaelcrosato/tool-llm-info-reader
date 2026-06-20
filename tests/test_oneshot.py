import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import llm_usage_reader as tool


def fake_adapter(
    response_text: str,
    *,
    model: str | None = "claude-opus-4-8",
    evidence_level: str = "native_telemetry",
    input_tokens: int | None = 120,
    output_tokens: int | None = 8,
    cached_input_tokens: int | None = None,
    reported_cost_usd: str | None = None,
):
    """Build a responder callable returning a fixed Invocation."""

    def _responder(prompt, system, model_arg, context):
        return tool.make_invocation(
            response_text=response_text,
            model=model,
            evidence_level=evidence_level,
            adapter="fake-adapter",
            adapter_version="1",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reported_cost_usd=reported_cost_usd,
            raw={"prompt": prompt, "system": system, "model": model_arg},
        )

    return _responder


class LibraryIntegrityTests(unittest.TestCase):
    def test_ids_are_unique(self) -> None:
        ids = [entry["id"] for entry in tool.ONESHOTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_required_fields_present(self) -> None:
        for entry in tool.ONESHOTS:
            for field in ("id", "title", "category", "difficulty", "prompt", "grader", "example_answer", "explanation"):
                self.assertIn(field, entry, f"{entry.get('id')} missing {field}")
            self.assertIn(entry["grader"]["type"], tool.GRADERS, entry["id"])
            self.assertTrue(entry["prompt"].strip())
            self.assertTrue(entry["explanation"].strip())

    def test_every_example_answer_passes_its_own_grader(self) -> None:
        # This is the load-bearing self-consistency invariant: a 1-shot whose
        # reference answer fails its own grader is a broken test criterion.
        for entry in tool.ONESHOTS:
            grade = tool.grade_output(str(entry["example_answer"]), entry["grader"])
            self.assertTrue(grade["passed"], f"{entry['id']} reference answer failed: {grade['detail']}")
            self.assertEqual(grade["score"], 1.0, entry["id"])

    def test_library_has_breadth(self) -> None:
        categories = {entry["category"] for entry in tool.ONESHOTS}
        self.assertGreaterEqual(len(categories), 5)
        self.assertGreaterEqual(len(tool.ONESHOTS), 12)


class GraderTests(unittest.TestCase):
    def test_exact_match_options(self) -> None:
        grader = {"type": "exact_match", "expected": "BANANA"}
        self.assertTrue(tool.grade_output("BANANA", grader)["passed"])
        self.assertTrue(tool.grade_output("  BANANA  ", grader)["passed"])
        self.assertFalse(tool.grade_output("banana", grader)["passed"])
        ci = {"type": "exact_match", "expected": "negative", "lowercase": True, "strip_punct": True}
        self.assertTrue(tool.grade_output("Negative.", ci)["passed"])

    def test_numeric_equals(self) -> None:
        grader = {"type": "numeric_equals", "expected": 391}
        self.assertTrue(tool.grade_output("391", grader)["passed"])
        self.assertTrue(tool.grade_output("The answer is 391.", grader)["passed"])
        self.assertFalse(tool.grade_output("392", grader)["passed"])
        tol = {"type": "numeric_equals", "expected": 40.5, "tolerance": 0.01}
        self.assertTrue(tool.grade_output("40.50", tol)["passed"])

    def test_not_contains(self) -> None:
        grader = {"type": "not_contains", "forbidden": ["apple"], "min_words": 1}
        self.assertTrue(tool.grade_output("cherry", grader)["passed"])
        self.assertFalse(tool.grade_output("Apple", grader)["passed"])
        self.assertFalse(tool.grade_output("", grader)["passed"])

    def test_json_equals_path_numeric(self) -> None:
        grader = {"type": "json_equals", "path": "total", "expected": 40.5, "numeric": True}
        self.assertTrue(tool.grade_output('{"total": 40.50}', grader)["passed"])
        self.assertTrue(tool.grade_output('```json\n{"total": 40.5}\n```', grader)["passed"])
        self.assertFalse(tool.grade_output('{"total": 41}', grader)["passed"])
        self.assertFalse(tool.grade_output("not json", grader)["passed"])

    def test_json_equals_array(self) -> None:
        expected = [{"item": "b", "qty": 5}, {"item": "a", "qty": 3}]
        grader = {"type": "json_equals", "expected": expected}
        self.assertTrue(tool.grade_output(json.dumps(expected), grader)["passed"])
        # Order matters for the array
        self.assertFalse(tool.grade_output(json.dumps(list(reversed(expected))), grader)["passed"])

    def test_summary(self) -> None:
        grader = {"type": "summary", "max_words": 20}
        self.assertTrue(tool.grade_output("Bees pollinate plants and sustain ecosystems worldwide.", grader)["passed"])
        too_long = " ".join(["word"] * 25) + "."
        self.assertFalse(tool.grade_output(too_long, grader)["passed"])
        self.assertFalse(tool.grade_output("Two sentences. Here is another.", grader)["passed"])

    def test_word_count(self) -> None:
        grader = {"type": "word_count", "count": 5}
        self.assertTrue(tool.grade_output("The ocean is very deep.", grader)["passed"])
        self.assertFalse(tool.grade_output("Too few words", grader)["passed"])

    def test_python_function_pass_and_fail(self) -> None:
        grader = {
            "type": "python_function",
            "function_name": "double",
            "cases": [{"args": [2], "expected": 4}, {"args": [5], "expected": 10}],
        }
        good = "```python\ndef double(n):\n    return n * 2\n```"
        self.assertTrue(tool.grade_output(good, grader)["passed"])
        bad = "```python\ndef double(n):\n    return n + 2\n```"
        result = tool.grade_output(bad, grader)
        self.assertFalse(result["passed"])
        self.assertLess(result["score"], 1.0)

    def test_python_function_handles_broken_code(self) -> None:
        grader = {"type": "python_function", "function_name": "f", "cases": [{"args": [1], "expected": 1}]}
        # Missing function definition -> graceful fail, not a crash.
        self.assertFalse(tool.grade_output("print('hi')", grader)["passed"])
        # Syntax error -> graceful fail.
        self.assertFalse(tool.grade_output("def f(:\n  pass", grader)["passed"])

    def test_python_function_timeout(self) -> None:
        grader = {
            "type": "python_function",
            "function_name": "f",
            "cases": [{"args": [1], "expected": 1}],
            "timeout": 2,
        }
        result = tool.grade_output("def f(n):\n    while True:\n        pass", grader)
        self.assertFalse(result["passed"])
        self.assertIn("timed out", result["detail"])

    def test_unknown_grader_raises(self) -> None:
        with self.assertRaises(tool.CliError):
            tool.grade_output("x", {"type": "does-not-exist"})

    def test_grade_none_output(self) -> None:
        result = tool.grade_output(None, {"type": "exact_match", "expected": "x"})
        self.assertFalse(result["passed"])


class HelperTests(unittest.TestCase):
    def test_extract_json_from_fence(self) -> None:
        self.assertEqual(tool.extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_extract_json_embedded(self) -> None:
        self.assertEqual(tool.extract_json('Here you go: {"a": 1} done'), {"a": 1})

    def test_extract_json_raises(self) -> None:
        with self.assertRaises(ValueError):
            tool.extract_json("no json here")

    def test_extract_numbers(self) -> None:
        self.assertEqual(tool.extract_numbers("1,000 and 42.5"), [1000.0, 42.5])

    def test_count_words(self) -> None:
        self.assertEqual(tool.count_words("one two three"), 3)

    def test_estimate_cost(self) -> None:
        cost = tool.estimate_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000)
        self.assertEqual(cost, "30.000000")  # $5 in + $25 out
        self.assertEqual(tool.estimate_cost_usd("claude-opus-4-8[1m]", 1_000_000, 0), "5.000000")
        self.assertIsNone(tool.estimate_cost_usd("unknown-model", 10, 10))
        self.assertIsNone(tool.estimate_cost_usd("claude-opus-4-8", None, 10))

    def test_base_model_id(self) -> None:
        self.assertEqual(tool.base_model_id("claude-opus-4-8[1m]"), "claude-opus-4-8")
        self.assertIsNone(tool.base_model_id(None))


class RunPipelineTests(unittest.TestCase):
    def test_sim_run_records_simulated_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            result = tool.run_oneshot(oneshot=oneshot, adapter_name="sim", model=None, data_dir=data_dir)
            self.assertTrue(result["benchmark"]["passed"])
            self.assertTrue(result["benchmark"]["simulated"])
            self.assertFalse(result["benchmark"]["benchmark_eligible"])
            records = tool.read_ledger(data_dir)  # re-validates everything
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"]["type"], "unavailable")
            self.assertEqual(records[0]["status"], "completed")
            self.assertEqual(records[0]["benchmark"]["oneshot_id"], "instruction-exact-word")

    def test_real_adapter_pass_records_native_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            result = tool.run_oneshot(
                oneshot=oneshot,
                adapter_name="claude-code",
                model=None,
                data_dir=data_dir,
                responder=fake_adapter("BANANA", input_tokens=120, output_tokens=8),
                provider_override="anthropic",
            )
            bench = result["benchmark"]
            self.assertTrue(bench["passed"])
            self.assertTrue(bench["benchmark_eligible"])
            self.assertEqual(bench["evidence_level"], "native_telemetry")
            self.assertEqual(bench["estimated_cost_usd"], tool.estimate_cost_usd("claude-opus-4-8", 120, 8))
            records = tool.read_ledger(data_dir)
            rec = records[0]
            self.assertEqual(rec["status"], "completed")
            self.assertEqual(rec["exit_code"], 0)
            self.assertEqual(rec["source"]["type"], "native_telemetry")
            self.assertEqual(rec["usage"]["input_tokens"], 120)
            self.assertEqual(rec["usage"]["output_tokens"], 8)
            self.assertEqual(rec["usage"]["tokens_consumed"], 128)
            self.assertTrue(tool.SHA256_PATTERN.fullmatch(rec["source"]["evidence_sha256"]))

    def test_real_adapter_fail_records_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            result = tool.run_oneshot(
                oneshot=oneshot,
                adapter_name="claude-code",
                model=None,
                data_dir=data_dir,
                responder=fake_adapter("not the word"),
                provider_override="anthropic",
            )
            self.assertFalse(result["benchmark"]["passed"])
            self.assertTrue(result["benchmark"]["benchmark_eligible"])
            rec = tool.read_ledger(data_dir)[0]
            self.assertEqual(rec["status"], "failed")
            self.assertEqual(rec["exit_code"], 1)

    def test_cached_tokens_are_clamped_to_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            tool.run_oneshot(
                oneshot=oneshot,
                adapter_name="claude-code",
                model=None,
                data_dir=data_dir,
                responder=fake_adapter("BANANA", input_tokens=10, output_tokens=2, cached_input_tokens=999),
                provider_override="anthropic",
            )
            rec = tool.read_ledger(data_dir)[0]
            self.assertEqual(rec["usage"]["cached_input_tokens"], 10)

    def test_adapter_error_records_incomplete(self) -> None:
        def boom(prompt, system, model, context):
            raise tool.AdapterError("network down")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            result = tool.run_oneshot(
                oneshot=oneshot,
                adapter_name="claude-code",
                model=None,
                data_dir=data_dir,
                responder=boom,
                provider_override="anthropic",
            )
            self.assertFalse(result["benchmark"]["passed"])
            self.assertIn("network down", result["benchmark"]["detail"])
            rec = tool.read_ledger(data_dir)[0]
            self.assertEqual(rec["status"], "incomplete")
            self.assertEqual(rec["source"]["type"], "unavailable")
            self.assertFalse(rec["benchmark"]["benchmark_eligible"])

    def test_adapter_unavailable_writes_no_record(self) -> None:
        def unavailable(prompt, system, model, context):
            raise tool.AdapterUnavailable("no key")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            with self.assertRaises(tool.AdapterUnavailable):
                tool.run_oneshot(
                    oneshot=oneshot,
                    adapter_name="claude-code",
                    model=None,
                    data_dir=data_dir,
                    responder=unavailable,
                    provider_override="anthropic",
                )
            self.assertEqual(tool.read_ledger(data_dir), [])


class BenchmarkValidationTests(unittest.TestCase):
    def base_record(self, **benchmark_overrides):
        benchmark = {
            "oneshot_id": "x",
            "category": "c",
            "adapter": "a",
            "grader": "exact_match",
            "evidence_level": "native_telemetry",
            "passed": True,
            "score": 1.0,
            "latency_ms": 10,
            "simulated": False,
            "benchmark_eligible": True,
        }
        benchmark.update(benchmark_overrides)
        return {"kind": "run", "status": "completed", "benchmark": benchmark}

    def validate(self, record):
        tool.validate_ledger_benchmark(record, Path("ledger"), 1)

    def test_valid_benchmark_passes(self) -> None:
        self.validate(self.base_record())  # no raise

    def test_benchmark_must_be_object(self) -> None:
        with self.assertRaises(tool.CliError):
            self.validate({"kind": "run", "status": "completed", "benchmark": "nope"})

    def test_benchmark_only_on_run(self) -> None:
        rec = self.base_record()
        rec["kind"] = "provider_usage_bucket"
        with self.assertRaises(tool.CliError):
            self.validate(rec)

    def test_missing_required_string(self) -> None:
        rec = self.base_record()
        del rec["benchmark"]["adapter"]
        with self.assertRaises(tool.CliError):
            self.validate(rec)

    def test_bad_evidence_level(self) -> None:
        with self.assertRaises(tool.CliError):
            self.validate(self.base_record(evidence_level="made-up"))

    def test_score_out_of_range(self) -> None:
        with self.assertRaises(tool.CliError):
            self.validate(self.base_record(score=1.5))

    def test_negative_latency(self) -> None:
        with self.assertRaises(tool.CliError):
            self.validate(self.base_record(latency_ms=-1))

    def test_eligible_requires_real_evidence(self) -> None:
        with self.assertRaises(tool.CliError):
            self.validate(self.base_record(evidence_level="unavailable", benchmark_eligible=True))

    def test_simulated_cannot_be_eligible(self) -> None:
        with self.assertRaises(tool.CliError):
            self.validate(self.base_record(evidence_level="simulated", simulated=True, benchmark_eligible=True))

    def test_passed_must_match_status(self) -> None:
        rec = self.base_record(passed=True)
        rec["status"] = "failed"
        with self.assertRaises(tool.CliError):
            self.validate(rec)
        rec2 = self.base_record(passed=False, benchmark_eligible=False)
        rec2["status"] = "completed"
        with self.assertRaises(tool.CliError):
            self.validate(rec2)

    def test_tampering_breaks_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            oneshot = tool.get_oneshot("instruction-exact-word")
            tool.run_oneshot(oneshot=oneshot, adapter_name="sim", model=None, data_dir=data_dir)
            ledger = tool.ledger_path(data_dir)
            lines = ledger.read_text(encoding="utf-8").splitlines()
            record = json.loads(lines[0])
            record["benchmark"]["score"] = 0.0  # tamper without recomputing the hash
            ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
            with self.assertRaises(tool.CliError):
                tool.read_ledger(data_dir)


class CliTests(unittest.TestCase):
    def run_cli(self, data_dir: Path, *args: str):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = tool.main(["--data-dir", str(data_dir), *args])
        return code, buf.getvalue()

    def test_list_and_show(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code, out = self.run_cli(data_dir, "oneshot", "list")
            self.assertEqual(code, 0)
            self.assertIn("code-is-palindrome", out)
            code, out = self.run_cli(data_dir, "oneshot", "list", "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out))
            code, out = self.run_cli(data_dir, "oneshot", "show", "code-fizzbuzz")
            self.assertEqual(code, 0)
            self.assertIn("fizzbuzz", out)

    def test_show_unknown_id_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self.run_cli(Path(tmp), "oneshot", "show", "no-such-id")
            self.assertEqual(code, 2)

    def test_run_sim_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code, out = self.run_cli(data_dir, "oneshot", "run", "math-multiply", "--adapter", "sim")
            self.assertEqual(code, 0)
            self.assertIn("PASS", out)
            self.assertEqual(len(tool.read_ledger(data_dir)), 1)

    def test_bench_sim_then_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            code, out = self.run_cli(data_dir, "oneshot", "bench", "--adapter", "sim", "--ids", "math-multiply,extract-capital")
            self.assertEqual(code, 0)
            self.assertIn("pass rate", out)
            self.assertEqual(len(tool.read_ledger(data_dir)), 2)
            code, _ = self.run_cli(data_dir, "verify")
            self.assertEqual(code, 0)

    def test_run_unknown_adapter_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = self.run_cli(Path(tmp), "oneshot", "run", "math-multiply", "--adapter", "nope")
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
