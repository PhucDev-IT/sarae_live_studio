class MediaEntry {
  const MediaEntry({
    required this.id,
    required this.name,
    required this.path,
    required this.sizeBytes,
    required this.importedAt,
    this.productCode = '',
    this.presenter = '',
    this.description = '',
    this.tags = const [],
    this.enabled = true,
  });

  final String id;
  final String name;
  final String path;
  final int sizeBytes;
  final DateTime importedAt;
  final String productCode;
  final String presenter;
  final String description;
  final List<String> tags;
  final bool enabled;

  MediaEntry copyWith({
    String? name, String? productCode, String? presenter,
    String? description, List<String>? tags, bool? enabled,
  }) => MediaEntry(
    id: id, name: name, path: path, sizeBytes: sizeBytes,
    importedAt: importedAt, productCode: productCode ?? this.productCode,
    presenter: presenter ?? this.presenter,
    description: description ?? this.description,
    tags: tags ?? this.tags, enabled: enabled ?? this.enabled,
  );

  Map<String, dynamic> toJson() => {
    'id': id, 'name': name, 'path': path, 'sizeBytes': sizeBytes,
    'importedAt': importedAt.toIso8601String(),
    'productCode': productCode, 'presenter': presenter,
    'description': description, 'tags': tags, 'enabled': enabled,
  };

  factory MediaEntry.fromJson(Map<String, dynamic> json) => MediaEntry(
    id: json['id'] as String,
    name: json['name'] as String,
    path: json['path'] as String? ?? '',
    sizeBytes: (json['sizeBytes'] as num?)?.toInt() ?? 0,
    importedAt: DateTime.tryParse(json['importedAt'] as String? ?? '') ?? DateTime.now(),
    productCode: json['productCode'] as String? ?? '',
    presenter: json['presenter'] as String? ?? '',
    description: json['description'] as String? ?? '',
    tags: (json['tags'] as List<dynamic>? ?? []).map((e) => e.toString()).toList(),
    enabled: json['enabled'] as bool? ?? true,
  );
}
