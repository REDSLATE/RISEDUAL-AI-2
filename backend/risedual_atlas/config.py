from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_IGNORE_DIRS = (
    ".git",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "tmp",
    "vendor",
)

DEFAULT_EXTENSIONS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
)

DEFAULT_ROLE_PATTERNS = {
    "scanner": ("scanner", "momentum", "market_data", "market-data"),
    "brain": ("brain", "strategist", "decider", "signal"),
    "intent": ("intent", "router", "ingest"),
    "gate": ("gate", "roadguard", "risk", "governor", "seat_policy"),
    "executor": ("executor", "broker", "order", "execution"),
    "outcome": ("outcome", "receipt", "fill", "journal"),
    "operator_ui": ("admin", "diagnostic", "dashboard", "live-trade"),
}


@dataclass(slots=True)
class StackRule:
    name: str
    roots: tuple[str, ...]


@dataclass(slots=True)
class AtlasConfig:
    project_name: str = "RISEDUAL"
    stack_rules: list[StackRule] = field(default_factory=list)
    ignore_dirs: tuple[str, ...] = DEFAULT_IGNORE_DIRS
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    max_file_bytes: int = 2_000_000
    role_patterns: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_ROLE_PATTERNS)
    )
    execution_owner: str = "mission_control"
    submit_patterns: tuple[str, ...] = (
        "submit_order(",
        "place_order(",
        "create_order(",
        "add_order(",
        ".submit_order(",
        ".place_order(",
    )
    signal_only_roots: tuple[str, ...] = ("external/brains",)
    broker_markers: tuple[str, ...] = ("webull", "kraken")
    allowed_cross_stack_patterns: tuple[str, ...] = ()
    heartbeat_max_age_seconds: int = 60

    @classmethod
    def defaults(cls) -> "AtlasConfig":
        return cls(
            stack_rules=[
                StackRule(
                    "mission_control",
                    (
                        "apps/mission-control",
                        "apps/mission_control",
                        "services/mission-control",
                        "services/mission_control",
                        "external/brains",
                    ),
                ),
                StackRule("alpha", ("apps/alpha", "services/alpha")),
                StackRule("shared", ("packages", "libs", "shared")),
            ]
        )

    @classmethod
    def load(cls, path: str | Path | None) -> "AtlasConfig":
        config = cls.defaults()
        if path is None:
            return config
        with Path(path).open("rb") as stream:
            raw = tomllib.load(stream)

        atlas = raw.get("atlas", {})
        config.project_name = str(atlas.get("project_name", config.project_name))
        config.ignore_dirs = tuple(atlas.get("ignore_dirs", config.ignore_dirs))
        config.extensions = tuple(atlas.get("extensions", config.extensions))
        config.max_file_bytes = int(atlas.get("max_file_bytes", config.max_file_bytes))

        stacks = raw.get("stacks", {})
        if stacks:
            config.stack_rules = [
                StackRule(name, tuple(section.get("roots", ())))
                for name, section in stacks.items()
            ]

        roles = raw.get("roles", {})
        if roles:
            config.role_patterns = {
                str(name): tuple(str(item) for item in patterns)
                for name, patterns in roles.items()
            }

        execution = raw.get("execution", {})
        config.execution_owner = str(
            execution.get("owner_stack", config.execution_owner)
        )
        config.submit_patterns = tuple(
            execution.get("submit_patterns", config.submit_patterns)
        )
        config.signal_only_roots = tuple(
            execution.get("signal_only_roots", config.signal_only_roots)
        )
        config.broker_markers = tuple(
            execution.get("broker_markers", config.broker_markers)
        )
        config.heartbeat_max_age_seconds = int(
            execution.get(
                "heartbeat_max_age_seconds", config.heartbeat_max_age_seconds
            )
        )

        boundaries = raw.get("boundaries", {})
        config.allowed_cross_stack_patterns = tuple(
            boundaries.get(
                "allowed_cross_stack_patterns", config.allowed_cross_stack_patterns
            )
        )
        return config

    @staticmethod
    def _under(path: str, root: str) -> bool:
        normalized_path = path.replace("\\", "/").strip("/")
        normalized_root = root.replace("\\", "/").strip("/")
        return normalized_path == normalized_root or normalized_path.startswith(
            normalized_root + "/"
        )

    def classify_stack(self, relative_path: str) -> str:
        candidates: list[tuple[int, str]] = []
        for rule in self.stack_rules:
            for root in rule.roots:
                if self._under(relative_path, root):
                    candidates.append((len(root), rule.name))
        return max(candidates, default=(0, "unmapped"))[1]

    def classify_role(self, relative_path: str) -> str:
        lowered = relative_path.lower()
        candidates: list[tuple[int, str]] = []
        for role, patterns in self.role_patterns.items():
            for pattern in patterns:
                if pattern.lower() in lowered:
                    candidates.append((len(pattern), role))
        return max(candidates, default=(0, "other"))[1]

    def is_ignored(self, relative_path: str) -> bool:
        parts = Path(relative_path).parts
        return any(
            part in self.ignore_dirs
            or any(fnmatch.fnmatch(part, pattern) for pattern in self.ignore_dirs)
            for part in parts
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "stacks": {rule.name: list(rule.roots) for rule in self.stack_rules},
            "execution_owner": self.execution_owner,
            "signal_only_roots": list(self.signal_only_roots),
            "heartbeat_max_age_seconds": self.heartbeat_max_age_seconds,
        }
