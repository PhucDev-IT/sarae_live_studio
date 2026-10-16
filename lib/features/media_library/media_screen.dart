import 'package:flutter/material.dart';

import 'media_controller.dart';
import 'media_entry.dart';

part 'media_components.dart';

class LibraryScreen extends StatefulWidget {
  const LibraryScreen({super.key, required this.controller});
  final MediaController controller;

  @override
  State<LibraryScreen> createState() => _LibraryScreenState();
}

class _LibraryScreenState extends State<LibraryScreen> {
  final search = TextEditingController();
  String filter = 'all';
  String? selectedId;
  int page = 1;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_refresh);
  }

  void _refresh() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.controller.removeListener(_refresh);
    search.dispose();
    super.dispose();
  }

  List<MediaEntry> get visibleItems {
    final query = search.text.trim().toLowerCase();
    final result = widget.controller.items.where((e) {
      if (filter == 'enabled' && !e.enabled) return false;
      if (filter == 'unmapped' && e.productCode.trim().isNotEmpty) return false;
      return query.isEmpty ||
          [
            e.name,
            e.productCode,
            e.presenter,
            e.description,
            ...e.tags,
          ].any((value) => value.toLowerCase().contains(query));
    }).toList();
    result.sort((a, b) => b.importedAt.compareTo(a.importedAt));
    return result;
  }

  Future<void> _import() async {
    final count = await widget.controller.importVideos();
    if (!mounted) return;
    final error = widget.controller.error;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          error ??
              (count > 0
                  ? 'Đã nhập $count video vào thư viện.'
                  : 'Không có video mới được nhập.'),
        ),
      ),
    );
  }

  void _edit(MediaEntry entry) {
    showDialog<void>(
      context: context,
      builder: (context) => EditVideoDialog(
        entry: entry,
        onSave: (updated) async {
          final ok = await widget.controller.updateEntry(updated);
          if (!context.mounted) return;
          if (ok) {
            Navigator.pop(context);
          } else {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text(
                  widget.controller.error ?? 'Không lưu được video',
                ),
              ),
            );
          }
        },
      ),
    );
  }

  Future<void> _delete(MediaEntry entry) async {
    final approved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Xóa video khỏi thư viện?'),
        content: Text(
          'Chỉ xóa metadata của "${entry.name}". File gốc trên ổ đĩa không bị xóa.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Hủy'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Xóa'),
          ),
        ],
      ),
    );
    if (approved != true) return;
    final ok = await widget.controller.removeEntry(entry.id);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          ok
              ? 'Đã xóa khỏi thư viện.'
              : widget.controller.error ?? 'Có lỗi khi xóa',
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final items = visibleItems;
    final all = widget.controller.items;
    final selected = all.where((e) => e.id == selectedId).firstOrNull;

    return Scaffold(
      body: SafeArea(
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.all(24),
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 1500),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Wrap(
                              spacing: 12,
                              runSpacing: 12,
                              children: [
                                const SizedBox(
                                  width: 280,
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        'Video Library',
                                        style: TextStyle(
                                          fontSize: 27,
                                          fontWeight: FontWeight.w800,
                                          letterSpacing: -.7,
                                        ),
                                      ),
                                      SizedBox(height: 5),
                                      Text(
                                        'Tổ chức video theo sản phẩm và người giới thiệu.',
                                        style: TextStyle(
                                          color: Color(0xFF667085),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                FilledButton.icon(
                                  onPressed: widget.controller.isImporting
                                      ? null
                                      : _import,
                                  icon: const Icon(Icons.add),
                                  label: Text(
                                    widget.controller.isImporting
                                        ? 'Đang nhập...'
                                        : 'Import video',
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 24),
                            Wrap(
                              spacing: 12,
                              runSpacing: 12,
                              children: [
                                _Stat(
                                  label: 'Tổng video',
                                  count: all.length,
                                  icon: Icons.video_library_outlined,
                                ),
                                _Stat(
                                  label: 'Đang kích hoạt',
                                  count: all.where((e) => e.enabled).length,
                                  icon: Icons.check_circle_outline,
                                ),
                                _Stat(
                                  label: 'Chưa gắn sản phẩm',
                                  count: all
                                      .where((e) => e.productCode.isEmpty)
                                      .length,
                                  icon: Icons.sell_outlined,
                                ),
                              ],
                            ),
                            const SizedBox(height: 24),
                            Card(
                              child: Padding(
                                padding: const EdgeInsets.all(16),
                                child: Column(
                                  children: [
                                    Wrap(
                                      spacing: 12,
                                      runSpacing: 12,
                                      children: [
                                        SizedBox(
                                          width: 280,
                                          child: TextField(
                                            controller: search,
                                            onChanged: (_) => setState(() {}),
                                            decoration: const InputDecoration(
                                              hintText: 'Tìm tên video, mã sản phẩm, người mẫu, tag...',
                                              prefixIcon: Icon(Icons.search),
                                              isDense: true,
                                            ),
                                          ),
                                        ),
                                        DropdownButton<String>(
                                          value: filter,
                                          items: const [
                                            DropdownMenuItem(
                                              value: 'all',
                                              child: Text('Tất cả'),
                                            ),
                                            DropdownMenuItem(
                                              value: 'enabled',
                                              child: Text('Đang kích hoạt'),
                                            ),
                                            DropdownMenuItem(
                                              value: 'unmapped',
                                              child: Text('Chưa gắn sản phẩm'),
                                            ),
                                          ],
                                          onChanged: (value) => setState(
                                            () => filter = value ?? 'all',
                                          ),
                                        ),
                                      ],
                                    ),
                                    const SizedBox(height: 16),
                                    if (widget.controller.isLoading)
                                      const Padding(
                                        padding: EdgeInsets.all(40),
                                        child: CircularProgressIndicator(),
                                      )
                                    else if (widget.controller.error != null &&
                                        all.isEmpty)
                                      _EmptyState(
                                        icon: Icons.error_outline,
                                        title: 'Không thể tải thư viện',
                                        subtitle: widget.controller.error!,
                                      )
                                    else if (items.isEmpty)
                                      _EmptyState(
                                        icon: Icons.video_library_outlined,
                                        title: all.isEmpty
                                            ? 'Chưa có video nào'
                                            : 'Không tìm thấy video',
                                        subtitle: all.isEmpty
                                            ? 'Import các file MP4 hoặc MOV để bắt đầu tạo thư viện.'
                                            : 'Thử thay đổi từ khóa hoặc bộ lọc.',
                                      )
                                    else ...[
                                      LayoutBuilder(
                                        builder: (context, constraints) {
                                          final columns =
                                              constraints.maxWidth > 1050
                                              ? 4
                                              : constraints.maxWidth > 720
                                              ? 3
                                              : constraints.maxWidth > 400
                                              ? 2
                                              : 1;
                                          final width =
                                              (constraints.maxWidth -
                                                  (columns - 1) * 12) /
                                              columns;
                                          return Wrap(
                                            spacing: 12,
                                            runSpacing: 12,
                                            children: items
                                                .map(
                                                  (e) => SizedBox(
                                                    width: width,
                                                    child: _MediaCard(
                                                      entry: e,
                                                      selected:
                                                          e.id == selectedId,
                                                      onTap: () => setState(
                                                        () => selectedId = e.id,
                                                      ),
                                                      onEdit: () => _edit(e),
                                                      onDelete: () =>
                                                          _delete(e),
                                                    ),
                                                  ),
                                                )
                                                .toList(),
                                          );
                                        },
                                      ),
                                    ],
                                  ],
                                ),
                              ),
                            ),
                            if (selected != null) ...[
                              const SizedBox(height: 18),
                              Card(
                                child: Padding(
                                  padding: const EdgeInsets.all(18),
                                  child: Wrap(
                                    spacing: 20,
                                    runSpacing: 10,
                                    crossAxisAlignment:
                                        WrapCrossAlignment.center,
                                    children: [
                                      const Icon(Icons.info_outline),
                                      Text(
                                        'Đã chọn: ${selected.name}',
                                        style: const TextStyle(
                                          fontWeight: FontWeight.w600,
                                        ),
                                      ),
                                      Text(
                                        'ID: ${selected.id}',
                                        style: const TextStyle(fontSize: 12),
                                      ),
                                      OutlinedButton.icon(
                                        onPressed: () => _edit(selected),
                                        icon: const Icon(Icons.edit_outlined),
                                        label: const Text('Sửa metadata'),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ],
                            const SizedBox(height: 16),
                            const Text(
                              'Lưu ý: bản đầu chỉ lưu thông tin video. File gốc không được sao chép, và preview/thumbnail sẽ tích hợp cùng FFmpeg ở bước tiếp theo.',
                              style: TextStyle(
                                color: Color(0xFF7C8494),
                                fontSize: 12,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
