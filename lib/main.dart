import 'package:flutter/material.dart';
import 'features/media_library/media_controller.dart';
import 'features/media_library/media_entry.dart';
import 'features/media_library/media_repository.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const SaraeLiveStudio());
}

class SaraeLiveStudio extends StatefulWidget {
  const SaraeLiveStudio({super.key});

  @override
  State<SaraeLiveStudio> createState() => _SaraeLiveStudioState();
}

class _SaraeLiveStudioState extends State<SaraeLiveStudio> {
  late final MediaController controller;

  @override
  void initState() {
    super.initState();
    controller = MediaController(MediaRepository());
    controller.initialize();
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Sarae Live Studio',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF6757DA),
            brightness: Brightness.light,
            surface: const Color(0xFFF9FAFD),
          ),
          scaffoldBackgroundColor: const Color(0xFFF6F7FB),
          cardTheme: CardThemeData(
            color: Colors.white,
            surfaceTintColor: Colors.transparent,
            elevation: 0,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: Color(0xFFE7E9F0)),
            ),
          ),
          inputDecorationTheme: InputDecorationTheme(
            filled: true,
            fillColor: Colors.white,
            contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
          ),
        ),
        home: LibraryScreen(controller: controller),
      );
}

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
          [e.name, e.productCode, e.presenter, e.description, ...e.tags]
              .any((value) => value.toLowerCase().contains(query));
    }).toList();
    result.sort((a, b) => b.importedAt.compareTo(a.importedAt));
    return result;
  }

  Future<void> _import() async {
    final count = await widget.controller.importVideos();
    if (!mounted) return;
    final error = widget.controller.error;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(error ?? (count > 0
          ? 'Đã nhập $count video vào thư viện.'
          : 'Không có video mới được nhập.')),
    ));
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
              SnackBar(content: Text(widget.controller.error ?? 'Không lưu được video')),
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
        content: Text('Chỉ xóa metadata của "${entry.name}". File gốc trên ổ đĩa không bị xóa.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Xóa')),
        ],
      ),
    );
    if (approved != true) return;
    final ok = await widget.controller.removeEntry(entry.id);
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(ok ? 'Đã xóa khỏi thư viện.' : widget.controller.error ?? 'Có lỗi khi xóa'),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final items = visibleItems;
    final all = widget.controller.items;
    final selected = all.where((e) => e.id == selectedId).firstOrNull;
    final wide = MediaQuery.sizeOf(context).width >= 920;
    return Scaffold(
      body: SafeArea(
        child: Row(
          children: [
            if (wide) const _Sidebar(),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _TopBar(wide: wide),
                  Expanded(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.all(24),
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 1500),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(children: [
                              const Expanded(child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text('Video Library', style: TextStyle(fontSize: 27, fontWeight: FontWeight.w800, letterSpacing: -.7)),
                                  SizedBox(height: 5),
                                  Text('Tổ chức video theo sản phẩm và người giới thiệu.', style: TextStyle(color: Color(0xFF667085))),
                                ],
                              )),
                              FilledButton.icon(
                                onPressed: widget.controller.isImporting ? null : _import,
                                icon: const Icon(Icons.add),
                                label: Text(widget.controller.isImporting ? 'Đang nhập...' : 'Import video'),
                              ),
                            ]),
                            const SizedBox(height: 24),
                            Wrap(spacing: 12, runSpacing: 12, children: [
                              _Stat(label: 'Tổng video', count: all.length, icon: Icons.video_library_outlined),
                              _Stat(label: 'Đang kích hoạt', count: all.where((e) => e.enabled).length, icon: Icons.check_circle_outline),
                              _Stat(label: 'Chưa gắn sản phẩm', count: all.where((e) => e.productCode.isEmpty).length, icon: Icons.sell_outlined),
                            ]),
                            const SizedBox(height: 24),
                            Card(child: Padding(
                              padding: const EdgeInsets.all(16),
                              child: Column(children: [
                                Row(children: [
                                  Expanded(child: TextField(
                                    controller: search,
                                    onChanged: (_) => setState(() {}),
                                    decoration: const InputDecoration(
                                      hintText: 'Tìm tên video, mã sản phẩm, người mẫu, tag...',
                                      prefixIcon: Icon(Icons.search),
                                      isDense: true,
                                    ),
                                  )),
                                  const SizedBox(width: 12),
                                  DropdownButton<String>(
                                    value: filter,
                                    items: const [
                                      DropdownMenuItem(value: 'all', child: Text('Tất cả')),
                                      DropdownMenuItem(value: 'enabled', child: Text('Đang kích hoạt')),
                                      DropdownMenuItem(value: 'unmapped', child: Text('Chưa gắn sản phẩm')),
                                    ],
                                    onChanged: (value) => setState(() => filter = value ?? 'all'),
                                  ),
                                ]),
                                const SizedBox(height: 16),
                                if (widget.controller.isLoading)
                                  const Padding(padding: EdgeInsets.all(40), child: CircularProgressIndicator())
                                else if (widget.controller.error != null && all.isEmpty)
                                  _EmptyState(
                                    icon: Icons.error_outline,
                                    title: 'Không thể tải thư viện',
                                    subtitle: widget.controller.error!,
                                  )
                                else if (items.isEmpty)
                                  _EmptyState(
                                    icon: Icons.video_library_outlined,
                                    title: all.isEmpty ? 'Chưa có video nào' : 'Không tìm thấy video',
                                    subtitle: all.isEmpty
                                      ? 'Import các file MP4 hoặc MOV để bắt đầu tạo thư viện.'
                                      : 'Thử thay đổi từ khóa hoặc bộ lọc.',
                                  )
                                else ...[
                                  LayoutBuilder(builder: (context, constraints) {
                                    final columns = constraints.maxWidth > 1050 ? 4
                                        : constraints.maxWidth > 720 ? 3
                                        : constraints.maxWidth > 400 ? 2 : 1;
                                    final width = (constraints.maxWidth - (columns - 1) * 12) / columns;
                                    return Wrap(spacing: 12, runSpacing: 12,
                                      children: items.map((e) => SizedBox(
                                        width: width,
                                        child: _MediaCard(
                                          entry: e,
                                          selected: e.id == selectedId,
                                          onTap: () => setState(() => selectedId = e.id),
                                          onEdit: () => _edit(e),
                                          onDelete: () => _delete(e),
                                        ),
                                      )).toList(),
                                    );
                                  }),
                                ],
                              ]),
                            )),
                            if (selected != null) ...[
                              const SizedBox(height: 18),
                              Card(child: Padding(
                                padding: const EdgeInsets.all(18),
                                child: Wrap(
                                  spacing: 20, runSpacing: 10,
                                  crossAxisAlignment: WrapCrossAlignment.center,
                                  children: [
                                    const Icon(Icons.info_outline),
                                    Text('Đã chọn: ${selected.name}', style: const TextStyle(fontWeight: FontWeight.w600)),
                                    Text('ID: ${selected.id}', style: const TextStyle(fontSize: 12)),
                                    OutlinedButton.icon(onPressed: () => _edit(selected), icon: const Icon(Icons.edit_outlined), label: const Text('Sửa metadata')),
                                  ],
                                ),
                              )),
                            ],
                            const SizedBox(height: 16),
                            const Text('Lưu ý: bản đầu chỉ lưu thông tin video. File gốc không được sao chép, và preview/thumbnail sẽ tích hợp cùng FFmpeg ở bước tiếp theo.',
                              style: TextStyle(color: Color(0xFF7C8494), fontSize: 12)),
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

class _Sidebar extends StatelessWidget {
  const _Sidebar();
  @override
  Widget build(BuildContext context) => Container(
    width: 224,
    color: const Color(0xFF181A2A),
    padding: const EdgeInsets.fromLTRB(14, 24, 14, 16),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Padding(
        padding: EdgeInsets.only(left: 12, bottom: 32),
        child: Row(children: [
          Icon(Icons.live_tv_rounded, color: Color(0xFFA99DFF), size: 28),
          SizedBox(width: 10),
          Expanded(child: Text('SARAE STUDIO', style: TextStyle(color: Colors.white, fontWeight: FontWeight.w800, letterSpacing: .6))),
        ]),
      ),
      const Padding(padding: EdgeInsets.only(left: 12, bottom: 12),
        child: Text('WORKSPACE', style: TextStyle(color: Color(0xFF9094B3), fontSize: 10, letterSpacing: 1.2))),
      const _NavRow(Icons.video_library_outlined, 'Video Library', selected: true),
      const SizedBox(height: 10),
      const _NavRow(Icons.inventory_2_outlined, 'Sản phẩm'),
      const _NavRow(Icons.people_alt_outlined, 'Người giới thiệu'),
      const _NavRow(Icons.playlist_play_outlined, 'Playlist'),
      const _NavRow(Icons.live_tv_outlined, 'Live Control'),
      const _NavRow(Icons.smart_toy_outlined, 'AI Settings'),
      const Spacer(),
      const Divider(color: Color(0xFF383B52)),
      const _NavRow(Icons.settings_outlined, 'Cấu hình'),
      const Padding(
        padding: EdgeInsets.fromLTRB(12, 12, 0, 0),
        child: Text('Phase 1 · Media Library', style: TextStyle(color: Color(0xFF9094B3), fontSize: 11)),
      ),
    ]),
  );
}

class _NavRow extends StatelessWidget {
  const _NavRow(this.icon, this.name, {this.selected = false});
  final IconData icon;
  final String name;
  final bool selected;
  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
    decoration: BoxDecoration(
      color: selected ? const Color(0xFF393456) : Colors.transparent,
      borderRadius: BorderRadius.circular(10),
    ),
    child: Row(children: [
      Icon(icon, size: 19, color: selected ? const Color(0xFFB6A9FF) : const Color(0xFF9398B4)),
      const SizedBox(width: 12),
      Expanded(child: Text(name, style: TextStyle(fontSize: 13,
        fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
        color: selected ? Colors.white : const Color(0xFFB5B8CC)))),
      if (!selected) const Text('Soon', style: TextStyle(fontSize: 9, color: Color(0xFF70748E))),
    ]),
  );
}

class _TopBar extends StatelessWidget {
  const _TopBar({required this.wide});
  final bool wide;
  @override
  Widget build(BuildContext context) => Container(
    height: 62,
    padding: const EdgeInsets.symmetric(horizontal: 24),
    decoration: const BoxDecoration(color: Colors.white, border: Border(bottom: BorderSide(color: Color(0xFFE9EBF1)))),
    child: Row(children: [
      if (!wide) const Padding(padding: EdgeInsets.only(right: 10), child: Icon(Icons.live_tv, color: Color(0xFF6757DA))),
      Text(wide ? 'Workspace  /  Media Library' : 'Sarae Live Studio',
          style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
      const Spacer(),
      const Icon(Icons.desktop_windows_outlined, size: 19, color: Color(0xFF667085)),
      const SizedBox(width: 8),
      const Text('Local workspace', style: TextStyle(fontSize: 12, color: Color(0xFF667085))),
    ]),
  );
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.count, required this.icon});
  final String label;
  final int count;
  final IconData icon;
  @override
  Widget build(BuildContext context) => SizedBox(width: 205, child: Card(
    child: Padding(padding: const EdgeInsets.all(16),
      child: Row(children: [
        Container(
          padding: const EdgeInsets.all(11),
          decoration: BoxDecoration(color: const Color(0xFFF0EEFF), borderRadius: BorderRadius.circular(12)),
          child: Icon(icon, color: const Color(0xFF6757DA))),
        const SizedBox(width: 12),
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('$count', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
          Text(label, style: const TextStyle(fontSize: 11, color: Color(0xFF667085))),
        ]),
      ]),
    ),
  ));
}

class _MediaCard extends StatelessWidget {
  const _MediaCard({required this.entry, required this.selected, required this.onTap, required this.onEdit, required this.onDelete});
  final MediaEntry entry;
  final bool selected;
  final VoidCallback onTap, onEdit, onDelete;

  @override
  Widget build(BuildContext context) => Card(
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(14),
      side: BorderSide(color: selected ? const Color(0xFF6757DA) : const Color(0xFFE7E9F0), width: selected ? 1.7 : 1),
    ),
    clipBehavior: Clip.antiAlias,
    child: InkWell(onTap: onTap, child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      AspectRatio(aspectRatio: 16 / 10, child: ColoredBox(
        color: const Color(0xFF252638),
        child: Stack(children: [
          const Center(child: Icon(Icons.movie_creation_outlined, color: Color(0xFF777994), size: 45)),
          Positioned(top: 10, left: 10, child: DecoratedBox(
            decoration: BoxDecoration(
              color: entry.enabled ? const Color(0xFFDCFCE7) : const Color(0xFFFEE2E2),
              borderRadius: BorderRadius.circular(6)),
            child: Padding(padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              child: Text(entry.enabled ? 'Đang bật' : 'Đang tắt', style: TextStyle(
                fontSize: 10, fontWeight: FontWeight.w700,
                color: entry.enabled ? const Color(0xFF166534) : const Color(0xFF991B1B))))),
          ),
        ]),
      )),
      Padding(padding: const EdgeInsets.fromLTRB(12, 12, 6, 8), child: Column(
        crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(entry.name, maxLines: 1, overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
          const SizedBox(height: 6),
          Text(entry.productCode.isEmpty ? 'Chưa gắn sản phẩm' : entry.productCode,
            maxLines: 1, overflow: TextOverflow.ellipsis,
            style: TextStyle(fontSize: 11, color: entry.productCode.isEmpty ? const Color(0xFFC26A24) : const Color(0xFF667085))),
          const SizedBox(height: 6),
          Text('${entry.presenter.isEmpty ? "Chưa có người giới thiệu" : entry.presenter} · ${(entry.sizeBytes / 1048576).toStringAsFixed(1)} MB',
            maxLines: 1, overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 10, color: Color(0xFF87909F))),
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            IconButton(tooltip: 'Sửa thông tin', onPressed: onEdit, icon: const Icon(Icons.edit_outlined, size: 17)),
            IconButton(tooltip: 'Xóa khỏi thư viện', onPressed: onDelete, icon: const Icon(Icons.delete_outline, size: 17)),
          ]),
        ],
      )),
    ])),
  );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.icon, required this.title, required this.subtitle});
  final IconData icon;
  final String title, subtitle;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 56, horizontal: 18),
    child: Column(children: [
      Icon(icon, size: 54, color: const Color(0xFFA8A3C2)),
      const SizedBox(height: 14),
      Text(title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
      const SizedBox(height: 6),
      Text(subtitle, textAlign: TextAlign.center, style: const TextStyle(color: Color(0xFF667085))),
    ]),
  );
}

class EditVideoDialog extends StatefulWidget {
  const EditVideoDialog({super.key, required this.entry, required this.onSave});
  final MediaEntry entry;
  final Future<void> Function(MediaEntry) onSave;
  @override
  State<EditVideoDialog> createState() => _EditVideoDialogState();
}

class _EditVideoDialogState extends State<EditVideoDialog> {
  late final TextEditingController name, product, presenter, description, tags;
  late bool enabled;
  bool saving = false;

  @override
  void initState() {
    super.initState();
    final e = widget.entry;
    name = TextEditingController(text: e.name);
    product = TextEditingController(text: e.productCode);
    presenter = TextEditingController(text: e.presenter);
    description = TextEditingController(text: e.description);
    tags = TextEditingController(text: e.tags.join(', '));
    enabled = e.enabled;
  }

  @override
  void dispose() {
    for (final c in [name, product, presenter, description, tags]) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Chỉnh sửa thông tin video'),
    content: SizedBox(width: 510, child: SingleChildScrollView(child: Column(
      mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _field('Tên video', name),
        _field('Mã sản phẩm', product, hint: 'VD: SARAE-001'),
        _field('Người giới thiệu', presenter, hint: 'VD: Lan'),
        _field('Mô tả nội dung', description, maxLines: 3),
        _field('Tags (phân cách bằng dấu phẩy)', tags, hint: 'trễ vai, review, mùa thu'),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Cho phép sử dụng video trong playlist'),
          value: enabled, onChanged: (value) => setState(() => enabled = value),
        ),
        const SizedBox(height: 8),
        Text('Đường dẫn gốc: ${widget.entry.path.isEmpty ? "(Trình duyệt không cung cấp đường dẫn)" : widget.entry.path}',
          style: const TextStyle(fontSize: 11, color: Color(0xFF667085))),
      ],
    ))),
    actions: [
      TextButton(onPressed: saving ? null : () => Navigator.pop(context), child: const Text('Hủy')),
      FilledButton(
        onPressed: saving ? null : () async {
          if (name.text.trim().isEmpty) return;
          setState(() => saving = true);
          await widget.onSave(widget.entry.copyWith(
            name: name.text.trim(), productCode: product.text.trim(),
            presenter: presenter.text.trim(), description: description.text.trim(),
            tags: tags.text.split(',').map((t) => t.trim()).where((t) => t.isNotEmpty).toList(),
            enabled: enabled,
          ));
          if (mounted) setState(() => saving = false);
        },
        child: Text(saving ? 'Đang lưu...' : 'Lưu thay đổi'),
      ),
    ],
  );

  Widget _field(String label, TextEditingController c, {String? hint, int maxLines = 1}) =>
    Padding(padding: const EdgeInsets.only(bottom: 14), child: Column(
      crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
        const SizedBox(height: 7),
        TextField(controller: c, maxLines: maxLines,
          decoration: InputDecoration(hintText: hint, isDense: true)),
      ],
    ));
}
