part of 'media_screen.dart';

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.count, required this.icon});
  final String label;
  final int count;
  final IconData icon;
  @override
  Widget build(BuildContext context) => SizedBox(
    width: 205,
    child: Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(11),
              decoration: BoxDecoration(
                color: const Color(0xFFF0EEFF),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(icon, color: const Color(0xFF6757DA)),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '$count',
                    style: const TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  Text(
                    label,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 11,
                      color: Color(0xFF667085),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class _MediaCard extends StatelessWidget {
  const _MediaCard({
    required this.entry,
    required this.selected,
    required this.onTap,
    required this.onEdit,
    required this.onDelete,
  });
  final MediaEntry entry;
  final bool selected;
  final VoidCallback onTap, onEdit, onDelete;

  @override
  Widget build(BuildContext context) => Card(
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(14),
      side: BorderSide(
        color: selected ? const Color(0xFF6757DA) : const Color(0xFFE7E9F0),
        width: selected ? 1.7 : 1,
      ),
    ),
    clipBehavior: Clip.antiAlias,
    child: InkWell(
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 16 / 10,
            child: ColoredBox(
              color: const Color(0xFF252638),
              child: Stack(
                children: [
                  const Center(
                    child: Icon(
                      Icons.movie_creation_outlined,
                      color: Color(0xFF777994),
                      size: 45,
                    ),
                  ),
                  Positioned(
                    top: 10,
                    left: 10,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        color: entry.enabled
                            ? const Color(0xFFDCFCE7)
                            : const Color(0xFFFEE2E2),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        child: Text(
                          entry.enabled ? 'Đang bật' : 'Đang tắt',
                          style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.w700,
                            color: entry.enabled
                                ? const Color(0xFF166534)
                                : const Color(0xFF991B1B),
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 6, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  entry.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 13,
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  entry.productCode.isEmpty
                      ? 'Chưa gắn sản phẩm'
                      : entry.productCode,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11,
                    color: entry.productCode.isEmpty
                        ? const Color(0xFFC26A24)
                        : const Color(0xFF667085),
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  '${entry.presenter.isEmpty ? "Chưa có người giới thiệu" : entry.presenter} · ${(entry.sizeBytes / 1048576).toStringAsFixed(1)} MB',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 10,
                    color: Color(0xFF87909F),
                  ),
                ),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    IconButton(
                      tooltip: 'Sửa thông tin',
                      onPressed: onEdit,
                      icon: const Icon(Icons.edit_outlined, size: 17),
                    ),
                    IconButton(
                      tooltip: 'Xóa khỏi thư viện',
                      onPressed: onDelete,
                      icon: const Icon(Icons.delete_outline, size: 17),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({
    required this.icon,
    required this.title,
    required this.subtitle,
  });
  final IconData icon;
  final String title, subtitle;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 56, horizontal: 18),
    child: Column(
      children: [
        Icon(icon, size: 54, color: const Color(0xFFA8A3C2)),
        const SizedBox(height: 14),
        Text(
          title,
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
        ),
        const SizedBox(height: 6),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: const TextStyle(color: Color(0xFF667085)),
        ),
      ],
    ),
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
    content: SizedBox(
      width: 510,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _field('Tên video', name),
            _field('Mã sản phẩm', product, hint: 'VD: SARAE-001'),
            _field('Người giới thiệu', presenter, hint: 'VD: Lan'),
            _field('Mô tả nội dung', description, maxLines: 3),
            _field(
              'Tags (phân cách bằng dấu phẩy)',
              tags,
              hint: 'trễ vai, review, mùa thu',
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Cho phép sử dụng video trong playlist'),
              value: enabled,
              onChanged: (value) => setState(() => enabled = value),
            ),
            const SizedBox(height: 8),
            Text(
              'Đường dẫn gốc: ${widget.entry.path.isEmpty ? "(Trình duyệt không cung cấp đường dẫn)" : widget.entry.path}',
              style: const TextStyle(fontSize: 11, color: Color(0xFF667085)),
            ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: saving ? null : () => Navigator.pop(context),
        child: const Text('Hủy'),
      ),
      FilledButton(
        onPressed: saving
            ? null
            : () async {
                if (name.text.trim().isEmpty) return;
                setState(() => saving = true);
                await widget.onSave(
                  widget.entry.copyWith(
                    name: name.text.trim(),
                    productCode: product.text.trim(),
                    presenter: presenter.text.trim(),
                    description: description.text.trim(),
                    tags: tags.text
                        .split(',')
                        .map((t) => t.trim())
                        .where((t) => t.isNotEmpty)
                        .toList(),
                    enabled: enabled,
                  ),
                );
                if (mounted) setState(() => saving = false);
              },
        child: Text(saving ? 'Đang lưu...' : 'Lưu thay đổi'),
      ),
    ],
  );

  Widget _field(
    String label,
    TextEditingController c, {
    String? hint,
    int maxLines = 1,
  }) => Padding(
    padding: const EdgeInsets.only(bottom: 14),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 7),
        TextField(
          controller: c,
          maxLines: maxLines,
          decoration: InputDecoration(hintText: hint, isDense: true),
        ),
      ],
    ),
  );
}
