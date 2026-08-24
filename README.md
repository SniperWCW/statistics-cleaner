# Statistics Cleaner

`Statistics Cleaner` is a Home Assistant custom integration for reviewing and correcting outliers in recorder statistics, starting with direct local SQLite recorder database support.

It now also includes a direct local CLI so statistics can be scanned and corrected in bulk without going through the Home Assistant "Adjust statistic" dialog for one value at a time.

## Status

This repository now contains a working SQLite-first prototype:

- Home Assistant config flow
- HACS metadata
- service registration
- SQLite-backed scan workflow against Home Assistant recorder statistics
- persisted previews per config entry
- JSON backup export before writes
- transactional apply step for suggested corrections
- direct CLI workflow for local bulk correction without the Home Assistant UI
- detection of short outlier segments with multiple consecutive bad values

## Planned features

- Select a statistics entity
- Define a fixed outlier threshold
- Point the integration at the SQLite recorder database file
- Scan a time range for suspicious points
- Preview proposed corrections
- Create a backup before each change run
- Apply all approved corrections
- Undo from backup

## Repository layout

```text
custom_components/statistics_cleaner/
```

## Development notes

- Initial target: Home Assistant recorder database on the local installation
- First supported backend: SQLite only
- MariaDB support can be added later behind a separate storage adapter

## Installation

1. Copy `custom_components/statistics_cleaner` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration via `Settings -> Devices & Services -> Add Integration`.
4. Enter your statistics entity and the SQLite path, for example `/config/home-assistant_v2.db`.
5. Call the `statistics_cleaner.scan_outliers` service to create a preview.
6. Review the returned candidates and then call `statistics_cleaner.apply_preview`.

## Direct local CLI usage

You can also work directly against the SQLite recorder database without using the Home Assistant UI:

```powershell
python statistics_cleaner_cli.py `
  --entity-id sensor.energy_total `
  --db-path C:\path\to\home-assistant_v2.db `
  --start-at 2026-06-01 `
  --end-at 2026-06-30 `
  --threshold 0.15 `
  --json
```

To apply all suggested corrections after the scan:

```powershell
python statistics_cleaner_cli.py `
  --entity-id sensor.energy_total `
  --db-path C:\path\to\home-assistant_v2.db `
  --start-at 2026-06-01 `
  --end-at 2026-06-30 `
  --threshold 0.15 `
  --apply
```

The CLI stores previews and JSON backups in `.statistics_cleaner/` by default.

## Next implementation steps

1. Extend writes to adjust related columns more intelligently for every statistics type.
2. Add undo-from-backup import support.
3. Add MariaDB support behind a dedicated adapter.
