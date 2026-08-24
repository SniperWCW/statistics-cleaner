"""Local CLI for scanning and correcting Home Assistant recorder statistics."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from custom_components.statistics_cleaner.cleaner import StatisticsCleaner


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if len(normalized) == 10:
        parsed = datetime.fromisoformat(f"{normalized}T00:00:00")
    else:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan Home Assistant recorder statistics for outliers and optionally "
            "apply all suggested corrections directly to the SQLite database."
        )
    )
    parser.add_argument("--entity-id", required=True, help="Statistics entity_id, e.g. sensor.energy_total")
    parser.add_argument("--db-path", required=True, help="Path to home-assistant_v2.db")
    parser.add_argument(
        "--storage-path",
        default=".statistics_cleaner",
        help="Directory used for previews and backups",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.15,
        help="Maximum allowed deviation before a row is flagged",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="Time window in hours when no explicit start/end are supplied",
    )
    parser.add_argument("--start-at", help="Optional ISO start date or datetime")
    parser.add_argument("--end-at", help="Optional ISO end date or datetime")
    parser.add_argument(
        "--entry-id",
        default="cli",
        help="Preview namespace identifier used for preview/backup files",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the latest preview after scanning",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full result as JSON",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cleaner = StatisticsCleaner(Path(args.storage_path))
    scan_result = await cleaner.scan(
        entry_id=args.entry_id,
        entity_id=args.entity_id,
        threshold=args.threshold,
        window_hours=args.window_hours,
        database_path=Path(args.db_path),
        start_at=_parse_datetime(args.start_at),
        end_at=_parse_datetime(args.end_at),
    )

    payload: dict[str, Any] = {
        "scan_result": scan_result.as_dict(),
    }

    if args.apply:
        apply_result = await cleaner.apply_preview(entry_id=args.entry_id)
        payload["apply_result"] = {
            "backup_path": apply_result.backup_path,
            "applied_count": apply_result.applied_count,
        }

    return payload


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    payload = asyncio.run(_run(args))

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    scan_result = payload["scan_result"]
    print(
        f"Scan complete: {scan_result['scanned_rows']} rows checked, "
        f"{len(scan_result['candidates'])} candidate(s) found."
    )

    for candidate in scan_result["candidates"]:
        print(
            f"- {candidate['timestamp']} | {candidate['table']}#{candidate['row_id']} | "
            f"{candidate['original_value']} -> {candidate['suggested_value']} | "
            f"{candidate['reason']}"
        )

    apply_result = payload.get("apply_result")
    if apply_result is not None:
        print(
            f"Applied {apply_result['applied_count']} change(s). "
            f"Backup: {apply_result['backup_path']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
