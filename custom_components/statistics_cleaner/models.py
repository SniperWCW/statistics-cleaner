"""Shared models for Statistics Cleaner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OutlierCandidate:
    """Represents a suspicious statistics point."""

    row_id: int
    table: str
    target_column: str
    timestamp: str
    original_value: float
    suggested_value: float
    reason: str


@dataclass(slots=True)
class ScanResult:
    """Represents the result of a scan run."""

    entity_id: str
    threshold: float
    window_hours: int
    candidates: list[OutlierCandidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a Home Assistant friendly dictionary."""
        return {
            "entity_id": self.entity_id,
            "threshold": self.threshold,
            "window_hours": self.window_hours,
            "candidates": [
                {
                    "row_id": item.row_id,
                    "table": item.table,
                    "target_column": item.target_column,
                    "timestamp": item.timestamp,
                    "original_value": item.original_value,
                    "suggested_value": item.suggested_value,
                    "reason": item.reason,
                }
                for item in self.candidates
            ],
        }
