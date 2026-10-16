# Architecture

Sarae Live Studio uses a small feature-first, layered structure:

- `lib/app`: router, theme, and navigation shell.
- `lib/core/database`: Drift schema and platform-specific connection.
- `lib/features/media_library`: model, repository, controller, and screen.
- Other routes are explicit placeholders until their modules are implemented.

The UI invokes `MediaController`; the controller persists through `MediaRepository`. Riverpod provides the database, repository, controller, router, and theme state. `go_router` maps each sidebar item to its own route. Local UI selection and collapsed sidebar use widget state.

Windows uses Drift with SQLite. The conditional connection import keeps `dart:io` out of Web compilation. Web uses a browser-only preferences repository for metadata preview and has no synchronization with Windows.

To add a module: create a directory under `lib/features`, separate UI from persistence, expose dependencies through Riverpod, and register its route and sidebar entry in `lib/app/app.dart`. Add a Drift migration step before changing a persisted schema. Keep Windows integrations in platform-specific files.

The Python engine, FFmpeg, OBS, playlists, products, presenters, AI, thumbnail generation, video preview, and editor are outside Task 01.
