# Sarae Live Studio

Flutter Windows workspace for an internal video-based broadcast production tool, with a web-compatible catalog UI.

## Phase 1 — Media Library

Implemented:
- Responsive Desktop-style UI with sidebar, summary stats, filter and search.
- Import multiple MP4/MOV/M4V/WebM files via native picker.
- Metadata catalog: source filename/path, file size, product code, presenter, description, tags, active state.
- Edit/delete metadata, persist locally through `shared_preferences`.
- File originals are never deleted when removing a catalog entry.

Limitations:
- Does **not** yet extract durations, codecs, thumbnails or play video.
- Does **not** yet implement OBS, FFmpeg, AI, TikTok integration or product pinning.
- On web, the browser cannot provide a durable absolute local path; entries are metadata-only.
- SQLite/Drift migration is planned when catalog relations are implemented.

## Run

```bash
git checkout dev
flutter pub get
flutter run -d windows
```

For browser UI testing:

```bash
flutter run -d chrome
```

Checks:

```bash
dart format lib test
flutter analyze
flutter test
```

See [architecture](docs/ARCHITECTURE.md) for the module boundaries and roadmap.
