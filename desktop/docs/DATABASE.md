# Database

On Windows, `AppDatabase` opens `media.sqlite` below the application support directory in a `SaraeLiveStudio` subfolder. It is never placed in the repository or deleted during startup. Drift schema version is 1.

## Tables

- `media_records`: ID primary key, name, original path, byte size, import time, product code, presenter, description, JSON tags, enabled flag.
- `app_metadata`: key/value markers, including `legacy_media_v1=done`.

## Legacy migration

On first load, the desktop repository reads `sarae.media.v1` from `shared_preferences`. It validates the complete JSON list, required fields, dates, tags, nonnegative sizes, and unique IDs before writing. Drift imports all entries and writes the completion marker in one SQLite transaction. A failure rolls back both rows and marker. The source preferences value is retained, including after success, as a recovery copy. Later loads skip import when the marker exists, so edits and deletions in SQLite cannot be overwritten by stale legacy data.

The database migration strategy creates version 1 for new installs. An unknown future upgrade is rejected explicitly until a stepwise migration is added; it does not recreate or erase the database. Before changing the schema, increase `schemaVersion`, add an `onUpgrade` step, regenerate `app_database.g.dart`, and test an upgrade from the previous version.

Web uses a separate browser preview repository in preferences. It is not a SQLite database and is not synchronized with desktop.
