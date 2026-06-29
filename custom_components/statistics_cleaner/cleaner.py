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

from homeassistant.exceptions import HomeAssistantError

from .models import OutlierCandidate, ScanResult

_LOGGER = logging.getLogger(__name__)
_SUPPORTED_TABLES = ("statistics", "statistics_short_term")
_MAX_SUM_SEGMENT_LENGTHS = {
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
        candidates: list[OutlierCandidate] = []
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
            if exit_index is None:
                index += 1
                continue

            for segment_index in range(index, exit_index):
                row = rows[segment_index]
                corrected_value = row.value - jump_delta
                candidates.append(
                    OutlierCandidate(
                        row_id=row.row_id,
                        table=row.table,
                        target_column=row.target_column,
                        timestamp=row.start or self._format_timestamp(row.start_ts),
                        original_value=row.value,
                        suggested_value=corrected_value,
                        reason=(
                            f"sum level shift of {jump_delta:.3f} detected between "
                            f"{rows[index - 1].start or self._format_timestamp(rows[index - 1].start_ts)} "
                            f"and {rows[exit_index].start or self._format_timestamp(rows[exit_index].start_ts)}"
                        ),
                    )
                )

            index = exit_index + 1

        return candidates

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
        """Find isolated point anomalies for non-cumulative statistics."""
        candidates: list[OutlierCandidate] = []
        for index in range(1, len(rows) - 1):
            previous_row = rows[index - 1]
            current_row = rows[index]
            next_row = rows[index + 1]

            suggested_value = (previous_row.value + next_row.value) / 2
            delta = abs(current_row.value - suggested_value)
            if delta <= threshold:
                continue

            candidates.append(
                OutlierCandidate(
                    row_id=current_row.row_id,
                    table=current_row.table,
                    target_column=current_row.target_column,
                    timestamp=current_row.start or self._format_timestamp(current_row.start_ts),
                    original_value=current_row.value,
                    suggested_value=suggested_value,
                    reason=(
                        f"{current_row.target_column} deviates by {delta:.3f} from the "
                        "neighbour average"
                    ),
                )
            )

        return candidates

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
