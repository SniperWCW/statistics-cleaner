"""Regression tests for point outlier detection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from custom_components.statistics_cleaner.cleaner import StatisticsCleaner, _StatisticRow


def _rows(values: list[float]) -> list[_StatisticRow]:
    """Build minimal statistics rows for detector tests."""
    return [
        _StatisticRow(
            table="statistics",
            row_id=index + 1,
            start_ts=float(index),
            start=f"2026-01-01T{index:02d}:00:00+00:00",
            target_column="mean",
            value=value,
        )
        for index, value in enumerate(values)
    ]


class PointOutlierDetectionTest(unittest.TestCase):
    """Cover both repeated and ordinary point values."""

    def test_corrects_a_short_repeated_outlier_segment(self) -> None:
        with tempfile.TemporaryDirectory() as storage_path:
            cleaner = StatisticsCleaner(Path(storage_path))
            candidates = cleaner._find_point_candidates(
                rows=_rows([10.0, 10.0, 100.0, 100.0, 100.0, 10.0, 10.0]),
                threshold=1.0,
            )

        self.assertEqual([candidate.row_id for candidate in candidates], [3, 4, 5])
        self.assertTrue(all(candidate.suggested_value == 10.0 for candidate in candidates))

    def test_keeps_an_ordinary_progression(self) -> None:
        with tempfile.TemporaryDirectory() as storage_path:
            cleaner = StatisticsCleaner(Path(storage_path))
            candidates = cleaner._find_point_candidates(
                rows=_rows([10.0, 11.0, 12.0, 13.0, 14.0]),
                threshold=0.5,
            )

        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
