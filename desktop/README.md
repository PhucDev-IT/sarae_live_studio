# Sarae Live Studio

Flutter Windows workspace for coordinating video based sales livestreams. Web currently provides a separate browser preview catalog; its data does not sync with Windows.

## Implemented in Task 01

- Responsive desktop shell with nine routes, collapsible sidebar, 404 fallback and light/dark mode.
- Media Library: multi-video import, search, filters, metadata editing, activation, and metadata-only deletion. Original files are never deleted.
- Windows SQLite catalog via Drift in the application support directory.
- One-time, transactional migration from the previous `shared_preferences` catalog.

## Run

```bash
git checkout dev
flutter pub get
flutter run -d windows
```

For browser preview: `flutter run -d chrome`. Browser entries contain metadata only because a browser does not expose a durable native path.

## Verify

```bash
dart format lib test
flutter analyze
flutter test
flutter build windows
flutter build web
```

See [architecture](docs/ARCHITECTURE.md), [database](docs/DATABASE.md), and [roadmap](docs/ROADMAP.md).
