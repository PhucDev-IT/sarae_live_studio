# Sarae Live Studio — Architecture

## Target platforms
- **Windows:** primary desktop operator app.
- **Web:** compatible metadata/dashboard UI. A browser cannot hold durable unrestricted access to arbitrary local files.
- **Python engine (future phase):** isolated FastAPI process for FFmpeg, OBS, playlist and AI orchestration. UI must communicate via an API instead of importing platform-only libraries.

## Phase 1
- Flutter Material 3 desktop shell and media catalog.
- Import MP4, MOV, M4V and WebM metadata with file_picker.
- Edit product code, presenter, description, tags and activation state.
- Persist a small local metadata catalog with shared_preferences for both Windows and Web.
- Video binaries are **not** copied or uploaded.
- Web picker returns filename/size but generally no durable absolute path; web entries are therefore metadata-only.

## Not yet implemented
- SQLite/Drift migration and schema (target for larger catalogs).
- Thumbnail generation, ffprobe analysis, video playback and clipping.
- Product catalog relational mapping, playlist scheduler, pin adapter, OBS integration and AI.
- File relocation tracking and missing-file recovery.

## Design principles
- Separate presentation, controller, metadata model and repository.
- Treat video files as external references, not persisted byte blobs.
- Do not block rendering while importing metadata.
- Never infer that a remote product pin succeeded without an acknowledgment.
