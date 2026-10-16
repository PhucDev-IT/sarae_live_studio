import 'package:drift/drift.dart';

import 'connection_stub.dart'
    if (dart.library.io) 'connection_native.dart'
    as platform;

QueryExecutor openConnection() => platform.openConnection();
