import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/database/app_database.dart';
import '../features/media_library/media_controller.dart';
import '../features/media_library/media_repository.dart';
import '../features/media_library/media_screen.dart';

final databaseProvider = Provider<AppDatabase>((ref) {
  final database = AppDatabase();
  ref.onDispose(database.close);
  return database;
});

final mediaRepositoryProvider = FutureProvider<MediaRepository>((ref) async {
  final prefs = await SharedPreferences.getInstance();
  final legacy = LegacyMediaStore(prefs);
  if (kIsWeb) return WebMediaRepository(legacy);
  return DesktopMediaRepository(ref.watch(databaseProvider), legacy);
});

final mediaControllerProvider = FutureProvider<MediaController>((ref) async {
  final repository = await ref.watch(mediaRepositoryProvider.future);
  final controller = MediaController(repository);
  await controller.initialize();
  ref.onDispose(controller.dispose);
  return controller;
});

class ThemeModeController extends Notifier<ThemeMode> {
  @override
  ThemeMode build() => ThemeMode.light;
  void toggle() =>
      state = state == ThemeMode.light ? ThemeMode.dark : ThemeMode.light;
}

final themeModeProvider = NotifierProvider<ThemeModeController, ThemeMode>(
  ThemeModeController.new,
);

const sections = <({String path, String title, IconData icon})>[
  (path: '/', title: 'Dashboard', icon: Icons.dashboard_outlined),
  (path: '/media', title: 'Media Library', icon: Icons.video_library_outlined),
  (path: '/products', title: 'Products', icon: Icons.inventory_2_outlined),
  (path: '/presenters', title: 'Presenters', icon: Icons.people_outline),
  (path: '/editor', title: 'Video Editor', icon: Icons.movie_filter_outlined),
  (path: '/playlists', title: 'Playlists', icon: Icons.playlist_play_outlined),
  (path: '/live', title: 'Live Control', icon: Icons.live_tv_outlined),
  (path: '/ai', title: 'AI Settings', icon: Icons.smart_toy_outlined),
  (path: '/settings', title: 'Settings', icon: Icons.settings_outlined),
];

final routerProvider = Provider<GoRouter>((ref) {
  final router = GoRouter(
    routes: [
      ShellRoute(
        builder: (context, state, child) =>
            AppShell(path: state.uri.path, child: child),
        routes: [
          for (final section in sections)
            GoRoute(
              path: section.path,
              builder: (context, state) => section.path == '/media'
                  ? const MediaPage()
                  : PlaceholderPage(title: section.title),
            ),
        ],
      ),
    ],
    errorBuilder: (context, state) => AppShell(
      path: state.uri.path,
      child: const PlaceholderPage(title: 'Page not found', isMissing: true),
    ),
  );
  ref.onDispose(router.dispose);
  return router;
});

class SaraeLiveStudio extends ConsumerWidget {
  const SaraeLiveStudio({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) => MaterialApp.router(
    title: 'Sarae Live Studio',
    debugShowCheckedModeBanner: false,
    themeMode: ref.watch(themeModeProvider),
    theme: _theme(Brightness.light),
    darkTheme: _theme(Brightness.dark),
    routerConfig: ref.watch(routerProvider),
  );
}

ThemeData _theme(Brightness brightness) {
  final scheme = ColorScheme.fromSeed(
    seedColor: const Color(0xFF6757DA),
    brightness: brightness,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: scheme.surface,
    cardTheme: CardThemeData(
      elevation: 0,
      color: scheme.surfaceContainerLowest,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: scheme.outlineVariant),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surfaceContainerLowest,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
    ),
  );
}

class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.path, required this.child});
  final String path;
  final Widget child;
  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  bool collapsed = false;
  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final current = sections.where((s) => s.path == widget.path).firstOrNull;
    final sidebar = _sidebar(context, current?.path, scheme);
    return LayoutBuilder(
      builder: (context, constraints) {
        final narrow = constraints.maxWidth < 800;
        return Scaffold(
          drawer: narrow ? Drawer(child: sidebar) : null,
          body: SafeArea(
            child: Row(
              children: [
                if (!narrow)
                  SizedBox(width: collapsed ? 72 : 220, child: sidebar),
                Expanded(
                  child: Column(
                    children: [
                      Container(
                        height: 58,
                        padding: const EdgeInsets.symmetric(horizontal: 20),
                        decoration: BoxDecoration(
                          color: scheme.surfaceContainerLowest,
                          border: Border(
                            bottom: BorderSide(color: scheme.outlineVariant),
                          ),
                        ),
                        child: Row(
                          children: [
                            if (narrow)
                              Builder(
                                builder: (context) => IconButton(
                                  tooltip: 'Open navigation',
                                  icon: const Icon(Icons.menu),
                                  onPressed: () =>
                                      Scaffold.of(context).openDrawer(),
                                ),
                              ),
                            Expanded(
                              child: Text(
                                'Workspace  /  ${current?.title ?? 'Page not found'}',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ),
                            const Spacer(),
                            const Icon(
                              Icons.desktop_windows_outlined,
                              size: 18,
                            ),
                            if (!narrow)
                              const Padding(
                                padding: EdgeInsets.only(left: 8),
                                child: Text('Local workspace'),
                              ),
                          ],
                        ),
                      ),
                      Expanded(child: widget.child),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _sidebar(
    BuildContext context,
    String? selected,
    ColorScheme scheme,
  ) => Material(
    color: scheme.surfaceContainerHigh,
    child: Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 16, 8, 12),
          child: Row(
            children: [
              Icon(Icons.live_tv_rounded, color: scheme.primary),
              if (!collapsed || MediaQuery.sizeOf(context).width < 800)
                const Expanded(
                  child: Padding(
                    padding: EdgeInsets.only(left: 8),
                    child: Text(
                      'SARAE STUDIO',
                      style: TextStyle(fontWeight: FontWeight.bold),
                    ),
                  ),
                ),
              if (MediaQuery.sizeOf(context).width >= 800)
                IconButton(
                  tooltip: collapsed ? 'Expand sidebar' : 'Collapse sidebar',
                  onPressed: () => setState(() => collapsed = !collapsed),
                  icon: Icon(
                    collapsed ? Icons.chevron_right : Icons.chevron_left,
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: ListView(
            children: [
              for (final section in sections)
                ListTile(
                  dense: true,
                  selected: selected == section.path,
                  leading: Icon(section.icon, size: 21),
                  title: collapsed && MediaQuery.sizeOf(context).width >= 800
                      ? null
                      : Text(section.title),
                  onTap: () {
                    if (MediaQuery.sizeOf(context).width < 800) {
                      Navigator.pop(context);
                    }
                    context.go(section.path);
                  },
                ),
            ],
          ),
        ),
        Consumer(
          builder: (context, ref, child) => IconButton(
            tooltip: 'Toggle light/dark theme',
            onPressed: () => ref.read(themeModeProvider.notifier).toggle(),
            icon: Icon(
              ref.watch(themeModeProvider) == ThemeMode.light
                  ? Icons.dark_mode_outlined
                  : Icons.light_mode_outlined,
            ),
          ),
        ),
        const SizedBox(height: 12),
      ],
    ),
  );
}

class MediaPage extends ConsumerWidget {
  const MediaPage({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final controller = ref.watch(mediaControllerProvider);
    return controller.when(
      data: (value) => LibraryScreen(controller: value),
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (error, stack) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Could not load Media Library: $error'),
            TextButton(
              onPressed: () => ref.invalidate(mediaControllerProvider),
              child: const Text('Retry'),
            ),
          ],
        ),
      ),
    );
  }
}

class PlaceholderPage extends StatelessWidget {
  const PlaceholderPage({
    super.key,
    required this.title,
    this.isMissing = false,
  });
  final String title;
  final bool isMissing;
  @override
  Widget build(BuildContext context) => Center(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 460),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isMissing ? Icons.search_off : Icons.construction_outlined,
              size: 42,
            ),
            const SizedBox(height: 16),
            Text(title, style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 8),
            Text(
              isMissing
                  ? 'This route does not exist.'
                  : 'This module is planned for a later task.',
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    ),
  );
}
