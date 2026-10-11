"""An original, single-process queue for pending note-review jobs.

Only pending keys are deduplicated. Taking a job releases its key. This small
teaching example has no worker threads, database, network or retry guarantee.
"""
from __future__ import annotations


class NoteQueue:
    def __init__(self, capacity: int):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._pending: dict[str, dict[str, str]] = {}

    @staticmethod
    def _key(raw: str) -> str:
        if not isinstance(raw, str):
            raise ValueError("key must be text")
        normalized = raw.strip().casefold()
        if not normalized:
            raise ValueError("key must not be blank")
        return normalized

    def enqueue(self, raw_key: str, text: str) -> bool:
        key = self._key(raw_key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be blank")
        if key in self._pending:
            return False
        if len(self._pending) >= self._capacity:
            raise OverflowError("pending queue is full")
        self._pending[key] = {"key": key, "text": text.strip()}
        return True

    def take(self) -> dict[str, str] | None:
        if not self._pending:
            return None
        oldest_key = next(iter(self._pending))
        return dict(self._pending.pop(oldest_key))

    def snapshot(self) -> list[dict[str, str]]:
        return [dict(job) for job in self._pending.values()]
