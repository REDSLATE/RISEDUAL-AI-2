from __future__ import annotations

import ast
import hashlib
import json
import posixpath
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import AtlasConfig


JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
PYTHON_EXTENSION = ".py"

JS_IMPORT_RE = re.compile(
    r"(?:import|export)\s+(?:[\s\S]*?\s+from\s+)?[\"']([^\"']+)[\"']"
    r"|require\(\s*[\"']([^\"']+)[\"']\s*\)"
    r"|import\(\s*[\"']([^\"']+)[\"']\s*\)",
    re.MULTILINE,
)
JS_DEFINITION_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
    r"|(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"
    r"|(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?\([^)]*\)\s*=>",
    re.MULTILINE,
)
ENDPOINT_RE = re.compile(r"[\"'](/api/[A-Za-z0-9_./:{}?=&-]+)[\"']")
ENV_JS_RE = re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)")
REASON_RE = re.compile(
    r"(?:reason|reason_code|block_reason|decision_code|terminal_result)"
    r"\s*[=:]\s*[\"']([A-Za-z0-9_.:-]{2,100})[\"']",
    re.IGNORECASE,
)
STRING_LITERAL_RE = re.compile(
    r"(?:[rubfRUBF]{0,2})(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
STATE_MARKERS = {
    "sqlite": re.compile(r"\bsqlite(?:3)?\b", re.IGNORECASE),
    "mongodb": re.compile(r"\b(?:mongodb|mongoengine|pymongo)\b", re.IGNORECASE),
    "convex": re.compile(r"\bconvex\b", re.IGNORECASE),
    "redis": re.compile(r"\bredis\b", re.IGNORECASE),
    "postgres": re.compile(r"\b(?:postgres|postgresql|psycopg)\b", re.IGNORECASE),
    "filesystem": re.compile(r"\b(?:open\(|read_text\(|write_text\()"),
}


@dataclass(slots=True)
class FileNode:
    path: str
    stack: str
    role: str
    language: str
    size_bytes: int
    sha256: str
    imports: list[str] = field(default_factory=list)
    definitions: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    environment_variables: list[str] = field(default_factory=list)
    state_stores: list[str] = field(default_factory=list)
    gate_reasons: list[str] = field(default_factory=list)
    skipped_reason: str | None = None


@dataclass(slots=True)
class DependencyEdge:
    source: str
    target: str | None
    import_name: str
    source_stack: str
    target_stack: str | None


@dataclass(slots=True)
class Finding:
    code: str
    severity: str
    path: str
    line: int | None
    message: str
    evidence: str | None = None


@dataclass(slots=True)
class AtlasDocument:
    schema_version: int
    generated_at: str
    repository_root: str
    config: dict[str, Any]
    summary: dict[str, Any]
    files: list[FileNode]
    dependencies: list[DependencyEdge]
    findings: list[Finding]
    endpoint_catalog: dict[str, list[str]]
    gate_reason_catalog: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "repository_root": self.repository_root,
            "config": self.config,
            "summary": self.summary,
            "files": [asdict(item) for item in self.files],
            "dependencies": [asdict(item) for item in self.dependencies],
            "findings": [asdict(item) for item in self.findings],
            "endpoint_catalog": self.endpoint_catalog,
            "gate_reason_catalog": self.gate_reason_catalog,
        }

    def write_json(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.definitions: set[str] = set()
        self.endpoints: set[str] = set()
        self.environment_variables: set[str] = set()
        self.gate_reasons: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.update(alias.name for alias in node.names)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level
        module = node.module or ""
        self.imports.add(prefix + module)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definitions.add(node.name)
        self._visit_route_decorators(node.decorator_list)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definitions.add(node.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                isinstance(owner, ast.Name)
                and owner.id == "os"
                and node.func.attr == "getenv"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                self.environment_variables.add(node.args[0].value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            self.environment_variables.add(node.slice.value)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        interesting = {
            "reason",
            "reason_code",
            "block_reason",
            "decision_code",
            "terminal_result",
        }
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and str(key.value).lower() in interesting
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                self.gate_reasons.add(value.value)
        self.generic_visit(node)

    def _visit_route_decorators(self, decorators: Iterable[ast.expr]) -> None:
        for decorator in decorators:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if first.value.startswith("/api/"):
                    self.endpoints.add(first.value)


class RepositoryScanner:
    def __init__(self, repository_root: str | Path, config: AtlasConfig | None = None):
        self.root = Path(repository_root).resolve()
        self.config = config or AtlasConfig.defaults()
        if not self.root.is_dir():
            raise ValueError(f"repository root is not a directory: {self.root}")
        self._texts: dict[str, str] = {}

    def scan(self) -> AtlasDocument:
        nodes: list[FileNode] = []
        findings: list[Finding] = []
        self._texts.clear()

        for path in self._source_files():
            relative = path.relative_to(self.root).as_posix()
            node, node_findings = self._scan_file(path, relative)
            nodes.append(node)
            findings.extend(node_findings)

        nodes.sort(key=lambda node: node.path)
        dependencies = self._build_dependencies(nodes)
        findings.extend(self._boundary_findings(dependencies))
        findings.extend(self._missing_root_findings())
        findings.sort(key=lambda item: (item.severity, item.code, item.path, item.line or 0))

        endpoint_catalog: dict[str, list[str]] = {}
        reason_catalog: dict[str, list[str]] = {}
        for node in nodes:
            for endpoint in node.endpoints:
                endpoint_catalog.setdefault(endpoint, []).append(node.path)
            for reason in node.gate_reasons:
                reason_catalog.setdefault(reason, []).append(node.path)

        stack_counts = Counter(node.stack for node in nodes)
        role_counts = Counter(node.role for node in nodes)
        severity_counts = Counter(item.severity for item in findings)
        summary = {
            "file_count": len(nodes),
            "dependency_count": len(dependencies),
            "endpoint_count": len(endpoint_catalog),
            "gate_reason_count": len(reason_catalog),
            "files_by_stack": dict(sorted(stack_counts.items())),
            "files_by_role": dict(sorted(role_counts.items())),
            "findings_by_severity": dict(sorted(severity_counts.items())),
        }
        return AtlasDocument(
            schema_version=1,
            generated_at=datetime.now(UTC).isoformat(),
            repository_root=str(self.root),
            config=self.config.to_public_dict(),
            summary=summary,
            files=nodes,
            dependencies=dependencies,
            findings=findings,
            endpoint_catalog={k: sorted(v) for k, v in sorted(endpoint_catalog.items())},
            gate_reason_catalog={k: sorted(v) for k, v in sorted(reason_catalog.items())},
        )

    def _source_files(self) -> Iterable[Path]:
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root).as_posix()
            if self.config.is_ignored(relative):
                continue
            if path.suffix.lower() in self.config.extensions:
                yield path

    def _scan_file(self, path: Path, relative: str) -> tuple[FileNode, list[Finding]]:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        language = self._language(path.suffix.lower())
        node = FileNode(
            path=relative,
            stack=self.config.classify_stack(relative),
            role=self.config.classify_role(relative),
            language=language,
            size_bytes=len(raw),
            sha256=digest,
        )
        if len(raw) > self.config.max_file_bytes:
            node.skipped_reason = f"larger than max_file_bytes={self.config.max_file_bytes}"
            return node, [
                Finding(
                    "FILE_TOO_LARGE",
                    "info",
                    relative,
                    None,
                    "File content was not parsed; structural metadata was retained.",
                    node.skipped_reason,
                )
            ]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            node.skipped_reason = "not valid UTF-8"
            return node, []

        self._texts[relative] = text
        if path.suffix.lower() == PYTHON_EXTENSION:
            self._populate_python(node, text)
        elif path.suffix.lower() in JS_EXTENSIONS:
            self._populate_javascript(node, text)
        else:
            node.endpoints = sorted(set(ENDPOINT_RE.findall(text)))
            node.environment_variables = sorted(set(ENV_JS_RE.findall(text)))
            node.gate_reasons = sorted(set(REASON_RE.findall(text)))
        node.state_stores = sorted(
            name for name, pattern in STATE_MARKERS.items() if pattern.search(text)
        )
        findings = self._ownership_findings(node, text)
        findings.extend(self._signal_only_findings(node, text))
        return node, findings

    @staticmethod
    def _language(extension: str) -> str:
        return {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".json": "json",
            ".toml": "toml",
            ".yaml": "yaml",
            ".yml": "yaml",
        }.get(extension, "text")

    @staticmethod
    def _populate_python(node: FileNode, text: str) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            node.skipped_reason = "python syntax error"
            node.endpoints = sorted(set(ENDPOINT_RE.findall(text)))
            node.gate_reasons = sorted(set(REASON_RE.findall(text)))
            return
        visitor = _PythonVisitor()
        visitor.visit(tree)
        node.imports = sorted(item for item in visitor.imports if item)
        node.definitions = sorted(visitor.definitions)
        node.endpoints = sorted(visitor.endpoints | set(ENDPOINT_RE.findall(text)))
        node.environment_variables = sorted(visitor.environment_variables)
        node.gate_reasons = sorted(visitor.gate_reasons | set(REASON_RE.findall(text)))

    @staticmethod
    def _populate_javascript(node: FileNode, text: str) -> None:
        imports = {next(value for value in match if value) for match in JS_IMPORT_RE.findall(text)}
        definitions = {
            next(value for value in match if value)
            for match in JS_DEFINITION_RE.findall(text)
        }
        node.imports = sorted(imports)
        node.definitions = sorted(definitions)
        node.endpoints = sorted(set(ENDPOINT_RE.findall(text)))
        node.environment_variables = sorted(set(ENV_JS_RE.findall(text)))
        node.gate_reasons = sorted(set(REASON_RE.findall(text)))

    def _ownership_findings(self, node: FileNode, text: str) -> list[Finding]:
        if (
            node.language not in {"python", "javascript", "typescript"}
            or node.stack == self.config.execution_owner
            or self._is_test_or_doc(node.path)
        ):
            return []
        findings: list[Finding] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "//", "*")):
                continue
            code_only = STRING_LITERAL_RE.sub("", line)
            for pattern in self.config.submit_patterns:
                if pattern.lower() in code_only.lower():
                    findings.append(
                        Finding(
                            "EXECUTION_OWNER_VIOLATION",
                            "critical",
                            node.path,
                            line_number,
                            f"Broker-submit marker appears outside {self.config.execution_owner}.",
                            stripped[:240],
                        )
                    )
                    break
        return findings

    def _signal_only_findings(self, node: FileNode, text: str) -> list[Finding]:
        if (
            node.language not in {"python", "javascript", "typescript"}
            or not any(
                self.config._under(node.path, root)
                for root in self.config.signal_only_roots
            )
        ):
            return []
        findings: list[Finding] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            lowered = STRING_LITERAL_RE.sub("", line).lower()
            markers = [marker for marker in self.config.broker_markers if marker.lower() in lowered]
            submit = [pattern for pattern in self.config.submit_patterns if pattern.lower() in lowered]
            if markers or submit:
                findings.append(
                    Finding(
                        "SIGNAL_ONLY_BROKER_COUPLING",
                        "critical",
                        node.path,
                        line_number,
                        "Signal-only code references a broker or submit operation.",
                        line.strip()[:240],
                    )
                )
        return findings

    @staticmethod
    def _is_test_or_doc(path: str) -> bool:
        lowered = path.lower()
        return (
            "/test" in lowered
            or lowered.startswith("test")
            or "/docs/" in lowered
            or lowered.startswith("docs/")
            or lowered.endswith((".md", ".txt"))
        )

    def _build_dependencies(self, nodes: list[FileNode]) -> list[DependencyEdge]:
        by_path = {node.path: node for node in nodes}
        module_index: dict[str, list[str]] = {}
        for node in nodes:
            path = Path(node.path)
            without_suffix = path.with_suffix("").as_posix()
            variants = {without_suffix, without_suffix.replace("/", ".")}
            if without_suffix.endswith("/__init__"):
                package = without_suffix[: -len("/__init__")]
                variants.update({package, package.replace("/", ".")})
            if "/src/" in without_suffix:
                after_src = without_suffix.split("/src/", 1)[1]
                variants.update({after_src, after_src.replace("/", ".")})
            for variant in variants:
                module_index.setdefault(variant, []).append(node.path)

        edges: list[DependencyEdge] = []
        for node in nodes:
            for import_name in node.imports:
                target = self._resolve_import(node.path, import_name, by_path, module_index)
                target_node = by_path.get(target) if target else None
                edges.append(
                    DependencyEdge(
                        source=node.path,
                        target=target,
                        import_name=import_name,
                        source_stack=node.stack,
                        target_stack=target_node.stack if target_node else None,
                    )
                )
        return sorted(edges, key=lambda edge: (edge.source, edge.import_name))

    @staticmethod
    def _resolve_import(
        source: str,
        import_name: str,
        by_path: dict[str, FileNode],
        module_index: dict[str, list[str]],
    ) -> str | None:
        source_path = Path(source)
        if import_name.startswith("."):
            if "/" in import_name:
                base = posixpath.normpath((source_path.parent / import_name).as_posix())
            else:
                dots = len(import_name) - len(import_name.lstrip("."))
                suffix = import_name[dots:].replace(".", "/")
                parent = source_path.parent
                for _ in range(max(0, dots - 1)):
                    parent = parent.parent
                base = posixpath.normpath((parent / suffix).as_posix())
            candidates = [base, *(base + ext for ext in (PYTHON_EXTENSION, *JS_EXTENSIONS))]
            candidates.extend(
                f"{base}/index{ext}" for ext in JS_EXTENSIONS
            )
            candidates.append(f"{base}/__init__.py")
            for candidate in candidates:
                normalized = Path(candidate).as_posix()
                if normalized in by_path:
                    return normalized
            return None

        normalized_module = import_name.replace("/", ".")
        candidates = module_index.get(import_name, []) + module_index.get(
            normalized_module, []
        )
        if len(set(candidates)) == 1:
            return candidates[0]
        suffix = import_name.replace(".", "/")
        suffix_matches = [
            path
            for path in by_path
            if path.removesuffix(Path(path).suffix).endswith(suffix)
        ]
        return suffix_matches[0] if len(suffix_matches) == 1 else None

    def _boundary_findings(self, edges: list[DependencyEdge]) -> list[Finding]:
        protected = {"alpha", "mission_control"}
        findings: list[Finding] = []
        for edge in edges:
            if (
                edge.source_stack not in protected
                or edge.target_stack not in protected
                or edge.source_stack == edge.target_stack
            ):
                continue
            joined = f"{edge.source} {edge.target or ''} {edge.import_name}"
            if any(pattern in joined for pattern in self.config.allowed_cross_stack_patterns):
                continue
            findings.append(
                Finding(
                    "CROSS_STACK_IMPORT",
                    "critical",
                    edge.source,
                    None,
                    f"{edge.source_stack} imports directly from {edge.target_stack}.",
                    f"{edge.import_name} -> {edge.target}",
                )
            )
        return findings

    def _missing_root_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for rule in self.config.stack_rules:
            if not rule.roots:
                continue
            if not any((self.root / root).exists() for root in rule.roots):
                findings.append(
                    Finding(
                        "STACK_ROOT_NOT_FOUND",
                        "warning",
                        rule.name,
                        None,
                        f"None of the configured roots for stack '{rule.name}' exist.",
                        ", ".join(rule.roots),
                    )
                )
        return findings
