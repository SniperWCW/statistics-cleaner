"""SQLite-backed cleaner implementation for Statistics Cleaner."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

try:
    from homeassistant.exceptions import HomeAssistantError
except ImportError:
    class HomeAssistantError(Exception):
        """Fallback error used when Home Assistant is not installed."""

from .models import OutlierCandidate, ScanResult

_LOGGER = logging.getLogger(__name__)
_SUPPORTED_TABLES = ("statistics", "statistics_short_term")
_MAX_SUM_SEGMENT_LENGTHS = {
    "statistics": 12,
    "statistics_short_term": 24,
}
_SUM_HISTORY_WINDOW = 6
_SUM_CONFIRMATION_WINDOW = 3
_MAX_POINT_SEGMENT_LENGTHS = {
    "statistics": 12,
    "statistics_short_term": 24,
}


@dataclass(slots=True)
class ApplyResult:
    """Represents the outcome of applying a preview run."""

    backup_path: str
    applied_count: int


@dataclass(slots=True)
class _StatisticRow:
    """One statistics row loaded from SQLite."""

    table: str
    row_id: int
    start_ts: float | None
    start: str | None
    target_column: str
    value: float


class StatisticsCleaner:
    """Coordinates scan, preview, backup, and apply workflows."""

    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._preview_path = storage_path / "previews"
        self._backup_path = storage_path / "backups"
        self._preview_path.mkdir(parents=True, exist_ok=True)
        self._backup_path.mkdir(parents=True, exist_ok=True)

    async def scan(
        self,
        *,
        entry_id: str,
        entity_id: str,
        threshold: float,
        window_hours: int,
        database_path: Path,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> ScanResult:
        """Scan SQLite statistics rows and persist a preview."""
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            self._scan_sync,
            entry_id,
            entity_id,
            threshold,
            window_hours,
            database_path,
            start_at,
            end_at,
        )
        return result

    async def create_backup(self, *, entry_id: str) -> str:
        """Create a JSON backup file for the latest preview rows."""
        preview = self._load_preview(entry_id=entry_id)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_file = self._backup_path / f"{entry_id}_{timestamp}.json"
        backup_file.write_text(json.dumps(preview, indent=2), encoding="utf-8")
        return str(backup_file)

    async def apply_preview(self, *, entry_id: str) -> ApplyResult:
        """Apply the latest preview to the SQLite database."""
        backup_path = await self.create_backup(entry_id=entry_id)
        loop = asyncio.get_running_loop()
        applied_count = await loop.run_in_executor(None, self._apply_preview_sync, entry_id)
        return ApplyResult(backup_path=backup_path, applied_count=applied_count)

    def _scan_sync(
        self,
        entry_id: str,
        entity_id: str,
        threshold: float,
        window_hours: int,
        database_path: Path,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> ScanResult:
        database_path = self._validate_database_path(database_path)
        rows = self._load_rows(
            database_path=database_path,
            entity_id=entity_id,
            window_hours=window_hours,
            start_at=start_at,
            end_at=end_at,
        )

        grouped_rows: dict[tuple[str, str], list[_StatisticRow]] = defaultdict(list)
        for row in rows:
            grouped_rows[(row.table, row.target_column)].append(row)

        candidates: list[OutlierCandidate] = []
        for (table, target_column), group in grouped_rows.items():
            if target_column == "sum":
                candidates.extend(self._find_sum_candidates(group, threshold))
                continue

            candidates.extend(self._find_point_candidates(group, threshold))

        candidates.sort(key=lambda item: (item.timestamp, item.table, item.row_id))

        result = ScanResult(
            entity_id=entity_id,
            threshold=threshold,
            window_hours=window_hours,
            start_at=start_at.isoformat() if start_at else None,
            end_at=end_at.isoformat() if end_at else None,
            scanned_rows=len(rows),
            candidates=candidates,
        )
        self._save_preview(
            entry_id=entry_id,
            payload={
                "entry_id": entry_id,
                "entity_id": entity_id,
                "threshold": threshold,
                "window_hours": window_hours,
                "database_path": str(database_path),
                "start_at": start_at.isoformat() if start_at else None,
                "end_at": end_at.isoformat() if end_at else None,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "candidates": [asdict(candidate) for candidate in candidates],
            },
        )
        return result

    def _find_sum_candidates(
        self,
        rows: list[_StatisticRow],
        threshold: float,
    ) -> list[OutlierCandidate]:
        """Find anomalous level-shift segments in cumulative sum rows."""
        original_values = {row.row_id: row.value for row in rows}
        candidate_map: dict[int, OutlierCandidate] = {}
        index = 1

        while index < len(rows):
            jump_delta = rows[index].value - rows[index - 1].value
            if abs(jump_delta) <= threshold:
                index += 1
                continue

            exit_index = self._find_matching_sum_exit(
                rows=rows,
                start_index=index,
                jump_delta=jump_delta,
                threshold=threshold,
            )
            if exit_index is not None:
                self._apply_sum_offset(
                    rows=rows,
                    original_values=original_values,
                    candidate_map=candidate_map,
                    start_index=index,
                    end_index=exit_index,
                    offset=jump_delta,
                    reason=(
                        f"sum level shift of {jump_delta:.3f} detected between "
                        f"{rows[index - 1].start or self._format_timestamp(rows[index - 1].start_ts)} "
                        f"and {rows[exit_index].start or self._format_timestamp(rows[exit_index].start_ts)}"
                    ),
                )
                index += 1
                continue

            offset = self._estimate_persistent_sum_offset(
                rows=rows,
                start_index=index,
                threshold=threshold,
            )
            if offset is None:
                index += 1
                continue

            self._apply_sum_offset(
                rows=rows,
                original_values=original_values,
                candidate_map=candidate_map,
                start_index=index,
                end_index=len(rows),
                offset=offset,
                reason=(
                    f"persistent sum shift of {offset:.3f} detected from "
                    f"{rows[index].start or self._format_timestamp(rows[index].start_ts)} onward"
                ),
            )
            index += 1

        return sorted(candidate_map.values(), key=lambda item: (item.timestamp, item.row_id))

    def _estimate_persistent_sum_offset(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        threshold: float,
    ) -> float | None:
        """Estimate a persistent offset using pre-jump slope and early post-jump levels."""
        baseline_delta = self._baseline_sum_delta(rows=rows, index=start_index)
        if baseline_delta is None:
            return None

        if not self._is_persistent_sum_shift(
            rows=rows,
            start_index=start_index,
            baseline_delta=baseline_delta,
            threshold=threshold,
        ):
            return None

        anchor_value = rows[start_index - 1].value
        confirmation_end = min(len(rows), start_index + 1 + _SUM_CONFIRMATION_WINDOW)
        offsets = []
        for index in range(start_index, confirmation_end):
            expected_value = anchor_value + baseline_delta * (index - start_index + 1)
            offsets.append(rows[index].value - expected_value)

        if not offsets:
            return None

        offset = float(median(offsets))
        if abs(offset) <= threshold:
            return None
        return offset

    def _apply_sum_offset(
        self,
        *,
        rows: list[_StatisticRow],
        original_values: dict[int, float],
        candidate_map: dict[int, OutlierCandidate],
        start_index: int,
        end_index: int,
        offset: float,
        reason: str,
    ) -> None:
        """Apply an offset to the working rows and accumulate preview candidates."""
        for segment_index in range(start_index, end_index):
            row = rows[segment_index]
            row.value -= offset

            if row.row_id in candidate_map:
                candidate = candidate_map[row.row_id]
                candidate.suggested_value -= offset
                candidate.reason = f"{candidate.reason}; {reason}"
                continue

            candidate_map[row.row_id] = OutlierCandidate(
                row_id=row.row_id,
                table=row.table,
                target_column=row.target_column,
                timestamp=row.start or self._format_timestamp(row.start_ts),
                original_value=original_values[row.row_id],
                suggested_value=original_values[row.row_id] - offset,
                reason=reason,
            )

    def _baseline_sum_delta(
        self,
        *,
        rows: list[_StatisticRow],
        index: int,
    ) -> float | None:
        """Estimate the local expected increment before a suspected jump."""
        start_index = max(1, index - _SUM_HISTORY_WINDOW)
        deltas = [
            rows[position].value - rows[position - 1].value
            for position in range(start_index, index)
        ]
        if len(deltas) < 2:
            return None
        return float(median(deltas))

    def _is_persistent_sum_shift(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        baseline_delta: float,
        threshold: float,
    ) -> bool:
        """Check whether post-jump values continue with a normal slope but shifted level."""
        confirmation_end = min(len(rows), start_index + 1 + _SUM_CONFIRMATION_WINDOW)
        if confirmation_end <= start_index + 1:
            return False

        tolerance = max(threshold, abs(baseline_delta) * 0.5, 0.2)
        matching_steps = 0
        for index in range(start_index + 1, confirmation_end):
            delta = rows[index].value - rows[index - 1].value
            if abs(delta - baseline_delta) <= tolerance:
                matching_steps += 1

        required_matches = max(1, confirmation_end - start_index - 2)
        return matching_steps >= required_matches

    def _find_matching_sum_exit(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        jump_delta: float,
        threshold: float,
    ) -> int | None:
        """Find the matching opposite jump that closes a sum offset segment."""
        row = rows[start_index]
        max_segment_length = _MAX_SUM_SEGMENT_LENGTHS.get(row.table, 24 * 14)
        max_index = min(len(rows), start_index + max_segment_length + 1)
        tolerance = max(threshold, abs(jump_delta) * 0.15)
        best_index: int | None = None
        best_score: tuple[float, int] | None = None

        for index in range(start_index + 1, max_index):
            exit_delta = rows[index].value - rows[index - 1].value
            if jump_delta == 0:
                continue
            if exit_delta == 0 or (jump_delta > 0) == (exit_delta > 0):
                continue
            mismatch = abs(jump_delta + exit_delta)
            if mismatch > tolerance:
                continue

            segment_length = index - start_index
            score = (mismatch, segment_length)
            if best_score is None or score < best_score:
                best_index = index
                best_score = score

        return best_index

    def _find_point_candidates(
        self,
        rows: list[_StatisticRow],
        threshold: float,
    ) -> list[OutlierCandidate]:
        """Find isolated and short multi-point anomalies for non-cumulative statistics."""
        candidates: list[OutlierCandidate] = []
        index = 1
        while index < len(rows) - 1:
            segment_end = self._find_point_segment_end(
                rows=rows,
                start_index=index,
                threshold=threshold,
            )
            if segment_end is None:
                index += 1
                continue

            previous_row = rows[index - 1]
            next_row = rows[segment_end + 1]
            span = segment_end - index + 2

            for position in range(index, segment_end + 1):
                current_row = rows[position]
                distance = position - index + 1
                suggested_value = previous_row.value + (
                    (next_row.value - previous_row.value) * distance / span
                )
                delta = abs(current_row.value - suggested_value)

                if segment_end == index:
                    reason = (
                        f"{current_row.target_column} deviates by {delta:.3f} from the "
                        "neighbour average"
                    )
                else:
                    reason = (
                        f"{current_row.target_column} is part of a {segment_end - index + 1}-point "
                        f"outlier segment and deviates by {delta:.3f} from the interpolated trend"
                    )

                candidates.append(
                    OutlierCandidate(
                        row_id=current_row.row_id,
                        table=current_row.table,
                        target_column=current_row.target_column,
                        timestamp=current_row.start
                        or self._format_timestamp(current_row.start_ts),
                        original_value=current_row.value,
                        suggested_value=suggested_value,
                        reason=reason,
                    )
                )

            index = segment_end + 1

        return candidates

    def _find_point_segment_end(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        threshold: float,
    ) -> int | None:
        """Return the last index of an anomalous point segment if one is found."""
        max_length = _MAX_POINT_SEGMENT_LENGTHS.get(rows[start_index].table, 24)
        max_end = min(len(rows) - 1, start_index + max_length)
        best_end: int | None = None
        best_score: tuple[int, float] | None = None

        for segment_end in range(start_index, max_end):
            next_anchor_index = segment_end + 1
            if next_anchor_index >= len(rows):
                break

            deviations = self._segment_deviations(
                rows=rows,
                start_index=start_index,
                segment_end=segment_end,
            )
            if not deviations:
                continue

            max_deviation = max(deviations)
            if max_deviation <= threshold:
                continue

            if not self._segment_has_stable_anchors(
                rows=rows,
                start_index=start_index,
                segment_end=segment_end,
                threshold=threshold,
            ):
                continue

            if not self._segment_has_discontinuous_edges(
                rows=rows,
                start_index=start_index,
                segment_end=segment_end,
                threshold=threshold,
            ):
                continue

            if not self._segment_needs_correction(
                deviations=deviations,
                threshold=threshold,
                segment_length=segment_end - start_index + 1,
            ):
                continue

            score = (segment_end - start_index + 1, max_deviation)
            if best_score is None or score < best_score:
                best_end = segment_end
                best_score = score

        return best_end

    def _segment_deviations(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        segment_end: int,
    ) -> list[float]:
        """Measure row deviations from a line between the outer anchor points."""
        previous_row = rows[start_index - 1]
        next_row = rows[segment_end + 1]
        span = segment_end - start_index + 2
        deviations: list[float] = []

        for position in range(start_index, segment_end + 1):
            distance = position - start_index + 1
            expected_value = previous_row.value + (
                (next_row.value - previous_row.value) * distance / span
            )
            deviations.append(abs(rows[position].value - expected_value))

        return deviations

    def _segment_has_stable_anchors(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        segment_end: int,
        threshold: float,
    ) -> bool:
        """Check that values before and after the segment follow a compatible trend."""
        previous_row = rows[start_index - 1]
        next_row = rows[segment_end + 1]
        anchor_delta = abs(next_row.value - previous_row.value)

        if start_index == 1 and segment_end + 2 >= len(rows):
            return True

        left_ok = True
        if start_index >= 2:
            left_delta = abs(previous_row.value - rows[start_index - 2].value)
            left_ok = anchor_delta <= max(threshold * 1.5, left_delta * 3, 0.5)

        right_ok = True
        if segment_end + 2 < len(rows):
            right_delta = abs(rows[segment_end + 2].value - next_row.value)
            right_ok = anchor_delta <= max(threshold * 1.5, right_delta * 3, 0.5)

        return left_ok and right_ok

    def _segment_has_discontinuous_edges(
        self,
        *,
        rows: list[_StatisticRow],
        start_index: int,
        segment_end: int,
        threshold: float,
    ) -> bool:
        """Require a clear jump at both boundaries of a proposed segment."""
        left_jump = abs(rows[start_index].value - rows[start_index - 1].value)
        right_jump = abs(rows[segment_end].value - rows[segment_end + 1].value)
        return left_jump > threshold and right_jump > threshold

    def _segment_needs_correction(
        self,
        *,
        deviations: list[float],
        threshold: float,
        segment_length: int,
    ) -> bool:
        """Require strong enough deviation to avoid changing ordinary noise."""
        if segment_length == 1:
            return deviations[0] > threshold

        average_deviation = sum(deviations) / len(deviations)
        significant_points = sum(1 for item in deviations if item > threshold)
        return average_deviation > threshold and significant_points >= max(2, segment_length // 2)

    def _apply_preview_sync(self, entry_id: str) -> int:
        preview = self._load_preview(entry_id=entry_id)
        database_path = self._validate_database_path(Path(preview["database_path"]))
        candidates = preview["candidates"]
        if not candidates:
            return 0

        connection = sqlite3.connect(database_path)
        try:
            cursor = connection.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            for candidate in candidates:
                table = candidate["table"]
                target_column = candidate["target_column"]
                row_id = candidate["row_id"]
                suggested_value = candidate["suggested_value"]

                if table not in _SUPPORTED_TABLES:
                    raise HomeAssistantError(f"Unsupported statistics table: {table}")
                if target_column not in {"state", "sum", "mean"}:
                    raise HomeAssistantError(f"Unsupported target column: {target_column}")

                cursor.execute(
                    f"UPDATE {table} SET {target_column} = ? WHERE id = ?",
                    (suggested_value, row_id),
                )

            connection.commit()
        except sqlite3.Error as err:
            connection.rollback()
            raise HomeAssistantError(f"Failed to update SQLite statistics: {err}") from err
        finally:
            connection.close()

        return len(candidates)

    def _load_rows(
        self,
        *,
        database_path: Path,
        entity_id: str,
        window_hours: int,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[_StatisticRow]:
        metadata_ids = self._load_metadata_ids(database_path=database_path, entity_id=entity_id)
        if not metadata_ids:
            raise HomeAssistantError(
                f"No statistics metadata found for entity '{entity_id}' in {database_path}"
            )

        if start_at is None:
            start_at = datetime.now(tz=UTC) - timedelta(hours=window_hours)
        if end_at is None:
            end_at = datetime.now(tz=UTC)
        if start_at > end_at:
            raise HomeAssistantError("start_at must be before end_at.")

        min_start_ts = start_at.timestamp()
        max_start_ts = end_at.timestamp()
        rows: list[_StatisticRow] = []
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            cursor = connection.cursor()
            for table in _SUPPORTED_TABLES:
                placeholders = ",".join("?" for _ in metadata_ids)
                cursor.execute(
                    (
                        f"SELECT id, start_ts, start, state, sum, mean "
                        f"FROM {table} "
                        f"WHERE metadata_id IN ({placeholders}) "
                        "AND COALESCE(start_ts, 0) >= ? "
                        "AND COALESCE(start_ts, 0) <= ? "
                        "ORDER BY COALESCE(start_ts, 0) ASC, id ASC"
                    ),
                    [*metadata_ids, min_start_ts, max_start_ts],
                )
                for row in cursor.fetchall():
                    target_column, value = self._pick_target_column(row)
                    if target_column is None or value is None:
                        continue
                    rows.append(
                        _StatisticRow(
                            table=table,
                            row_id=row["id"],
                            start_ts=row["start_ts"],
                            start=row["start"],
                            target_column=target_column,
                            value=float(value),
                        )
                    )
        except sqlite3.Error as err:
            raise HomeAssistantError(f"Failed to read SQLite statistics: {err}") from err
        finally:
            connection.close()

        rows.sort(key=lambda item: ((item.start_ts or 0), item.table, item.row_id))
        return rows

    def _load_metadata_ids(self, *, database_path: Path, entity_id: str) -> list[int]:
        connection = sqlite3.connect(database_path)
        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT id FROM statistics_meta WHERE statistic_id = ? ORDER BY id ASC",
                (entity_id,),
            )
            return [int(row[0]) for row in cursor.fetchall()]
        except sqlite3.Error as err:
            raise HomeAssistantError(f"Failed to read statistics metadata: {err}") from err
        finally:
            connection.close()

    def _pick_target_column(self, row: sqlite3.Row) -> tuple[str | None, float | None]:
        for column in ("sum", "state", "mean"):
            value = row[column]
            if value is not None:
                return column, float(value)
        return None, None

    def _preview_file(self, *, entry_id: str) -> Path:
        return self._preview_path / f"{entry_id}.json"

    def _save_preview(self, *, entry_id: str, payload: dict) -> None:
        self._preview_file(entry_id=entry_id).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def _load_preview(self, *, entry_id: str) -> dict:
        preview_file = self._preview_file(entry_id=entry_id)
        if not preview_file.exists():
            raise HomeAssistantError(
                "No preview was found for this entry. Run scan_outliers first."
            )
        return json.loads(preview_file.read_text(encoding="utf-8"))

    def _validate_database_path(self, database_path: Path) -> Path:
        resolved = database_path.expanduser().resolve()
        if not resolved.exists():
            raise HomeAssistantError(f"SQLite database not found: {resolved}")
        if resolved.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            _LOGGER.warning("Using SQLite file with uncommon suffix: %s", resolved)
        return resolved

    def _format_timestamp(self, timestamp: float | None) -> str:
        if timestamp is None:
            return ""
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
