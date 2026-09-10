# Forecast snapshots

Varje publicerad prognos ska sparas som en ny, immutable fil med:

- `forecast_timestamp`
- `data_cutoff`
- `git_commit`
- `source_manifest_hash`
- `model_version`
- `random_seed`
- `prediction`

Den officiella prognosen före valresultatet 2026 får aldrig skrivas över.

