from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import AtlasConfig
from .integration import audit_runtime_contract, fingerprint_from_intent
from .ledger import AtlasLedger
from .replay import ReplayCase, ReplayValidator
from .report import write_report
from .scanner import RepositoryScanner


def _read_json(path: str) -> Any:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(value: Any, path: str | None) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)


def command_scan(args: argparse.Namespace) -> int:
    config = AtlasConfig.load(args.config)
    document = RepositoryScanner(args.repo, config).scan()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    document.write_json(output / "atlas.json")
    write_report(document, output / "atlas.md")
    summary = {
        "atlas_json": str((output / "atlas.json").resolve()),
        "atlas_markdown": str((output / "atlas.md").resolve()),
        **document.summary,
    }
    sys.stdout.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    critical_count = document.summary["findings_by_severity"].get("critical", 0)
    return 2 if args.fail_on_critical and critical_count else 0


def command_query(args: argparse.Namespace) -> int:
    atlas = _read_json(args.atlas)
    files = atlas.get("files", [])
    if args.stack:
        files = [item for item in files if item.get("stack") == args.stack]
    if args.role:
        files = [item for item in files if item.get("role") == args.role]
    if args.path_contains:
        needle = args.path_contains.lower()
        files = [item for item in files if needle in item.get("path", "").lower()]
    filtered = bool(args.stack or args.role or args.path_contains)
    selected_paths = {item.get("path") for item in files}
    result = {
        "count": len(files),
        "files": files,
        "findings": [
            item
            for item in atlas.get("findings", [])
            if not filtered
            or item.get("path") in selected_paths
            or (args.stack and item.get("path") == args.stack)
        ],
    }
    _write_json(result, args.out)
    return 0


def command_init_db(args: argparse.Namespace) -> int:
    ledger = AtlasLedger(args.db)
    _write_json({"database": str(ledger.path), "schema_version": 1}, None)
    return 0


def command_diagnostics(args: argparse.Namespace) -> int:
    ledger = AtlasLedger(args.db)
    _write_json(ledger.diagnostics(window_hours=args.window_hours), args.out)
    return 0


def command_snapshot(args: argparse.Namespace) -> int:
    ledger = AtlasLedger(args.db)
    _write_json(ledger.export_snapshot(intent_limit=args.intent_limit), args.out)
    return 0


def command_prune(args: argparse.Namespace) -> int:
    ledger = AtlasLedger(args.db)
    result = ledger.prune(
        event_days=args.event_days,
        summary_days=args.summary_days,
        transition_days=args.transition_days,
        dedupe_days=args.dedupe_days,
    )
    _write_json(result, None)
    return 0


def command_key(args: argparse.Namespace) -> int:
    payload = _read_json(args.intent)
    fingerprint = fingerprint_from_intent(payload)
    _write_json(
        {"canonical": fingerprint.canonical(), "idempotency_key": fingerprint.idempotency_key()},
        args.out,
    )
    return 0


def command_audit_runtime(args: argparse.Namespace) -> int:
    observation = _read_json(args.observation)
    findings = audit_runtime_contract(
        observation, heartbeat_max_age_seconds=args.heartbeat_max_age_seconds
    )
    _write_json(
        {
            "finding_count": len(findings),
            "findings": [
                {"code": item.code, "severity": item.severity, "message": item.message}
                for item in findings
            ],
        },
        args.out,
    )
    return 0


def command_replay(args: argparse.Namespace) -> int:
    validator = ReplayValidator()
    cases = [ReplayCase.load(path) for path in args.cases]
    results = []
    ledger = AtlasLedger(args.db) if args.db else None
    for case in cases:
        result = validator.validate(case)
        encoded = result.to_dict()
        if ledger:
            encoded["trace_id"] = validator.record(ledger, case, result)
        results.append(encoded)
    payload = {
        "passed": all(result["passed"] for result in results),
        "case_count": len(results),
        "results": results,
    }
    _write_json(payload, args.out)
    return 0 if payload["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="risedual-atlas",
        description="Map RISEDUAL and verify its intent lifecycle without broker activity.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Build atlas.json and atlas.md")
    scan.add_argument("--repo", required=True)
    scan.add_argument("--config")
    scan.add_argument("--output", default="atlas-output")
    scan.add_argument("--fail-on-critical", action="store_true")
    scan.set_defaults(func=command_scan)

    query = subparsers.add_parser("query", help="Query a generated atlas")
    query.add_argument("--atlas", required=True)
    query.add_argument("--stack")
    query.add_argument("--role")
    query.add_argument("--path-contains")
    query.add_argument("--out")
    query.set_defaults(func=command_query)

    init_db = subparsers.add_parser("init-db", help="Initialize the compact SQLite ledger")
    init_db.add_argument("--db", required=True)
    init_db.set_defaults(func=command_init_db)

    diagnostics = subparsers.add_parser("diagnostics", help="Read compact runtime diagnostics")
    diagnostics.add_argument("--db", required=True)
    diagnostics.add_argument("--window-hours", type=int, default=24)
    diagnostics.add_argument("--out")
    diagnostics.set_defaults(func=command_diagnostics)

    snapshot = subparsers.add_parser("snapshot", help="Export diagnostics plus recent intent rows")
    snapshot.add_argument("--db", required=True)
    snapshot.add_argument("--intent-limit", type=int, default=100)
    snapshot.add_argument("--out")
    snapshot.set_defaults(func=command_snapshot)

    prune = subparsers.add_parser("prune", help="Apply bounded SQLite retention")
    prune.add_argument("--db", required=True)
    prune.add_argument("--event-days", type=int, default=30)
    prune.add_argument("--summary-days", type=int, default=180)
    prune.add_argument("--transition-days", type=int, default=180)
    prune.add_argument("--dedupe-days", type=int, default=365)
    prune.set_defaults(func=command_prune)

    key = subparsers.add_parser("key", help="Derive a durable key from an intent JSON file")
    key.add_argument("--intent", required=True)
    key.add_argument("--out")
    key.set_defaults(func=command_key)

    audit = subparsers.add_parser("audit-runtime", help="Diagnose known runtime contract drift")
    audit.add_argument("--observation", required=True)
    audit.add_argument("--heartbeat-max-age-seconds", type=int, default=60)
    audit.add_argument("--out")
    audit.set_defaults(func=command_audit_runtime)

    replay = subparsers.add_parser("replay", help="Validate recorded decision traces")
    replay.add_argument("cases", nargs="+")
    replay.add_argument("--db", help="Optionally retain replay trace summaries in SQLite")
    replay.add_argument("--out")
    replay.set_defaults(func=command_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
        return 2
