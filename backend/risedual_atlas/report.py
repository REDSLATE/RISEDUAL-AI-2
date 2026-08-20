from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .scanner import AtlasDocument, Finding


def _table(headers: list[str], rows: Iterable[list[object]]) -> list[str]:
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        result.append("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |")
    return result


def _finding_rows(findings: list[Finding]) -> list[list[object]]:
    return [
        [
            finding.severity,
            finding.code,
            finding.path + (f":{finding.line}" if finding.line else ""),
            finding.message,
        ]
        for finding in findings
    ]


def render_markdown(document: AtlasDocument) -> str:
    summary = document.summary
    lines = [
        f"# {document.config['project_name']} System Atlas",
        "",
        f"Generated: `{document.generated_at}`",
        "",
        "This report is a static architecture and authority map. It does not place orders or "
        "change runtime configuration.",
        "",
        "## Summary",
        "",
    ]
    lines.extend(
        _table(
            ["Measure", "Count"],
            [
                ["Source files", summary["file_count"]],
                ["Dependency edges", summary["dependency_count"]],
                ["API endpoints", summary["endpoint_count"]],
                ["Decision/gate reasons", summary["gate_reason_count"]],
            ],
        )
    )
    lines.extend(["", "### Files by stack", ""])
    lines.extend(_table(["Stack", "Files"], summary["files_by_stack"].items()))
    lines.extend(["", "### Files by execution role", ""])
    lines.extend(_table(["Role", "Files"], summary["files_by_role"].items()))

    critical = [item for item in document.findings if item.severity == "critical"]
    warnings = [item for item in document.findings if item.severity == "warning"]
    info = [item for item in document.findings if item.severity == "info"]
    lines.extend(["", "## Boundary and authority findings", ""])
    if critical or warnings:
        lines.extend(_table(["Severity", "Code", "Location", "Finding"], _finding_rows(critical + warnings)))
    else:
        lines.append("No critical or warning findings were detected by the configured checks.")
    if info:
        lines.extend(["", "### Informational", ""])
        lines.extend(_table(["Severity", "Code", "Location", "Finding"], _finding_rows(info)))

    lines.extend(["", "## Endpoint catalog", ""])
    if document.endpoint_catalog:
        lines.extend(
            _table(
                ["Endpoint", "Defined or referenced in"],
                ([endpoint, "<br>".join(paths)] for endpoint, paths in document.endpoint_catalog.items()),
            )
        )
    else:
        lines.append("No `/api/` endpoints were found.")

    lines.extend(["", "## Decision and gate reason catalog", ""])
    if document.gate_reason_catalog:
        lines.extend(
            _table(
                ["Reason", "Observed in"],
                ([reason, "<br>".join(paths)] for reason, paths in document.gate_reason_catalog.items()),
            )
        )
    else:
        lines.append("No explicit reason-code assignments were found.")

    lines.extend(["", "## File map", ""])
    lines.extend(
        _table(
            ["Path", "Stack", "Role", "Language", "State"],
            (
                [node.path, node.stack, node.role, node.language, ", ".join(node.state_stores)]
                for node in document.files
            ),
        )
    )
    lines.append("")
    return "\n".join(lines)


def write_report(document: AtlasDocument, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_markdown(document), encoding="utf-8")
