import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../../core/database/app_database.dart';
import 'media_entry.dart';

abstract class MediaRepository {
  Future<List<MediaEntry>> load();
  Future<void> save(List<MediaEntry> entries);
}

/// Preserves the source value after failure, so migration can be retried.
class LegacyMediaStore {
  static const key = 'sarae.media.v1';
  final SharedPreferences prefs;
  LegacyMediaStore(this.prefs);

  List<MediaEntry> read() {
    final raw = prefs.getString(key);
    if (raw == null) return [];
    final decoded = jsonDecode(raw);
    if (decoded is! List) {
      throw const FormatException('Legacy catalog must be a list');
    }
    final ids = <String>{};
    return decoded.map((value) {
      if (value is! Map) throw const FormatException('Invalid media record');
      final map = Map<String, dynamic>.from(value);
      if (map['id'] is! String ||
          (map['id'] as String).trim().isEmpty ||
          map['name'] is! String ||
          (map['name'] as String).trim().isEmpty ||
          map['importedAt'] is! String ||
          DateTime.tryParse(map['importedAt'] as String) == null ||
          map['tags'] != null &&
              (map['tags'] is! List ||
                  (map['tags'] as List).any((t) => t is! String)) ||
          map['sizeBytes'] != null &&
              (map['sizeBytes'] is! num || (map['sizeBytes'] as num) < 0)) {
        throw const FormatException('Invalid legacy media fields');
      }
      final entry = MediaEntry.fromJson(map);
      if (!ids.add(entry.id)) {
        throw const FormatException('Duplicate legacy media ID');
      }
      return entry;
    }).toList();
  }
}

class DesktopMediaRepository implements MediaRepository {
  final AppDatabase database;
  final LegacyMediaStore legacy;
  DesktopMediaRepository(this.database, this.legacy);

  @override
  Future<List<MediaEntry>> load() async {
    if (!await database.legacyMigrated()) {
      final entries = legacy.read();
      await database.importLegacy(entries);
    }
    return database.readMedia();
  }

  @override
  Future<void> save(List<MediaEntry> entries) => database.replaceMedia(entries);
}

/// Browser-only preview catalog. No desktop synchronization is implied.
class WebMediaRepository implements MediaRepository {
  final LegacyMediaStore store;
  WebMediaRepository(this.store);

  @override
  Future<List<MediaEntry>> load() async => store.read();

  @override
  Future<void> save(List<MediaEntry> entries) async {
    final result = await store.prefs.setString(
      LegacyMediaStore.key,
      jsonEncode(entries.map((e) => e.toJson()).toList()),
    );
    if (!result) throw StateError('Could not save browser preview catalog');
  }
}
