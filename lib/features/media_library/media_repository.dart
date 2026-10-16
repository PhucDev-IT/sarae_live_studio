import 'dart:convert';
import 'package:shared_preferences/shared_preferences.dart';
import 'media_entry.dart';

/// Metadata-only persistence. Original video files remain in their chosen folder.
/// A subsequent phase will migrate this store to SQLite/Drift.
class MediaRepository {
  static const _key = 'sarae.media.v1';

  Future<List<MediaEntry>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);
    if (raw == null) return [];
    try {
      final list = jsonDecode(raw) as List<dynamic>;
      return list.map((e) => MediaEntry.fromJson(Map<String, dynamic>.from(e as Map))).toList();
    } catch (_) {
      // Preserve the original stored value if it cannot be decoded.
      rethrow;
    }
  }

  Future<void> save(List<MediaEntry> entries) async {
    final prefs = await SharedPreferences.getInstance();
    final result = await prefs.setString(_key, jsonEncode(entries.map((e) => e.toJson()).toList()));
    if (!result) throw StateError('Could not save media library');
  }
}
