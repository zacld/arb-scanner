"""Tiny disk + in-run cache so trend sources aren't re-fetched repeatedly.

Signals from a provider are cached for a short TTL (default 6h) in the user's
home dir — keeps scans fast and avoids hammering a source within a run or day.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from .base import TrendSignal

log = logging.getLogger(__name__)

CACHE_PATH = Path.home() / ".arbfinder-trends-cache.json"
DEFAULT_TTL = 6 * 3600.0

_MEMO: dict[str, list[TrendSignal]] = {}  # in-run, survives one process


def _load() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _store(data: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        log.debug("Could not write trend cache: %s", exc)


def cached_signals(key: str, ttl: float, producer, fresh: bool = False) -> list[TrendSignal]:
    """Return ``producer()``'s signals, served from memo/disk within ``ttl``."""
    if not fresh and key in _MEMO:
        return _MEMO[key]
    disk = _load()
    entry = disk.get(key)
    if not fresh and entry and (time.time() - entry.get("ts", 0)) < ttl:
        signals = [TrendSignal(**d) for d in entry.get("signals", [])]
        _MEMO[key] = signals
        return signals
    signals = list(producer())
    _MEMO[key] = signals
    disk[key] = {"ts": time.time(), "signals": [asdict(s) for s in signals]}
    _store(disk)
    return signals
