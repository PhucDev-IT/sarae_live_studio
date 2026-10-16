import 'dart:io';

import 'package:drift/drift.dart';
import 'package:drift/native.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

QueryExecutor openConnection() => LazyDatabase(() async {
  final dir = await getApplicationSupportDirectory();
  final folder = Directory(p.join(dir.path, 'SaraeLiveStudio'));
  await folder.create(recursive: true);
  return NativeDatabase(File(p.join(folder.path, 'media.sqlite')));
});
