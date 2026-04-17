"""Pattern 03 — Prompt Versioning.

Treat prompts as code. Version them. Pin them in production. Route traffic by version.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Prompt:
    """An immutable versioned prompt."""

    id: str  # e.g., "contract_summary"
    version: str  # e.g., "v3"
    template: str
    variables: list[str] = field(default_factory=list)
    description: str = ""
    owner: str = ""
    created_at: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def hash(self) -> str:
        """SHA256 of the template content; used as a cache/logging key."""
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()[:12]

    def render(self, **kwargs) -> str:
        """Fill in template variables using {name} syntax."""
        missing = [v for v in self.variables if v not in kwargs]
        if missing:
            raise ValueError(f"Missing variables: {missing}")
        return self.template.format(**kwargs)


class PromptRegistry:
    """Repository of versioned prompts. Loaded from disk; immutable in memory."""

    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path
        self._prompts: dict[tuple[str, str], Prompt] = {}  # (id, version) -> Prompt
        self._active_version: dict[str, str] = {}  # id -> active version
        self._load()

    def _load(self) -> None:
        if not self.registry_path.exists():
            return
        for f in self.registry_path.glob("*.json"):
            data = json.loads(f.read_text())
            prompt = Prompt(
                id=data["id"],
                version=data["version"],
                template=data["template"],
                variables=data.get("variables", []),
                description=data.get("description", ""),
                owner=data.get("owner", ""),
                created_at=data.get("created_at", ""),
                tags=data.get("tags", []),
            )
            self._prompts[(prompt.id, prompt.version)] = prompt

        # Load active versions mapping (separate file)
        active_file = self.registry_path / "_active.json"
        if active_file.exists():
            self._active_version = json.loads(active_file.read_text())

    def get(self, prompt_id: str, version: Optional[str] = None) -> Prompt:
        """Get a prompt by ID. Uses active version if version omitted."""
        if version is None:
            version = self._active_version.get(prompt_id)
            if version is None:
                raise KeyError(f"No active version for prompt: {prompt_id}")
        key = (prompt_id, version)
        if key not in self._prompts:
            raise KeyError(f"Prompt not found: {prompt_id}@{version}")
        return self._prompts[key]

    def list_versions(self, prompt_id: str) -> list[str]:
        return sorted(v for (pid, v) in self._prompts.keys() if pid == prompt_id)

    def versioned_ids(self) -> list[tuple[str, str]]:
        return sorted(self._prompts.keys())

    def set_active(self, prompt_id: str, version: str) -> None:
        key = (prompt_id, version)
        if key not in self._prompts:
            raise KeyError(f"Cannot activate unknown prompt: {prompt_id}@{version}")
        self._active_version[prompt_id] = version
        (self.registry_path / "_active.json").write_text(
            json.dumps(self._active_version, indent=2)
        )


class TrafficRouter:
    """Route prompt requests by version, supporting canary rollout.

    Example:
        router = TrafficRouter()
        router.set_split("contract_summary", {"v3": 0.95, "v4": 0.05})

        version = router.pick("contract_summary", user_id="user_abc")
    """

    def __init__(self) -> None:
        self._splits: dict[str, dict[str, float]] = {}

    def set_split(self, prompt_id: str, version_weights: dict[str, float]) -> None:
        total = sum(version_weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        self._splits[prompt_id] = dict(version_weights)

    def pick(self, prompt_id: str, user_id: str) -> str:
        """Deterministically pick a version for the given user.

        Same user + same split = same version (sticky).
        """
        split = self._splits.get(prompt_id)
        if not split:
            raise KeyError(f"No split configured for {prompt_id}")

        # Deterministic hash of user_id to a float in [0, 1)
        h = hashlib.sha256(f"{prompt_id}:{user_id}".encode()).hexdigest()
        bucket = int(h[:8], 16) / float(0xFFFFFFFF + 1)

        cumulative = 0.0
        for version, weight in sorted(split.items()):
            cumulative += weight
            if bucket < cumulative:
                return version

        return list(split.keys())[-1]  # floating point safety


if __name__ == "__main__":
    # Demo the router
    router = TrafficRouter()
    router.set_split("prompt_x", {"v1": 0.9, "v2": 0.1})

    counts = {"v1": 0, "v2": 0}
    for i in range(1000):
        v = router.pick("prompt_x", f"user_{i}")
        counts[v] += 1

    print(f"Traffic split on 1000 users: {counts}")
    # Expected: ~900 v1, ~100 v2
