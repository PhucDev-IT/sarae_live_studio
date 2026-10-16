import 'dart:convert';

import 'package:drift/drift.dart';

import '../../features/media_library/media_entry.dart';
import 'connection.dart';

part 'app_database.g.dart';

class MediaRecords extends Table {
  TextColumn get id => text()();
  TextColumn get name => text()();
  TextColumn get path => text()();
  IntColumn get sizeBytes => integer()();
  DateTimeColumn get importedAt => dateTime()();
  TextColumn get productCode => text().withDefault(const Constant(''))();
  TextColumn get presenter => text().withDefault(const Constant(''))();
  TextColumn get description => text().withDefault(const Constant(''))();
  TextColumn get tagsJson => text().withDefault(const Constant('[]'))();
  BoolColumn get enabled => boolean().withDefault(const Constant(true))();

  @override
  Set<Column> get primaryKey => {id};
}

class AppMetadata extends Table {
  TextColumn get key => text()();
  TextColumn get value => text()();

  @override
  Set<Column> get primaryKey => {key};
}

@DriftDatabase(tables: [MediaRecords, AppMetadata])
class AppDatabase extends _$AppDatabase {
  AppDatabase() : super(openConnection());
  AppDatabase.forTesting(super.executor);

  @override
  int get schemaVersion => 1;

  @override
  MigrationStrategy get migration => MigrationStrategy(
    onCreate: (m) => m.createAll(),
    onUpgrade: (m, from, to) async {
      // Add explicit stepwise migrations when the schema version increases.
      throw UnsupportedError('Missing migration from schema $from to $to');
    },
  );

  Future<List<MediaEntry>> readMedia() async {
    final rows = await select(mediaRecords).get();
    return rows
        .map(
          (row) => MediaEntry(
            id: row.id,
            name: row.name,
            path: row.path,
            sizeBytes: row.sizeBytes,
            importedAt: row.importedAt,
            productCode: row.productCode,
            presenter: row.presenter,
            description: row.description,
            tags: (jsonDecode(row.tagsJson) as List).cast<String>(),
            enabled: row.enabled,
          ),
        )
        .toList();
  }

  Future<void> replaceMedia(List<MediaEntry> entries) => transaction(() async {
    await delete(mediaRecords).go();
    for (final entry in entries) {
      await into(mediaRecords).insert(_companion(entry));
    }
  });

  Future<bool> legacyMigrated() async =>
      (await (select(
            appMetadata,
          )..where((t) => t.key.equals('legacy_media_v1'))).getSingleOrNull())
          ?.value ==
      'done';

  Future<void> importLegacy(List<MediaEntry> entries) => transaction(() async {
    if (await legacyMigrated()) return;
    for (final entry in entries) {
      await into(mediaRecords).insertOnConflictUpdate(_companion(entry));
    }
    await into(appMetadata).insert(
      const AppMetadataCompanion(
        key: Value('legacy_media_v1'),
        value: Value('done'),
      ),
    );
  });

  MediaRecordsCompanion _companion(MediaEntry e) =>
      MediaRecordsCompanion.insert(
        id: e.id,
        name: e.name,
        path: e.path,
        sizeBytes: e.sizeBytes,
        importedAt: e.importedAt,
        productCode: Value(e.productCode),
        presenter: Value(e.presenter),
        description: Value(e.description),
        tagsJson: Value(jsonEncode(e.tags)),
        enabled: Value(e.enabled),
      );
}
