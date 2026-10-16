import 'dart:convert';
import 'dart:io';

import 'package:drift/native.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sarae_live_studio/app/app.dart';
import 'package:sarae_live_studio/core/database/app_database.dart';
import 'package:sarae_live_studio/features/media_library/media_entry.dart';
import 'package:sarae_live_studio/features/media_library/media_repository.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  MediaEntry sample() => MediaEntry(
    id: 'VID-1',
    name: 'review.mp4',
    path: 'C:/clips/review.mp4',
    sizeBytes: 12345,
    importedAt: DateTime.utc(2026, 10, 16),
    productCode: 'SP-1',
    presenter: 'Lan',
    tags: const ['review', 'blouse'],
  );

  test('metadata round trip', () {
    final restored = MediaEntry.fromJson(sample().toJson());
    expect(restored.presenter, 'Lan');
    expect(restored.tags, ['review', 'blouse']);
  });

  test('legacy migration imports once and preserves metadata', () async {
    SharedPreferences.setMockInitialValues({
      LegacyMediaStore.key: jsonEncode([sample().toJson()]),
    });
    final prefs = await SharedPreferences.getInstance();
    final db = AppDatabase.forTesting(NativeDatabase.memory());
    final repo = DesktopMediaRepository(db, LegacyMediaStore(prefs));
    expect((await repo.load()).single.productCode, 'SP-1');
    expect(await db.legacyMigrated(), isTrue);
    expect((await repo.load()).length, 1);
    await repo.save([sample().copyWith(presenter: 'Mai')]);
    expect((await repo.load()).single.presenter, 'Mai');
    expect(prefs.getString(LegacyMediaStore.key), isNotNull);
    await db.close();
  });

  test('invalid legacy value remains available for retry', () async {
    const invalid = '[{"id":"","name":"bad"}]';
    SharedPreferences.setMockInitialValues({LegacyMediaStore.key: invalid});
    final prefs = await SharedPreferences.getInstance();
    final db = AppDatabase.forTesting(NativeDatabase.memory());
    final repo = DesktopMediaRepository(db, LegacyMediaStore(prefs));
    await expectLater(repo.load(), throwsFormatException);
    expect(await db.legacyMigrated(), isFalse);
    expect(await db.readMedia(), isEmpty);
    expect(prefs.getString(LegacyMediaStore.key), invalid);
    await db.close();
  });

  test('SQLite metadata survives closing and reopening its file', () async {
    final directory = await Directory.systemTemp.createTemp('sarae-db-test-');
    try {
      final file = File('${directory.path}/media.sqlite');
      final first = AppDatabase.forTesting(NativeDatabase(file));
      await first.replaceMedia([sample()]);
      await first.close();
      final reopened = AppDatabase.forTesting(NativeDatabase(file));
      expect((await reopened.readMedia()).single.tags, ['review', 'blouse']);
      await reopened.close();
    } finally {
      await directory.delete(recursive: true);
    }
  });

  testWidgets(
    'sidebar navigates to Media Library and unknown route falls back',
    (tester) async {
      SharedPreferences.setMockInitialValues({});
      await tester.binding.setSurfaceSize(const Size(1366, 768));
      final db = AppDatabase.forTesting(NativeDatabase.memory());
      await tester.pumpWidget(
        ProviderScope(
          overrides: [databaseProvider.overrideWithValue(db)],
          child: const SaraeLiveStudio(),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Media Library').first);
      await tester.pumpAndSettle();
      expect(find.text('Import video'), findsOneWidget);
      for (final width in [700.0, 400.0, 1920.0, 2560.0]) {
        await tester.binding.setSurfaceSize(Size(width, 768));
        await tester.pumpAndSettle();
      }
      GoRouter.of(tester.element(find.text('Import video'))).go('/missing');
      await tester.pumpAndSettle();
      expect(find.text('Page not found'), findsWidgets);
      await tester.binding.setSurfaceSize(null);
      await db.close();
    },
  );
}
