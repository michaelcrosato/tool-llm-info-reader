#!/usr/bin/env python3
"""Local LLM usage ledger.

This is intentionally dependency-free. It records local runs, imports provider
usage/cost exports, and summarizes usage over a time period without asking an
LLM to invent telemetry.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import datetime as dt
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from decimal import Decimal, DecimalException, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 1
DEFAULT_DATA_DIR = Path("data")
DEFAULT_LEDGER = Path("usage-ledger.jsonl")
DEFAULT_LOCK = Path("usage-ledger.lock")
UTC = dt.timezone.utc
DURATION_TIMESTAMP_TOLERANCE_MS = 1000
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RECORD_ID_PATTERN = re.compile(r"^rec_[a-f0-9]{16}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
LEDGER_KINDS = {"provider_cost_bucket", "provider_usage_bucket", "run"}
LEDGER_STATUSES = {"completed", "failed", "incomplete"}
SOURCE_TYPES = {
    "manual_attestation",
    "provider_export",
    "native_telemetry",
    "vendor_session_store",
    "proxy_log",
    "local_recorder",
    "unavailable",
    "legacy_self_reported",
}
BILLING_SOURCES = {"provider_cost_api", "provider_export", "manual_attestation", "unavailable"}
PROVIDER_BILLING_SOURCES = {"provider_cost_api", "provider_export"}
PROVIDER_BILLING_STRING_FIELDS = ("line_item", "project_id", "api_key_id")
TRUSTED_EXCLUDED_SOURCE_TYPES = {"manual_attestation", "unavailable", "legacy_self_reported"}
MANUAL_SOURCE_TYPES = {"manual_attestation", "unavailable"}
MANUAL_BILLING_SOURCES = BILLING_SOURCES - PROVIDER_BILLING_SOURCES
ADAPTER_EVIDENCE_SOURCE_TYPES = {"native_telemetry", "vendor_session_store", "proxy_log"}
ADAPTER_EVIDENCE_STRING_FIELDS = ("adapter", "adapter_version")
ADAPTER_EVIDENCE_SHA256_FIELDS = ("evidence_sha256",)
PROVIDER_EXPORT_STRING_FIELDS = ("file", "provider_object")
PROVIDER_EXPORT_SHA256_FIELDS = ("file_sha256", "import_key")
TOKEN_COMPONENT_FIELDS = (
    "input_tokens",
    "output_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
)
USAGE_VALUE_FIELDS = [
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
    "billed_tokens",
    "requests",
    "tokens_consumed",
]
HOST_STRING_FIELDS = (
    "hostname",
    "os",
    "os_release",
    "os_version",
    "machine",
    "processor",
    "python",
    "executable",
)


class CliError(Exception):
    """Expected CLI error with a clean message."""


def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def to_iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def epoch_seconds_to_utc(seconds: int, error_message: str) -> dt.datetime:
    try:
        return dt.datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise CliError(error_message) from exc


def parse_time(value: str | None, *, default: dt.datetime | None = None) -> dt.datetime:
    if value is None:
        if default is None:
            raise CliError("missing required time")
        return default

    raw = value.strip()
    if not raw:
        raise CliError("empty time value")
    if raw.lower() == "now":
        return now_utc()
    if raw.isdigit():
        return epoch_seconds_to_utc(
            int(raw),
            f"invalid time {value!r}; use ISO-8601, YYYY-MM-DD, epoch seconds, or now",
        )
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        raw = raw + "T00:00:00+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CliError(f"invalid time {value!r}; use ISO-8601, YYYY-MM-DD, epoch seconds, or now") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_duration(value: str) -> dt.timedelta:
    raw = value.strip().lower()
    if not raw:
        raise CliError("empty duration")
    suffixes = {
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
        "w": 7 * 24 * 60 * 60,
    }
    suffix = raw[-1]
    if suffix in suffixes:
        number = raw[:-1]
        try:
            amount = Decimal(number)
            seconds = amount * suffixes[suffix]
        except DecimalException as exc:
            raise CliError(f"invalid duration {value!r}") from exc
        return duration_from_decimal(seconds, value)
    try:
        return duration_from_decimal(Decimal(raw), value)
    except DecimalException as exc:
        raise CliError(f"invalid duration {value!r}; examples: 30m, 24h, 7d") from exc


def duration_from_decimal(seconds: Decimal, original: str) -> dt.timedelta:
    if not seconds.is_finite():
        raise CliError(f"invalid duration {original!r}; must be finite")
    if seconds <= 0:
        raise CliError(f"invalid duration {original!r}; must be > 0")
    try:
        return dt.timedelta(seconds=float(seconds))
    except (OverflowError, ValueError) as exc:
        raise CliError(f"invalid duration {original!r}; too large") from exc


def positive_int_or_none(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise CliError(f"{name} must be an integer") from exc
    if parsed < 0:
        raise CliError(f"{name} must be >= 0")
    return parsed


def decimal_or_none(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CliError(f"{name} must be a decimal number") from exc
    if not parsed.is_finite():
        raise CliError(f"{name} must be finite")
    if parsed < 0:
        raise CliError(f"{name} must be >= 0")
    return format(parsed, "f")


def ensure_data_dir(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "runs").mkdir(parents=True, exist_ok=True)


def ledger_path(data_dir: Path) -> Path:
    return data_dir / DEFAULT_LEDGER


def ledger_lock_path(data_dir: Path) -> Path:
    return data_dir / DEFAULT_LOCK


def run_lock_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / "runs" / f"{run_id}.lock"


def run_state_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / "runs" / f"{run_id}.json"


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def stable_json_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256_bytes(data)


def record_hash(record: dict[str, Any]) -> str:
    return stable_json_hash({key: value for key, value in record.items() if key != "record_hash"})


def refresh_record_hash(record: dict[str, Any]) -> dict[str, Any]:
    record["record_hash"] = record_hash(record)
    return record


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(f"invalid JSON in {path}: {exc}") from exc


def load_run_state(path: Path, run_id: str) -> dict[str, Any]:
    run = load_json_file(path)
    if not isinstance(run, dict):
        raise CliError(f"invalid run state at {path}: expected object")
    schema_version = run.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise CliError(f"invalid run state at {path}: field 'schema_version' must be an integer")
    if schema_version != SCHEMA_VERSION:
        raise CliError(
            f"invalid run state at {path}: unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )
    if run.get("run_id") != run_id:
        raise CliError(f"invalid run state at {path}: run_id mismatch")
    if run.get("status") not in {"running", "completed"}:
        raise CliError(f"invalid run state at {path}: status must be running or completed")
    provider = run.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise CliError(f"invalid run state at {path}: field 'provider' must be a non-empty string")
    if provider != provider.strip():
        raise CliError(f"invalid run state at {path}: field 'provider' must not have leading or trailing whitespace")
    model = run.get("model")
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            raise CliError(f"invalid run state at {path}: field 'model' must be a non-empty string or null")
        if model != model.strip():
            raise CliError(f"invalid run state at {path}: field 'model' must not have leading or trailing whitespace")
    started_at = run.get("started_at")
    if not isinstance(started_at, str) or not started_at:
        raise CliError(f"invalid run state at {path}: field 'started_at' must be a non-empty time string")
    try:
        parse_time(started_at)
    except CliError as exc:
        raise CliError(f"invalid run state at {path}: field 'started_at' is invalid: {exc}") from exc
    return run


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_ledger(data_dir: Path, records: Iterable[dict[str, Any]]) -> int:
    ensure_data_dir(data_dir)
    path = ledger_path(data_dir)
    pending = list(records)
    with exclusive_file_lock(ledger_lock_path(data_dir)):
        seen_import_keys, seen_run_ids, seen_record_ids = existing_ledger_identities(path)
        pending_record_ids: set[str] = set()
        pending_run_ids: set[str] = set()
        for index, record in enumerate(pending, 1):
            validate_ledger_record(record, path, index)
            key = import_key(record)
            if key and key in seen_import_keys:
                continue
            record_id = record["record_id"]
            if record_id in seen_record_ids or record_id in pending_record_ids:
                raise CliError(f"duplicate record_id in ledger: {record_id}")
            pending_record_ids.add(record_id)
            run_id = ledger_run_id(record)
            if run_id is not None:
                if run_id in seen_run_ids or run_id in pending_run_ids:
                    raise CliError(f"duplicate run_id in ledger: {run_id}")
                pending_run_ids.add(run_id)
        count = 0
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            for record in pending:
                key = import_key(record)
                if key and key in seen_import_keys:
                    continue
                fh.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
                seen_record_ids.add(record["record_id"])
                if key:
                    seen_import_keys.add(key)
                run_id = ledger_run_id(record)
                if run_id is not None:
                    seen_run_ids.add(run_id)
                count += 1
    return count


def ledger_run_id(record: dict[str, Any]) -> str | None:
    if record.get("kind") != "run":
        return None
    value = record.get("run_id")
    return value if isinstance(value, str) and value else None


def import_key(record: dict[str, Any]) -> str | None:
    source = record.get("source")
    if not isinstance(source, dict):
        return None
    value = source.get("import_key")
    return str(value) if value else None


def existing_import_keys(path: Path) -> set[str]:
    keys, _, _ = existing_ledger_identities(path)
    return keys


def existing_ledger_identities(path: Path) -> tuple[set[str], set[str], set[str]]:
    if not path.exists():
        return set(), set(), set()
    keys: set[str] = set()
    run_ids: set[str] = set()
    record_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CliError(f"invalid ledger JSON at {path}:{line_no}: {exc}") from exc
            validate_ledger_record(record, path, line_no)
            record_id = record["record_id"]
            if record_id in record_ids:
                raise CliError(f"duplicate record_id in ledger at {path}:{line_no}: {record_id}")
            record_ids.add(record_id)
            key = import_key(record)
            if key:
                if key in keys:
                    raise CliError(f"duplicate import_key in ledger at {path}:{line_no}: {key}")
                keys.add(key)
            run_id = ledger_run_id(record)
            if run_id is not None:
                if run_id in run_ids:
                    raise CliError(f"duplicate run_id in ledger at {path}:{line_no}: {run_id}")
                run_ids.add(run_id)
    return keys, run_ids, record_ids


def find_ledger_run_record(data_dir: Path, run_id: str) -> dict[str, Any] | None:
    for record in read_ledger(data_dir):
        if ledger_run_id(record) == run_id:
            return record
    return None


def ledger_record_error(path: Path, line_no: int, message: str) -> CliError:
    return CliError(f"invalid ledger record at {path}:{line_no}: {message}")


def validate_ledger_object_field(record: dict[str, Any], path: Path, line_no: int, field: str) -> dict[str, Any]:
    value = record.get(field)
    if not isinstance(value, dict):
        raise ledger_record_error(path, line_no, f"field {field!r} must be object")
    return value


def validate_ledger_required_string_field(record: dict[str, Any], path: Path, line_no: int, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ledger_record_error(path, line_no, f"field {field!r} must be a non-empty string")
    return value


def validate_ledger_time_field(record: dict[str, Any], path: Path, line_no: int, field: str) -> dt.datetime:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ledger_record_error(path, line_no, f"field {field!r} must be a non-empty time string")
    try:
        return parse_time(value)
    except CliError as exc:
        raise ledger_record_error(path, line_no, f"field {field!r} is invalid: {exc}") from exc


def validate_ledger_optional_nonnegative_int(
    mapping: dict[str, Any],
    path: Path,
    line_no: int,
    field: str,
    display_field: str | None = None,
) -> None:
    value = mapping.get(field)
    if value is None:
        return
    name = display_field or field
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ledger_record_error(path, line_no, f"field {name!r} must be a non-negative integer or null")


def validate_ledger_optional_decimal(
    mapping: dict[str, Any],
    path: Path,
    line_no: int,
    field: str,
    display_field: str | None = None,
) -> None:
    value = mapping.get(field)
    if value is None:
        return
    name = display_field or field
    if isinstance(value, bool):
        raise ledger_record_error(path, line_no, f"field {name!r} must be a non-negative decimal or null")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ledger_record_error(path, line_no, f"field {name!r} must be a non-negative decimal or null") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ledger_record_error(path, line_no, f"field {name!r} must be a non-negative decimal or null")


def validate_ledger_source(source: dict[str, Any], path: Path, line_no: int) -> None:
    source_type = source.get("type")
    if not isinstance(source_type, str) or not source_type:
        raise ledger_record_error(path, line_no, "field 'source.type' must be a non-empty string")
    if source_type not in SOURCE_TYPES:
        raise ledger_record_error(path, line_no, f"field 'source.type' has unsupported value {source_type!r}")
    duration_clock = source.get("duration_clock")
    if source_type == "local_recorder":
        if duration_clock != "monotonic":
            raise ledger_record_error(
                path,
                line_no,
                "field 'source.duration_clock' must be monotonic for local_recorder",
            )
        command = source.get("command")
        if not isinstance(command, list) or not command:
            raise ledger_record_error(
                path,
                line_no,
                "field 'source.command' must be a non-empty string array for local_recorder",
            )
        if not isinstance(command[0], str) or not command[0]:
            raise ledger_record_error(path, line_no, "field 'source.command[0]' must be a non-empty string")
        if any(not isinstance(part, str) for part in command):
            raise ledger_record_error(path, line_no, "field 'source.command' must contain only strings")
    elif duration_clock is not None:
        raise ledger_record_error(
            path,
            line_no,
            "field 'source.duration_clock' can only be set when source.type is local_recorder",
        )
    if source_type == "provider_export":
        for field in PROVIDER_EXPORT_STRING_FIELDS:
            value = source.get(field)
            if not isinstance(value, str) or not value:
                raise ledger_record_error(
                    path,
                    line_no,
                    f"field 'source.{field}' must be a non-empty string for provider_export",
                )
        for field in PROVIDER_EXPORT_SHA256_FIELDS:
            value = source.get(field)
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise ledger_record_error(
                    path,
                    line_no,
                    f"field 'source.{field}' must be a sha256 hex string for provider_export",
                )
    if source_type in ADAPTER_EVIDENCE_SOURCE_TYPES:
        for field in ADAPTER_EVIDENCE_STRING_FIELDS:
            value = source.get(field)
            if not isinstance(value, str) or not value:
                raise ledger_record_error(
                    path,
                    line_no,
                    f"field 'source.{field}' must be a non-empty string for {source_type}",
                )
        for field in ADAPTER_EVIDENCE_SHA256_FIELDS:
            value = source.get(field)
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
                raise ledger_record_error(
                    path,
                    line_no,
                    f"field 'source.{field}' must be a sha256 hex string for {source_type}",
                )


def validate_ledger_billing(billing: dict[str, Any], source_type: str, path: Path, line_no: int) -> None:
    source = billing.get("source")
    if not isinstance(source, str) or not source:
        raise ledger_record_error(path, line_no, "field 'billing.source' must be a non-empty string")
    if source not in BILLING_SOURCES:
        raise ledger_record_error(path, line_no, f"field 'billing.source' has unsupported value {source!r}")
    if source in PROVIDER_BILLING_SOURCES and source_type != "provider_export":
        raise ledger_record_error(path, line_no, f"field 'billing.source' cannot be {source!r} unless source.type is provider_export")
    validate_ledger_optional_decimal(billing, path, line_no, "actual_cost_usd", "billing.actual_cost_usd")
    validate_ledger_optional_decimal(billing, path, line_no, "quantity", "billing.quantity")
    if billing.get("actual_cost_usd") is None:
        if source != "unavailable":
            raise ledger_record_error(
                path,
                line_no,
                "field 'billing.source' must be unavailable when actual_cost_usd is null",
            )
        if billing.get("currency") is not None:
            raise ledger_record_error(
                path,
                line_no,
                "field 'billing.currency' must be null when actual_cost_usd is null",
            )
    else:
        if source == "unavailable":
            raise ledger_record_error(path, line_no, "field 'billing.source' cannot be unavailable when actual_cost_usd is set")
        if billing.get("currency") != "usd":
            raise ledger_record_error(path, line_no, "field 'billing.currency' must be usd when actual_cost_usd is set")
    for field in PROVIDER_BILLING_STRING_FIELDS:
        value = billing.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ledger_record_error(path, line_no, f"field 'billing.{field}' must be a non-empty string or null")
        if value != value.strip():
            raise ledger_record_error(
                path,
                line_no,
                f"field 'billing.{field}' must not have leading or trailing whitespace",
            )


def usage_has_values(usage: dict[str, Any]) -> bool:
    return any(usage.get(field) is not None for field in USAGE_VALUE_FIELDS)


def validate_ledger_usage(usage: dict[str, Any], path: Path, line_no: int) -> None:
    for field in USAGE_VALUE_FIELDS:
        validate_ledger_optional_nonnegative_int(usage, path, line_no, field, f"usage.{field}")
    component_total = sum_ints(*(usage.get(field) for field in TOKEN_COMPONENT_FIELDS))
    if component_total is not None and usage.get("tokens_consumed") != component_total:
        raise ledger_record_error(
            path,
            line_no,
            "field 'usage.tokens_consumed' must equal the sum of input/output/audio token fields",
        )


def validate_ledger_host(host: dict[str, Any], path: Path, line_no: int) -> None:
    for field in HOST_STRING_FIELDS:
        if not isinstance(host.get(field), str):
            raise ledger_record_error(path, line_no, f"field 'host.{field}' must be a string")


def validate_ledger_schema_version(record: dict[str, Any], path: Path, line_no: int) -> None:
    schema_version = record.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ledger_record_error(path, line_no, "field 'schema_version' must be an integer")
    if schema_version != SCHEMA_VERSION:
        raise ledger_record_error(
            path,
            line_no,
            f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}",
        )


def validate_ledger_metadata(record: dict[str, Any], path: Path, line_no: int) -> None:
    record_id = validate_ledger_required_string_field(record, path, line_no, "record_id")
    if not RECORD_ID_PATTERN.fullmatch(record_id):
        raise ledger_record_error(path, line_no, "field 'record_id' must be a valid record id")
    kind = validate_ledger_required_string_field(record, path, line_no, "kind")
    if kind not in LEDGER_KINDS:
        raise ledger_record_error(path, line_no, f"field 'kind' has unsupported value {kind!r}")
    provider = validate_ledger_required_string_field(record, path, line_no, "provider")
    if provider != provider.strip():
        raise ledger_record_error(path, line_no, "field 'provider' must not have leading or trailing whitespace")
    model = record.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ledger_record_error(path, line_no, "field 'model' must be a non-empty string or null")
    if isinstance(model, str) and model != model.strip():
        raise ledger_record_error(path, line_no, "field 'model' must not have leading or trailing whitespace")
    status = validate_ledger_required_string_field(record, path, line_no, "status")
    if status not in LEDGER_STATUSES:
        raise ledger_record_error(path, line_no, f"field 'status' has unsupported value {status!r}")
    if kind != "run" and status != "completed":
        raise ledger_record_error(path, line_no, "field 'status' must be completed unless kind is run")
    run_id = record.get("run_id")
    if run_id is not None:
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            raise ledger_record_error(path, line_no, "field 'run_id' must be null or a valid run id")
        if kind != "run":
            raise ledger_record_error(path, line_no, "field 'run_id' must be null unless kind is run")
    validate_ledger_optional_nonnegative_int(record, path, line_no, "exit_code")
    exit_code = record.get("exit_code")
    if kind != "run" and exit_code is not None:
        raise ledger_record_error(path, line_no, "field 'exit_code' must be null unless kind is run")
    if status == "completed" and exit_code not in {None, 0}:
        raise ledger_record_error(path, line_no, "field 'status' cannot be completed when exit_code is nonzero")
    if status == "failed" and exit_code == 0:
        raise ledger_record_error(path, line_no, "field 'status' cannot be failed when exit_code is zero")
    validate_ledger_time_field(record, path, line_no, "created_at")
    host = validate_ledger_object_field(record, path, line_no, "host")
    validate_ledger_host(host, path, line_no)
    notes = record.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ledger_record_error(path, line_no, "field 'notes' must be a string or null")


def validate_ledger_duration(
    record: dict[str, Any],
    started_at: dt.datetime,
    finished_at: dt.datetime,
    source: dict[str, Any],
    path: Path,
    line_no: int,
) -> None:
    validate_ledger_optional_nonnegative_int(record, path, line_no, "duration_ms")
    duration_ms = record.get("duration_ms")
    if duration_ms is None:
        raise ledger_record_error(path, line_no, "field 'duration_ms' must be a non-negative integer")
    if source.get("type") == "local_recorder" and source.get("duration_clock") == "monotonic":
        return
    wall_duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    if abs(duration_ms - wall_duration_ms) > DURATION_TIMESTAMP_TOLERANCE_MS:
        raise ledger_record_error(
            path,
            line_no,
            "field 'duration_ms' must match started_at and finished_at within 1000ms",
        )


def validate_ledger_kind_source(record: dict[str, Any], source: dict[str, Any], path: Path, line_no: int) -> None:
    kind = record["kind"]
    source_type = source["type"]
    if kind in {"provider_usage_bucket", "provider_cost_bucket"} and source_type != "provider_export":
        raise ledger_record_error(
            path,
            line_no,
            f"field 'source.type' must be provider_export for {kind}",
        )


def validate_provider_export_kind(record: dict[str, Any], source: dict[str, Any], path: Path, line_no: int) -> None:
    kind = record["kind"]
    provider = record["provider"]
    provider_object = source.get("provider_object")
    if provider != "openai":
        raise ledger_record_error(
            path,
            line_no,
            "field 'provider' must be openai when source.type is provider_export",
        )
    if kind == "provider_usage_bucket":
        if not isinstance(provider_object, str) or not provider_object.startswith("organization.usage."):
            raise ledger_record_error(
                path,
                line_no,
                "field 'source.provider_object' must be an organization.usage.* object for provider_usage_bucket",
            )
        return
    if kind == "provider_cost_bucket":
        if provider_object != "organization.costs.result":
            raise ledger_record_error(
                path,
                line_no,
                "field 'source.provider_object' must be organization.costs.result for provider_cost_bucket",
            )
        return
    raise ledger_record_error(path, line_no, "field 'source.type' cannot be provider_export unless kind is a provider bucket")


def validate_provider_export_payload(
    record: dict[str, Any],
    usage: dict[str, Any],
    billing: dict[str, Any],
    path: Path,
    line_no: int,
) -> None:
    kind = record["kind"]
    if kind == "provider_usage_bucket" and billing.get("source") in PROVIDER_BILLING_SOURCES:
        raise ledger_record_error(
            path,
            line_no,
            "field 'billing.source' cannot be provider billing for provider_usage_bucket",
        )
    if kind == "provider_cost_bucket" and usage_has_values(usage):
        raise ledger_record_error(
            path,
            line_no,
            "usage values must be null for provider_cost_bucket",
        )


def validate_ledger_record(record: Any, path: Path, line_no: int) -> None:
    if not isinstance(record, dict):
        raise CliError(f"invalid ledger record at {path}:{line_no}: expected object")
    stored_hash = record.get("record_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        raise CliError(f"invalid ledger record at {path}:{line_no}: missing record_hash")
    expected_hash = record_hash(record)
    if stored_hash != expected_hash:
        raise CliError(f"invalid ledger record at {path}:{line_no}: record_hash mismatch")
    validate_ledger_schema_version(record, path, line_no)
    validate_ledger_metadata(record, path, line_no)
    source = validate_ledger_object_field(record, path, line_no, "source")
    validate_ledger_source(source, path, line_no)
    started_at = validate_ledger_time_field(record, path, line_no, "started_at")
    finished_at = validate_ledger_time_field(record, path, line_no, "finished_at")
    if finished_at < started_at:
        raise ledger_record_error(path, line_no, "finished_at cannot be earlier than started_at")
    validate_ledger_duration(record, started_at, finished_at, source, path, line_no)
    usage = validate_ledger_object_field(record, path, line_no, "usage")
    billing = validate_ledger_object_field(record, path, line_no, "billing")
    source_type = source["type"]
    validate_ledger_kind_source(record, source, path, line_no)
    if source_type == "provider_export":
        validate_provider_export_kind(record, source, path, line_no)
    validate_ledger_billing(billing, source_type, path, line_no)
    if source_type == "unavailable" and usage_has_values(usage):
        raise ledger_record_error(path, line_no, "usage values must be null when source.type is unavailable")
    if source_type == "local_recorder":
        if usage_has_values(usage):
            raise ledger_record_error(path, line_no, "usage values must be null when source.type is local_recorder")
        if billing.get("source") != "unavailable":
            raise ledger_record_error(
                path,
                line_no,
                "field 'billing.source' must be unavailable when source.type is local_recorder",
            )
    validate_ledger_usage(usage, path, line_no)
    if source_type == "provider_export":
        validate_provider_export_payload(record, usage, billing, path, line_no)


def read_ledger(data_dir: Path) -> list[dict[str, Any]]:
    path = ledger_path(data_dir)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    seen_import_keys: set[str] = set()
    seen_run_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CliError(f"invalid ledger JSON at {path}:{line_no}: {exc}") from exc
            validate_ledger_record(record, path, line_no)
            record_id = record["record_id"]
            if record_id in seen_record_ids:
                raise CliError(f"duplicate record_id in ledger at {path}:{line_no}: {record_id}")
            seen_record_ids.add(record_id)
            key = import_key(record)
            if key:
                if key in seen_import_keys:
                    raise CliError(f"duplicate import_key in ledger at {path}:{line_no}: {key}")
                seen_import_keys.add(key)
            run_id = ledger_run_id(record)
            if run_id is not None:
                if run_id in seen_run_ids:
                    raise CliError(f"duplicate run_id in ledger at {path}:{line_no}: {run_id}")
                seen_run_ids.add(run_id)
            records.append(record)
    return records


def system_snapshot() -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "os": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "executable": sys.executable,
    }


def blank_usage() -> dict[str, Any]:
    return {
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
        "input_audio_tokens": None,
        "output_audio_tokens": None,
        "billed_tokens": None,
        "requests": None,
        "tokens_consumed": None,
    }


def make_usage(args: argparse.Namespace) -> dict[str, Any]:
    usage = blank_usage()
    usage["input_tokens"] = positive_int_or_none(getattr(args, "input_tokens", None), "--input-tokens")
    usage["output_tokens"] = positive_int_or_none(getattr(args, "output_tokens", None), "--output-tokens")
    usage["cached_input_tokens"] = positive_int_or_none(
        getattr(args, "cached_input_tokens", None), "--cached-input-tokens"
    )
    usage["input_audio_tokens"] = positive_int_or_none(
        getattr(args, "input_audio_tokens", None), "--input-audio-tokens"
    )
    usage["output_audio_tokens"] = positive_int_or_none(
        getattr(args, "output_audio_tokens", None), "--output-audio-tokens"
    )
    usage["billed_tokens"] = positive_int_or_none(getattr(args, "billed_tokens", None), "--billed-tokens")
    usage["requests"] = positive_int_or_none(getattr(args, "requests", None), "--requests")
    usage["tokens_consumed"] = sum_ints(
        usage.get("input_tokens"),
        usage.get("output_tokens"),
        usage.get("input_audio_tokens"),
        usage.get("output_audio_tokens"),
    )
    return usage


def make_billing(args: argparse.Namespace) -> dict[str, Any]:
    cost = decimal_or_none(getattr(args, "cost_usd", None), "--cost-usd")
    billing_source = getattr(args, "billing_source", None)
    if billing_source is not None and billing_source not in MANUAL_BILLING_SOURCES:
        raise CliError(f"--billing-source must be one of: {', '.join(sorted(MANUAL_BILLING_SOURCES))}")
    if cost is None and billing_source not in {None, "unavailable"}:
        raise CliError("--billing-source requires --cost-usd unless it is unavailable")
    if cost is not None and billing_source == "unavailable":
        raise CliError("--billing-source cannot be unavailable when --cost-usd is set")
    return {
        "actual_cost_usd": cost,
        "currency": "usd" if cost is not None else None,
        "source": billing_source or ("manual_attestation" if cost is not None else "unavailable"),
    }


def sum_ints(*values: Any) -> int | None:
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        total += int(value)
        seen = True
    return total if seen else None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise CliError("--run-id must be 1-128 chars: letters, numbers, dot, underscore, or hyphen")
    return run_id


def validate_provider(provider: str) -> str:
    if not isinstance(provider, str) or not provider.strip():
        raise CliError("--provider must be a non-empty string")
    return provider.strip()


def normalize_model(model: Any) -> str | None:
    if model is None:
        return None
    if not isinstance(model, str):
        raise CliError("model must be a string or null")
    model = model.strip()
    return model or None


def normalize_cli_model(model: str | None) -> str | None:
    normalized = normalize_model(model)
    if model is not None and normalized is None:
        raise CliError("--model must be a non-empty string when provided")
    return normalized


def make_record(
    *,
    kind: str,
    provider: str,
    model: str | None,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    usage: dict[str, Any] | None = None,
    billing: dict[str, Any] | None = None,
    source_type: str,
    source_detail: dict[str, Any] | None = None,
    status: str = "completed",
    run_id: str | None = None,
    exit_code: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    source = {"type": source_type}
    if source_detail:
        source.update(source_detail)
    usage = usage or blank_usage()
    if source_type == "unavailable" and usage_has_values(usage):
        raise CliError("usage values cannot be set when --source is unavailable")
    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": new_id("rec"),
        "kind": kind,
        "run_id": run_id,
        "provider": provider,
        "model": normalize_model(model),
        "started_at": to_iso(started_at),
        "finished_at": to_iso(finished_at),
        "duration_ms": duration_ms,
        "status": status,
        "exit_code": exit_code,
        "usage": usage,
        "billing": billing
        or {
            "actual_cost_usd": None,
            "currency": None,
            "source": "unavailable",
        },
        "source": source,
        "host": system_snapshot(),
        "notes": notes,
        "created_at": to_iso(now_utc()),
    }
    return refresh_record_hash(record)


def add_usage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input-tokens", help="Observed text input tokens")
    parser.add_argument("--output-tokens", help="Observed text output tokens")
    parser.add_argument("--cached-input-tokens", help="Observed cached text input tokens")
    parser.add_argument("--input-audio-tokens", help="Observed audio input tokens")
    parser.add_argument("--output-audio-tokens", help="Observed audio output tokens")
    parser.add_argument("--billed-tokens", help="Provider-billed token count, if directly known")
    parser.add_argument("--requests", help="Number of model requests")
    parser.add_argument("--cost-usd", help="Actual USD cost, if provider-reconciled or manually attested")
    parser.add_argument(
        "--source",
        default="manual_attestation",
        choices=sorted(MANUAL_SOURCE_TYPES),
        help="Where manually entered model/usage values came from",
    )
    parser.add_argument(
        "--billing-source",
        choices=sorted(MANUAL_BILLING_SOURCES),
        help="Where cost data came from",
    )


def command_start(args: argparse.Namespace) -> int:
    provider = validate_provider(args.provider)
    data_dir = args.data_dir
    ensure_data_dir(data_dir)
    run_id = validate_run_id(args.run_id) if args.run_id else new_id("run")
    with exclusive_file_lock(run_lock_path(data_dir, run_id)):
        run_path = run_state_path(data_dir, run_id)
        if run_path.exists():
            raise CliError(f"run already exists: {run_id}")
        if find_ledger_run_record(data_dir, run_id) is not None:
            raise CliError(f"duplicate run_id in ledger: {run_id}")
        started_at = parse_time(args.started_at, default=now_utc())
        run = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "provider": provider,
            "model": normalize_cli_model(args.model),
            "started_at": to_iso(started_at),
            "status": "running",
            "source": {"type": "local_recorder"},
            "host": system_snapshot(),
            "notes": args.notes,
            "created_at": to_iso(now_utc()),
        }
        atomic_write_json(run_path, run)
    print(json.dumps({"run_id": run_id, "path": str(run_path), "started_at": run["started_at"]}, indent=2))
    return 0


def command_finish(args: argparse.Namespace) -> int:
    data_dir = args.data_dir
    run_id = validate_run_id(args.run_id)
    with exclusive_file_lock(run_lock_path(data_dir, run_id)):
        run_path = run_state_path(data_dir, run_id)
        run = load_run_state(run_path, run_id)
        existing_record = find_ledger_run_record(data_dir, run_id)
        if existing_record is not None:
            run["status"] = "completed"
            run["finished_at"] = existing_record["finished_at"]
            run["record_id"] = existing_record["record_id"]
            atomic_write_json(run_path, run)
            print(
                json.dumps(
                    {
                        "appended": 0,
                        "record_id": existing_record["record_id"],
                        "ledger": str(ledger_path(data_dir)),
                    },
                    indent=2,
                )
            )
            return 0
        if run.get("status") == "completed":
            raise CliError(f"run is already completed but no ledger record exists: {args.run_id}")

        started_at = parse_time(run.get("started_at"))
        finished_at = parse_time(args.finished_at, default=now_utc())
        if finished_at < started_at:
            raise CliError("finished time cannot be earlier than started time")

        if args.provider is not None:
            provider = validate_provider(args.provider)
        else:
            provider = run["provider"]
        if args.model is not None:
            model = normalize_cli_model(args.model)
        else:
            model = normalize_model(run.get("model"))
        exit_code = positive_int_or_none(args.exit_code, "--exit-code") if args.exit_code is not None else None
        record = make_record(
            kind="run",
            provider=provider,
            model=model,
            started_at=started_at,
            finished_at=finished_at,
            usage=make_usage(args),
            billing=make_billing(args),
            source_type=args.source,
            source_detail={"run_file": str(run_path)},
            status="completed" if exit_code in {None, 0} else "failed",
            run_id=run_id,
            exit_code=exit_code,
            notes=args.notes or run.get("notes"),
        )
        append_ledger(data_dir, [record])
        run["status"] = "completed"
        run["finished_at"] = record["finished_at"]
        run["record_id"] = record["record_id"]
        atomic_write_json(run_path, run)
    print(json.dumps({"appended": 1, "record_id": record["record_id"], "ledger": str(ledger_path(data_dir))}, indent=2))
    return 0


def command_record(args: argparse.Namespace) -> int:
    provider = validate_provider(args.provider)
    started_at = parse_time(args.started_at, default=now_utc())
    finished_at = parse_time(args.finished_at, default=now_utc())
    if finished_at < started_at:
        raise CliError("finished time cannot be earlier than started time")
    exit_code = positive_int_or_none(args.exit_code, "--exit-code") if args.exit_code is not None else None
    run_id = validate_run_id(args.run_id) if args.run_id else None
    lock = exclusive_file_lock(run_lock_path(args.data_dir, run_id)) if run_id is not None else nullcontext()
    with lock:
        if run_id is not None and run_state_path(args.data_dir, run_id).exists():
            raise CliError(f"run already exists: {run_id}")
        record = make_record(
            kind="run",
            provider=provider,
            model=normalize_cli_model(args.model),
            started_at=started_at,
            finished_at=finished_at,
            usage=make_usage(args),
            billing=make_billing(args),
            source_type=args.source,
            source_detail=None,
            status=args.status or ("completed" if exit_code in {None, 0} else "failed"),
            run_id=run_id,
            exit_code=exit_code,
            notes=args.notes,
        )
        append_ledger(args.data_dir, [record])
    print(json.dumps({"appended": 1, "record_id": record["record_id"], "ledger": str(ledger_path(args.data_dir))}, indent=2))
    return 0


def command_wrap(args: argparse.Namespace) -> int:
    if not args.command:
        raise CliError("wrap requires a command after --")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise CliError("wrap requires a command after --")
    cwd = Path(args.cwd) if args.cwd else None
    if cwd is not None and not cwd.is_dir():
        raise CliError(f"--cwd must be an existing directory: {cwd}")
    provider = validate_provider(args.provider)
    model = normalize_cli_model(args.model)
    run_id = validate_run_id(args.run_id) if args.run_id else new_id("run")
    with exclusive_file_lock(run_lock_path(args.data_dir, run_id)):
        if run_state_path(args.data_dir, run_id).exists():
            raise CliError(f"run already exists: {run_id}")
        if find_ledger_run_record(args.data_dir, run_id) is not None:
            raise CliError(f"duplicate run_id in ledger: {run_id}")
        started_at = now_utc()
        start_monotonic = time.perf_counter_ns()
        try:
            completed = subprocess.run(command, cwd=str(cwd) if cwd is not None else None)
            exit_code = completed.returncode
        except FileNotFoundError as exc:
            exit_code = 127
            finished_at = now_utc()
            duration_ms = int((time.perf_counter_ns() - start_monotonic) / 1_000_000)
            record = make_record(
                kind="run",
                provider=provider,
                model=model,
                started_at=started_at,
                finished_at=finished_at,
                usage=blank_usage(),
                billing=None,
                source_type="local_recorder",
                source_detail={"command": command, "duration_clock": "monotonic"},
                status="failed",
                run_id=run_id,
                exit_code=exit_code,
                notes=args.notes or str(exc),
            )
            record["duration_ms"] = duration_ms
            record["usage"]["unavailable_reason"] = "no usage telemetry source was attached to this wrapped command"
            refresh_record_hash(record)
            append_ledger(args.data_dir, [record])
            print(
                json.dumps(
                    {"record_id": record["record_id"], "exit_code": exit_code, "ledger": str(ledger_path(args.data_dir))},
                    indent=2,
                )
            )
            print(f"command not found: {command[0]}", file=sys.stderr)
            return exit_code

        finished_at = now_utc()
        duration_ms = int((time.perf_counter_ns() - start_monotonic) / 1_000_000)
        record = make_record(
            kind="run",
            provider=provider,
            model=model,
            started_at=started_at,
            finished_at=finished_at,
            usage=blank_usage(),
            billing=None,
            source_type="local_recorder",
            source_detail={"command": command, "duration_clock": "monotonic"},
            status="completed" if exit_code == 0 else "failed",
            run_id=run_id,
            exit_code=exit_code,
            notes=args.notes,
        )
        record["duration_ms"] = duration_ms
        record["usage"]["unavailable_reason"] = "no usage telemetry source was attached to this wrapped command"
        refresh_record_hash(record)
        append_ledger(args.data_dir, [record])
    print(json.dumps({"record_id": record["record_id"], "exit_code": exit_code, "ledger": str(ledger_path(args.data_dir))}, indent=2))
    return exit_code


def openai_bucket_times(bucket: dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    start = strict_required_int(bucket, "start_time", "OpenAI bucket")
    end = strict_required_int(bucket, "end_time", "OpenAI bucket")
    if end <= start:
        raise CliError("OpenAI bucket end_time must be after start_time")
    return (
        epoch_seconds_to_utc(start, "OpenAI bucket field 'start_time' is outside supported timestamp range"),
        epoch_seconds_to_utc(end, "OpenAI bucket field 'end_time' is outside supported timestamp range"),
    )


def strict_required_int(mapping: dict[str, Any], field: str, label: str) -> int:
    if field not in mapping or mapping[field] is None:
        raise CliError(f"{label} field {field!r} is required")
    value = mapping[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise CliError(f"{label} field {field!r} must be a non-negative integer")
    if value < 0:
        raise CliError(f"{label} field {field!r} must be >= 0")
    return value


def strict_optional_int(mapping: dict[str, Any], field: str) -> int | None:
    if field not in mapping or mapping[field] is None:
        return None
    value = mapping[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise CliError(f"OpenAI usage field {field!r} must be a non-negative integer when present")
    if value < 0:
        raise CliError(f"OpenAI usage field {field!r} must be >= 0")
    return value


def strict_openai_cost_amount(result: dict[str, Any]) -> tuple[str | None, str | None]:
    amount = result.get("amount")
    if not isinstance(amount, dict):
        raise CliError("OpenAI cost result missing amount object")

    currency = amount.get("currency")
    if not isinstance(currency, str) or not currency:
        raise CliError("OpenAI cost amount.currency must be a non-empty string")
    if currency != "usd":
        raise CliError("OpenAI cost amount.currency must be usd")

    if "value" not in amount or amount["value"] is None:
        raise CliError("OpenAI cost amount.value is required")
    value = amount["value"]
    if isinstance(value, bool):
        raise CliError("OpenAI cost amount.value must be a decimal number when present")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise CliError("OpenAI cost amount.value must be a decimal number when present") from exc
    if not parsed.is_finite():
        raise CliError("OpenAI cost amount.value must be finite")
    if parsed < 0:
        raise CliError("OpenAI cost amount.value must be >= 0")
    return format(parsed, "f"), currency


def normalize_openai_usage_result(result: dict[str, Any]) -> dict[str, Any]:
    input_tokens = strict_optional_int(result, "input_tokens")
    output_tokens = strict_optional_int(result, "output_tokens")
    input_audio_tokens = strict_optional_int(result, "input_audio_tokens")
    output_audio_tokens = strict_optional_int(result, "output_audio_tokens")
    requests = strict_optional_int(result, "num_model_requests")
    if requests is None:
        requests = strict_optional_int(result, "num_requests")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": strict_optional_int(result, "input_cached_tokens"),
        "input_audio_tokens": input_audio_tokens,
        "output_audio_tokens": output_audio_tokens,
        "billed_tokens": None,
        "requests": requests,
        "tokens_consumed": sum_ints(input_tokens, output_tokens, input_audio_tokens, output_audio_tokens),
    }


def strict_openai_model(result: dict[str, Any]) -> str | None:
    if "model" not in result or result["model"] is None:
        return None
    model = result["model"]
    if not isinstance(model, str) or not model.strip():
        raise CliError("OpenAI usage field 'model' must be a non-empty string when present")
    if model != model.strip():
        raise CliError("OpenAI usage field 'model' must not have leading or trailing whitespace")
    return model


def source_file_detail(path: Path) -> dict[str, Any]:
    return {
        "file": str(path),
        "file_sha256": file_sha256(path),
    }


def provider_import_key(
    provider: str,
    kind: str,
    started_at: dt.datetime,
    finished_at: dt.datetime,
    result: dict[str, Any],
) -> str:
    return stable_json_hash(
        {
            "provider": provider,
            "kind": kind,
            "started_at": to_iso(started_at),
            "finished_at": to_iso(finished_at),
            "result": result,
        }
    )


def iter_openai_buckets(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict) and payload.get("object") == "page" and isinstance(payload.get("data"), list):
        yield from payload["data"]
        return
    if isinstance(payload, list):
        yield from payload
        return
    raise CliError("expected an OpenAI page object with data[] or a raw bucket array")


def openai_bucket_results(bucket: Any) -> list[Any]:
    if not isinstance(bucket, dict):
        raise CliError("OpenAI bucket must be an object")
    results = bucket.get("results")
    if not isinstance(results, list):
        raise CliError("OpenAI bucket results must be an array")
    return results


def is_openai_bucket_like(bucket: Any) -> bool:
    return isinstance(bucket, dict) and (
        bucket.get("object") == "bucket"
        or "start_time" in bucket
        or "end_time" in bucket
        or "results" in bucket
    )


def openai_result_object(result: dict[str, Any]) -> str:
    value = result.get("object")
    if not isinstance(value, str) or not value:
        raise CliError("OpenAI bucket result field 'object' must be a non-empty string")
    if value.startswith("organization.usage.") or value == "organization.costs.result":
        return value
    raise CliError(f"unsupported OpenAI bucket result object {value!r}")


def build_openai_usage_records(path: Path, default_model: str | None, notes: str | None) -> list[dict[str, Any]]:
    payload = load_json_file(path)
    records: list[dict[str, Any]] = []
    detail = source_file_detail(path)
    saw_cost_result = False
    for bucket in iter_openai_buckets(payload):
        results = openai_bucket_results(bucket)
        started_at, finished_at = openai_bucket_times(bucket)
        for result in results:
            if not isinstance(result, dict):
                raise CliError("OpenAI usage result must be an object")
            object_name = openai_result_object(result)
            if not object_name.startswith("organization.usage."):
                saw_cost_result = True
                continue
            usage = normalize_openai_usage_result(result)
            if usage["tokens_consumed"] is None and usage["requests"] is None:
                raise CliError("OpenAI usage result must include token or request fields")
            records.append(
                make_record(
                    kind="provider_usage_bucket",
                    provider="openai",
                    model=strict_openai_model(result) or default_model,
                    started_at=started_at,
                    finished_at=finished_at,
                    usage=usage,
                    billing=None,
                    source_type="provider_export",
                    source_detail={
                        **detail,
                        "provider_object": object_name,
                        "import_key": provider_import_key(
                            "openai",
                            "provider_usage_bucket",
                            started_at,
                            finished_at,
                            result,
                        ),
                    },
                    status="completed",
                    notes=notes,
                )
            )
    if not records and saw_cost_result:
        raise CliError("OpenAI usage import found only cost results; use import-openai-costs")
    return records


def command_import_openai_usage(args: argparse.Namespace) -> int:
    records = build_openai_usage_records(args.file, args.default_model, args.notes)
    count = append_ledger(args.data_dir, records)
    print(json.dumps({"appended": count, "ledger": str(ledger_path(args.data_dir))}, indent=2))
    return 0


def build_openai_cost_records(path: Path, notes: str | None) -> list[dict[str, Any]]:
    payload = load_json_file(path)
    records: list[dict[str, Any]] = []
    detail = source_file_detail(path)
    saw_usage_result = False
    for bucket in iter_openai_buckets(payload):
        results = openai_bucket_results(bucket)
        started_at, finished_at = openai_bucket_times(bucket)
        for result in results:
            if not isinstance(result, dict):
                raise CliError("OpenAI cost result must be an object")
            object_name = openai_result_object(result)
            if object_name != "organization.costs.result":
                saw_usage_result = True
                continue
            cost, currency = strict_openai_cost_amount(result)
            records.append(
                make_record(
                    kind="provider_cost_bucket",
                    provider="openai",
                    model=None,
                    started_at=started_at,
                    finished_at=finished_at,
                    usage=blank_usage(),
                    billing={
                        "actual_cost_usd": cost,
                        "currency": currency,
                        "source": "provider_cost_api",
                        "line_item": result.get("line_item"),
                        "project_id": result.get("project_id"),
                        "api_key_id": result.get("api_key_id"),
                        "quantity": result.get("quantity"),
                    },
                    source_type="provider_export",
                    source_detail={
                        **detail,
                        "provider_object": "organization.costs.result",
                        "import_key": provider_import_key(
                            "openai",
                            "provider_cost_bucket",
                            started_at,
                            finished_at,
                            result,
                        ),
                    },
                    status="completed",
                    notes=notes,
                )
            )
    if not records and saw_usage_result:
        raise CliError("OpenAI cost import found only usage results; use import-openai-usage")
    return records


def command_import_openai_costs(args: argparse.Namespace) -> int:
    records = build_openai_cost_records(args.file, args.notes)
    count = append_ledger(args.data_dir, records)
    print(json.dumps({"appended": count, "ledger": str(ledger_path(args.data_dir))}, indent=2))
    return 0


def record_overlaps(record: dict[str, Any], start: dt.datetime, end: dt.datetime) -> bool:
    rec_start = parse_time(record.get("started_at"))
    rec_end = parse_time(record.get("finished_at"))
    return rec_start < end and rec_end > start


def record_is_contained(record: dict[str, Any], start: dt.datetime, end: dt.datetime) -> bool:
    rec_start = parse_time(record.get("started_at"))
    rec_end = parse_time(record.get("finished_at"))
    return rec_start >= start and rec_end <= end


def decimal_add(left: Decimal, value: Any) -> Decimal:
    if value is None:
        return left
    try:
        return left + Decimal(str(value))
    except InvalidOperation:
        return left


def empty_summary_row(provider: str, model: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "records": 0,
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "input_audio_tokens": 0,
        "output_audio_tokens": 0,
        "tokens_consumed": 0,
        "tokens_consumed_known_records": 0,
        "billed_tokens": 0,
        "billed_tokens_known_records": 0,
        "actual_cost_usd": Decimal("0"),
        "actual_cost_known_records": 0,
        "duration_ms": 0,
        "sources": set(),
    }


def summarize_records(records: list[dict[str, Any]], start: dt.datetime, end: dt.datetime, args: argparse.Namespace) -> dict[str, Any]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    source_counts: dict[str, int] = {}
    skipped = 0
    partial_overlap_skipped = 0
    for record in records:
        if not record_overlaps(record, start, end):
            continue
        provider = str(record.get("provider") or "unknown")
        model = str(record.get("model") or "(unattributed)")
        if args.provider and provider != args.provider:
            skipped += 1
            continue
        if args.model and model != args.model:
            skipped += 1
            continue
        source_type = str((record.get("source") or {}).get("type") or "unknown")
        if args.trusted_only and source_type in TRUSTED_EXCLUDED_SOURCE_TYPES:
            skipped += 1
            continue
        if not record_is_contained(record, start, end):
            partial_overlap_skipped += 1
            continue
        key = (provider, model)
        row = rows.setdefault(key, empty_summary_row(provider, model))
        usage = record.get("usage") or {}
        billing = record.get("billing") or {}
        row["records"] += 1
        row["requests"] += int(usage.get("requests") or 0)
        row["input_tokens"] += int(usage.get("input_tokens") or 0)
        row["output_tokens"] += int(usage.get("output_tokens") or 0)
        row["cached_input_tokens"] += int(usage.get("cached_input_tokens") or 0)
        row["input_audio_tokens"] += int(usage.get("input_audio_tokens") or 0)
        row["output_audio_tokens"] += int(usage.get("output_audio_tokens") or 0)
        if usage.get("tokens_consumed") is not None:
            row["tokens_consumed"] += int(usage.get("tokens_consumed") or 0)
            row["tokens_consumed_known_records"] += 1
        if usage.get("billed_tokens") is not None:
            row["billed_tokens"] += int(usage.get("billed_tokens") or 0)
            row["billed_tokens_known_records"] += 1
        billing_source = str(billing.get("source") or "unknown")
        if billing.get("actual_cost_usd") is not None and (
            not args.trusted_only or billing_source in PROVIDER_BILLING_SOURCES
        ):
            row["actual_cost_usd"] = decimal_add(row["actual_cost_usd"], billing.get("actual_cost_usd"))
            row["actual_cost_known_records"] += 1
        row["duration_ms"] += int(record.get("duration_ms") or 0)
        row["sources"].add(source_type)
        source_counts[source_type] = source_counts.get(source_type, 0) + 1

    rendered_rows = []
    totals = empty_summary_row("ALL", "ALL")
    for row in sorted(rows.values(), key=lambda item: (item["provider"], item["model"])):
        for field in [
            "records",
            "requests",
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "input_audio_tokens",
            "output_audio_tokens",
            "tokens_consumed",
            "tokens_consumed_known_records",
            "billed_tokens",
            "billed_tokens_known_records",
            "actual_cost_known_records",
            "duration_ms",
        ]:
            totals[field] += row[field]
        totals["actual_cost_usd"] += row["actual_cost_usd"]
        totals["sources"].update(row["sources"])
        rendered_rows.append(render_row(row))
    return {
        "from": to_iso(start),
        "to": to_iso(end),
        "rows": rendered_rows,
        "totals": render_row(totals),
        "source_counts": source_counts,
        "skipped": skipped,
        "partial_overlap_skipped": partial_overlap_skipped,
        "ledger": str(ledger_path(args.data_dir)),
    }


def render_row(row: dict[str, Any]) -> dict[str, Any]:
    tokens_consumed = row["tokens_consumed"] if row["tokens_consumed_known_records"] else None
    billed_tokens = row["billed_tokens"] if row["billed_tokens_known_records"] else None
    actual_cost_usd = format(row["actual_cost_usd"], "f") if row["actual_cost_known_records"] else None
    return {
        **{
            key: value
            for key, value in row.items()
            if key
            not in {
                "actual_cost_usd",
                "sources",
                "tokens_consumed",
                "billed_tokens",
            }
        },
        "tokens_consumed": tokens_consumed,
        "billed_tokens": billed_tokens,
        "actual_cost_usd": actual_cost_usd,
        "sources": sorted(row["sources"]),
    }


def print_summary_table(summary: dict[str, Any]) -> None:
    rows = summary["rows"]
    print(f"Period: {summary['from']} to {summary['to']}")
    if not rows:
        print("No matching records.")
        if summary.get("partial_overlap_skipped"):
            print(f"Skipped partial-overlap records: {summary['partial_overlap_skipped']}")
        return
    columns = [
        ("provider", "Provider"),
        ("model", "Model"),
        ("records", "Records"),
        ("requests", "Req"),
        ("tokens_consumed", "Consumed"),
        ("billed_tokens", "Billed"),
        ("actual_cost_usd", "Cost USD"),
        ("sources", "Sources"),
    ]
    table = []
    for row in rows:
        table.append(
            {
                **row,
                "tokens_consumed": "-" if row["tokens_consumed"] is None else row["tokens_consumed"],
                "billed_tokens": "-" if row["billed_tokens"] is None else row["billed_tokens"],
                "actual_cost_usd": "-" if row["actual_cost_usd"] is None else row["actual_cost_usd"],
                "sources": ",".join(row["sources"]),
            }
        )
    widths = {
        key: max(len(label), *(len(str(row[key])) for row in table))
        for key, label in columns
    }
    header = "  ".join(label.ljust(widths[key]) for key, label in columns)
    print(header)
    print("  ".join("-" * widths[key] for key, _ in columns))
    for row in table:
        print("  ".join(str(row[key]).ljust(widths[key]) for key, _ in columns))
    totals = summary["totals"]
    print()
    print(
        "Totals: "
        f"records={totals['records']} "
        f"requests={totals['requests']} "
        f"tokens_consumed={totals['tokens_consumed'] if totals['tokens_consumed'] is not None else 'unavailable'} "
        f"billed_tokens={totals['billed_tokens'] if totals['billed_tokens'] is not None else 'unavailable'} "
        f"actual_cost_usd={totals['actual_cost_usd'] if totals['actual_cost_usd'] is not None else 'unavailable'}"
    )
    print(f"Sources: {json.dumps(summary['source_counts'], sort_keys=True)}")
    if summary.get("partial_overlap_skipped"):
        print(f"Skipped partial-overlap records: {summary['partial_overlap_skipped']}")


def command_summary(args: argparse.Namespace) -> int:
    if args.last:
        end = parse_time(args.to, default=now_utc())
        start = end - parse_duration(args.last)
    else:
        end = parse_time(args.to, default=now_utc())
        start = parse_time(args.from_time, default=end - dt.timedelta(days=1))
    if end <= start:
        raise CliError("--to must be after --from")
    summary = summarize_records(read_ledger(args.data_dir), start, end, args)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_summary_table(summary)
    return 0


def imported_state_path(data_dir: Path) -> Path:
    return data_dir / "imported-files.json"


def load_imported_state(data_dir: Path) -> dict[str, Any]:
    path = imported_state_path(data_dir)
    if not path.exists():
        return {"files": {}}
    state = load_json_file(path)
    if not isinstance(state, dict):
        raise CliError(f"invalid imported state at {path}: expected object")
    files = state.get("files")
    if not isinstance(files, dict):
        raise CliError(f"invalid imported state at {path}: field 'files' must be object")
    for tracked_path, entry in files.items():
        if not isinstance(tracked_path, str) or not tracked_path:
            raise CliError(f"invalid imported state at {path}: file keys must be non-empty strings")
        if not isinstance(entry, dict):
            raise CliError(f"invalid imported state at {path}: entry for {tracked_path!r} must be object")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise CliError(f"invalid imported state at {path}: entry for {tracked_path!r} has invalid sha256")
        imported_at = entry.get("imported_at")
        if not isinstance(imported_at, str) or not imported_at:
            raise CliError(f"invalid imported state at {path}: entry for {tracked_path!r} has invalid imported_at")
        try:
            parse_time(imported_at)
        except CliError as exc:
            raise CliError(
                f"invalid imported state at {path}: entry for {tracked_path!r} has invalid imported_at"
            ) from exc
        records = entry.get("records")
        if isinstance(records, bool) or not isinstance(records, int) or records < 0:
            raise CliError(f"invalid imported state at {path}: entry for {tracked_path!r} has invalid records")
    return state


def save_imported_state(data_dir: Path, state: dict[str, Any]) -> None:
    atomic_write_json(imported_state_path(data_dir), state)


def classify_provider_export(payload: Any) -> str | None:
    try:
        buckets = list(iter_openai_buckets(payload))
    except CliError:
        if isinstance(payload, dict) and payload.get("object") == "page":
            raise
        return None
    saw_openai_shape = (
        isinstance(payload, dict)
        and payload.get("object") == "page"
        and isinstance(payload.get("data"), list)
        and not buckets
    )
    saw_usage = False
    saw_cost = False
    for bucket in buckets:
        if not is_openai_bucket_like(bucket):
            continue
        saw_openai_shape = True
        openai_bucket_times(bucket)
        for result in openai_bucket_results(bucket):
            if not isinstance(result, dict):
                raise CliError("OpenAI bucket result must be an object")
            obj = openai_result_object(result)
            saw_usage = saw_usage or obj.startswith("organization.usage.")
            saw_cost = saw_cost or obj == "organization.costs.result"
    if saw_usage and not saw_cost:
        return "openai_usage"
    if saw_cost and not saw_usage:
        return "openai_costs"
    if saw_usage and saw_cost:
        return "mixed"
    if saw_openai_shape:
        return "openai_empty"
    return None


def import_file_by_type(data_dir: Path, path: Path, notes: str | None = None) -> tuple[bool, int]:
    payload = load_json_file(path)
    kind = classify_provider_export(payload)
    if kind is None:
        return False, 0
    if kind == "openai_empty":
        return True, 0
    records: list[dict[str, Any]] = []
    if kind in {"openai_usage", "mixed"}:
        records.extend(build_openai_usage_records(path, None, notes))
    if kind in {"openai_costs", "mixed"}:
        records.extend(build_openai_cost_records(path, notes))
    count = append_ledger(data_dir, records)
    return True, count


def scan_inbox_once(args: argparse.Namespace) -> int:
    ensure_data_dir(args.data_dir)
    inbox = args.inbox
    inbox.mkdir(parents=True, exist_ok=True)
    state = load_imported_state(args.data_dir)
    imported = 0
    for path in sorted(inbox.glob("*.json")):
        digest = file_sha256(path)
        previous = state["files"].get(str(path))
        if previous and previous.get("sha256") == digest:
            continue
        recognized, appended = import_file_by_type(args.data_dir, path, notes=args.notes)
        if recognized:
            imported += appended
            state["files"][str(path)] = {
                "sha256": digest,
                "imported_at": to_iso(now_utc()),
                "records": appended,
            }
    save_imported_state(args.data_dir, state)
    return imported


def command_watch(args: argparse.Namespace) -> int:
    if not math.isfinite(args.interval) or args.interval <= 0:
        raise CliError("--interval must be a finite number > 0")
    print(f"Watching {args.inbox} for provider export JSON. Ctrl+C to stop.")
    while True:
        imported = scan_inbox_once(args)
        if imported:
            print(json.dumps({"imported_records": imported, "at": to_iso(now_utc())}))
        if args.once:
            return 0
        time.sleep(args.interval)


def command_show(args: argparse.Namespace) -> int:
    records = read_ledger(args.data_dir)
    limit = args.limit
    if limit < 0:
        raise CliError("--limit must be >= 0")
    selected = records[-limit:] if limit else records
    print(json.dumps(selected, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record, import, and summarize LLM token usage and cost evidence.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory for usage-ledger.jsonl and run state",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a locally timed LLM run")
    start.add_argument("--run-id", help="Optional run id")
    start.add_argument("--provider", default="unknown", help="Provider name, e.g. openai")
    start.add_argument("--model", help="Model name if known")
    start.add_argument("--started-at", help="UTC ISO time, epoch seconds, YYYY-MM-DD, or now")
    start.add_argument("--notes", help="Free-form note")
    start.set_defaults(func=command_start)

    finish = sub.add_parser("finish", help="Finish a run created by start")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--provider", help="Override provider")
    finish.add_argument("--model", help="Override model")
    finish.add_argument("--finished-at", help="UTC ISO time, epoch seconds, YYYY-MM-DD, or now")
    finish.add_argument("--exit-code", help="Process/verification exit code")
    finish.add_argument("--notes", help="Free-form note")
    add_usage_arguments(finish)
    finish.set_defaults(func=command_finish)

    record = sub.add_parser("record", help="Append one complete run record")
    record.add_argument("--run-id", help="Optional run id")
    record.add_argument("--provider", default="unknown", help="Provider name, e.g. openai")
    record.add_argument("--model", help="Model name")
    record.add_argument("--started-at", help="UTC ISO time, epoch seconds, YYYY-MM-DD, or now")
    record.add_argument("--finished-at", help="UTC ISO time, epoch seconds, YYYY-MM-DD, or now")
    record.add_argument("--status", choices=["completed", "failed", "incomplete"])
    record.add_argument("--exit-code", help="Process/verification exit code")
    record.add_argument("--notes", help="Free-form note")
    add_usage_arguments(record)
    record.set_defaults(func=command_record)

    wrap = sub.add_parser("wrap", help="Run a command and record start/finish metadata")
    wrap.add_argument("--provider", default="unknown", help="Provider name")
    wrap.add_argument("--model", help="Model name if known")
    wrap.add_argument("--run-id", help="Optional run id")
    wrap.add_argument("--cwd", help="Working directory for wrapped command")
    wrap.add_argument("--notes", help="Free-form note")
    wrap.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --")
    wrap.set_defaults(func=command_wrap)

    imp_usage = sub.add_parser("import-openai-usage", help="Import an OpenAI organization usage JSON response/export")
    imp_usage.add_argument("--file", required=True, type=Path)
    imp_usage.add_argument("--default-model", help="Model name to use when the export was not grouped by model")
    imp_usage.add_argument("--notes", help="Free-form note")
    imp_usage.set_defaults(func=command_import_openai_usage)

    imp_costs = sub.add_parser("import-openai-costs", help="Import an OpenAI organization costs JSON response/export")
    imp_costs.add_argument("--file", required=True, type=Path)
    imp_costs.add_argument("--notes", help="Free-form note")
    imp_costs.set_defaults(func=command_import_openai_costs)

    summary = sub.add_parser("summary", help="Summarize records over a time period")
    summary.add_argument("--from", dest="from_time", help="UTC ISO time, epoch seconds, YYYY-MM-DD, or now")
    summary.add_argument("--to", help="UTC ISO time, epoch seconds, YYYY-MM-DD, or now")
    summary.add_argument("--last", help="Relative period ending at --to or now, e.g. 24h, 7d")
    summary.add_argument("--provider", help="Provider filter")
    summary.add_argument("--model", help="Model filter")
    summary.add_argument("--trusted-only", action="store_true", help="Exclude manual/unavailable/legacy records")
    summary.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    summary.set_defaults(func=command_summary)

    watch = sub.add_parser("watch", help="Continuously import provider export JSON files from an inbox")
    watch.add_argument("--inbox", type=Path, default=DEFAULT_DATA_DIR / "inbox")
    watch.add_argument("--interval", type=float, default=300.0, help="Polling interval in seconds")
    watch.add_argument("--once", action="store_true", help="Scan once and exit")
    watch.add_argument("--notes", help="Free-form note applied to imported records")
    watch.set_defaults(func=command_watch)

    show = sub.add_parser("show", help="Print ledger records")
    show.add_argument("--limit", type=int, default=10)
    show.set_defaults(func=command_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
