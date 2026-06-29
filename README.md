# Statistics Cleaner

`Statistics Cleaner` is a Home Assistant custom integration for reviewing and correcting outliers in recorder statistics, starting with direct local SQLite recorder database support.

## Status

This repository now contains a working SQLite-first prototype:

- Home Assistant config flow
- HACS metadata
- service registration
- SQLite-backed scan workflow against Home Assistant recorder statistics
- persisted previews per config entry
- JSON backup export before writes
- transactional apply step for suggested corrections

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

## Next implementation steps

1. Improve the outlier detection strategy beyond neighbour averaging.
2. Extend writes to adjust related columns more intelligently for every statistics type.
3. Add undo-from-backup import support.
4. Add MariaDB support behind a dedicated adapter.
