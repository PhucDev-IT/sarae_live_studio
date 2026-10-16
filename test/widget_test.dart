import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sarae_live_studio/main.dart';
import 'package:sarae_live_studio/features/media_library/media_entry.dart';
import 'package:sarae_live_studio/features/media_library/media_repository.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('MediaEntry metadata serializes and restores', () {
    final item = MediaEntry(
      id: 'VID-1',
      name: 'review.mp4',
      path: 'C:/clips/review.mp4',
      sizeBytes: 12345,
      importedAt: DateTime.utc(2026, 10, 16),
      productCode: 'SP-1',
      presenter: 'Lan',
      tags: const ['review', 'blouse'],
    );
    final restored = MediaEntry.fromJson(item.toJson());
    expect(restored.id, 'VID-1');
    expect(restored.presenter, 'Lan');
    expect(restored.tags, ['review', 'blouse']);
  });

  test('MediaRepository persists metadata', () async {
    SharedPreferences.setMockInitialValues({});
    final repo = MediaRepository();
    await repo.save([
      MediaEntry(
        id: '1',
        name: 'clip.mp4',
        path: '/tmp/clip.mp4',
        sizeBytes: 100,
        importedAt: DateTime.utc(2026),
      ),
    ]);
    expect((await repo.load()).single.name, 'clip.mp4');
  });

  testWidgets('Shows media library shell', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.binding.setSurfaceSize(const Size(1280, 800));
    await tester.pumpWidget(const SaraeLiveStudio());
    await tester.pumpAndSettle();
    expect(find.text('Video Library'), findsWidgets);
    expect(find.text('Import video'), findsOneWidget);
    await tester.binding.setSurfaceSize(null);
  });
}
