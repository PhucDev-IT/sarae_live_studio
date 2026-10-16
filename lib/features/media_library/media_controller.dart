import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'media_entry.dart';
import 'media_repository.dart';

class MediaController extends ChangeNotifier {
  MediaController(this._repository);
  final MediaRepository _repository;

  List<MediaEntry> _items = [];
  List<MediaEntry> get items => List.unmodifiable(_items);
  bool isLoading = true;
  bool isImporting = false;
  String? error;

  Future<void> initialize() async {
    try {
      _items = await _repository.load();
      error = null;
    } catch (e) {
      error = 'Không thể đọc dữ liệu: $e';
    } finally {
      isLoading = false;
      notifyListeners();
    }
  }

  Future<int> importVideos() async {
    if (isImporting) return 0;
    isImporting = true;
    error = null;
    notifyListeners();
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['mp4', 'mov', 'm4v', 'webm'],
        allowMultiple: true,
        withData: false,
      );
      if (result == null) return 0;
      var added = 0;
      final updated = [..._items];
      final existing = _items.map((e) => '${e.path}|${e.name}|${e.sizeBytes}').toSet();
      for (final file in result.files) {
        // On web, the browser does not expose the real local filesystem path.
        final path = file.path ?? '';
        final key = '$path|${file.name}|${file.size}';
        if (!existing.add(key)) continue;
        updated.add(MediaEntry(
          id: 'VID-${DateTime.now().microsecondsSinceEpoch}-${added + 1}',
          name: file.name, path: path, sizeBytes: file.size,
          importedAt: DateTime.now(),
        ));
        added++;
      }
      if (added > 0) {
        await _repository.save(updated);
        _items = updated;
      }
      return added;
    } catch (e) {
      error = 'Không thể nhập video: $e';
      return 0;
    } finally {
      isImporting = false;
      notifyListeners();
    }
  }

  Future<bool> updateEntry(MediaEntry value) async {
    final index = _items.indexWhere((e) => e.id == value.id);
    if (index < 0) return false;
    final updated = [..._items]..[index] = value;
    return _commit(updated);
  }

  Future<bool> removeEntry(String id) async =>
      _commit(_items.where((e) => e.id != id).toList());

  Future<bool> _commit(List<MediaEntry> updated) async {
    try {
      await _repository.save(updated);
      _items = updated;
      error = null;
      notifyListeners();
      return true;
    } catch (e) {
      error = 'Không thể lưu thay đổi: $e';
      notifyListeners();
      return false;
    }
  }
}
