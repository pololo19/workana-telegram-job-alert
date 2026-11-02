from __future__ import annotations

import json
from pathlib import Path


class SeenJobStore:
    """Persists job slugs we've already notified about."""

    def __init__(self, path: Path, *, max_entries: int = 5000) -> None:
        self.path = path
        self.max_entries = max_entries
        self._slugs: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._slugs = []
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._slugs = list(data.get("slugs", []))
        except (json.JSONDecodeError, OSError):
            self._slugs = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"slugs": self._slugs[-self.max_entries :]}, indent=2),
            encoding="utf-8",
        )

    def contains(self, slug: str) -> bool:
        return slug in self._slugs

    def add(self, slug: str) -> None:
        if slug not in self._slugs:
            self._slugs.append(slug)
        if len(self._slugs) > self.max_entries:
            self._slugs = self._slugs[-self.max_entries :]

    def mark_many(self, slugs: list[str]) -> None:
        for slug in slugs:
            self.add(slug)
        self.save()
