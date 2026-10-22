# Hướng dẫn "dạy" AI trả lời LIVE (Sarae Sales Brain)

Tài liệu này dành cho **chủ shop** và cho **AI khác (Codex, Claude…)** cần tạo hàng loạt dữ liệu.
Định dạng chính xác (hợp đồng) nằm ở [`AI_KNOWLEDGE_FORMAT.md`](AI_KNOWLEDGE_FORMAT.md). Nếu hai tài liệu khác nhau,
**làm theo AI_KNOWLEDGE_FORMAT.md**.

---

## 1. Ý tưởng chính: 3 lớp kiến thức

AI **không cần học lại cho từng sản phẩm**. Ta viết câu mẫu MỘT lần, rồi ghép với dữ liệu của sản phẩm đang LIVE.

```
 Người xem gõ: "55kg mặc sz j"
          │
          ▼
 ┌──────────────────────┐   khớp mẫu    ┌──────────────────────────────────────────┐
 │  2. MẪU CÂU (template)│ ───────────► │ "{ask.weight} mặc {calc.size_for_weight}  │
 │  viết 1 lần cho mọi SP│               │   là vừa nha."                            │
 └──────────────────────┘               └──────────────────────────────────────────┘
          │ điền chỗ trống                                  ▲
          ▼                                                 │
 ┌──────────────────────┐   ┌──────────────────────┐        │
 │ 3. DỮ LIỆU SẢN PHẨM   │   │ 1. CHÍNH SÁCH SHOP   │ ───────┘
 │ giá, bảng size, màu.. │   │ ship, đổi trả, COD.. │
 └──────────────────────┘   └──────────────────────┘
          │
          ▼
 Giọng đọc trên LIVE: "Năm mươi lăm ký mặc M là vừa nha."
```

| Lớp | Là gì | Viết mấy lần | Ví dụ |
|---|---|---|---|
| 1. Chính sách shop (`policy`) | Điều đúng cho cả shop | 1 lần cho shop | `ship_time` = `2-4 ngày` |
| 2. Mẫu câu (`template`) | Kiểu câu hỏi + câu trả lời có `{chỗ trống}` | 1 lần cho mọi sản phẩm (hoặc 1 loại hàng) | `Mẫu này {product.price} thôi nha.` |
| 3. Dữ liệu sản phẩm (`product_facts`) | Giá (TikTok), bảng size, màu, chất liệu, loại, thông tin thêm | Mỗi sản phẩm (chỉ dữ liệu, không viết câu) | `material` = `cotton 100%` |

Thêm một loại nữa: **hỏi đáp riêng 1 sản phẩm** (`product_qa`) — dùng khi câu hỏi chỉ đúng cho đúng 1 mẫu
(vd "váy có dây buộc eo k").

**Thứ tự AI trả lời một câu hỏi:**

| Bước | Nguồn | Ghi chú |
|---|---|---|
| 1 | Luật có sẵn (giá, bảng size, màu, chất liệu) | Không tốn AI |
| 2 | Mẫu câu (`template`) | File này dạy chủ yếu phần này |
| 3 | Hỏi đáp riêng sản phẩm (Hỏi đáp AI › Kho hỏi đáp) | |
| 4 | AI (gom nhiều câu hỏi gọi 1 lần) | Câu trả lời tốt được biến thành mẫu mới |

Mẫu chỉ được dùng khi **mọi** chỗ trống trong mẫu đều có dữ liệu. Ví dụ mẫu có `{product.care}` mà sản phẩm chưa
nhập "cách giặt" thì mẫu đó tự bỏ qua — không bao giờ nói sai.

---

## 2. Nhập dữ liệu trên web

| Muốn làm | Vào đâu |
|---|---|
| Thêm / sửa mẫu câu dùng chung | **Hỏi đáp AI › Mẫu chung** |
| Nhập chính sách shop (ship, đổi trả, COD…) | **Hỏi đáp AI › Chính sách shop** |
| Xuất file sao lưu / nhập file JSONL | **Hỏi đáp AI › Sao lưu & nhập** |
| Nhập dữ liệu cho 1 sản phẩm | Mở sản phẩm › mục **Thông tin cho AI**: chọn **Loại hàng** (category) và điền **Thông tin thêm** (extras: `form`, `care`, `fit_tip`…) |
| Câu trả lời chỉ cho 1 sản phẩm | **Hỏi đáp AI › Kho hỏi đáp** |

Mẹo: nhập đủ 10 chính sách (mục 5) và các extras hay dùng (mục 6) thì gần như mọi mẫu có sẵn đều chạy được.

---

## 3. Viết mẫu câu hay (giọng người bán thật)

Câu `say` được **đọc to trên LIVE**. Nghe phải giống người bán đang nói chuyện: ngắn, vui, vào thẳng ý.

| NÊN | KHÔNG NÊN |
|---|---|
| `Mẫu này {product.price} thôi nha.` | `Dạ chào anh chị, sản phẩm có giá {product.price} ạ.` |
| `{ask.weight} mặc {calc.size_for_weight} là vừa nha.` | `Bạn ơi, với cân nặng {ask.weight} thì bạn nên chọn size {calc.size_for_weight}.` |
| `Còn hàng nha, chốt lẹ nè.` | `Mấy bạn hỏi còn hàng không thì shop xin trả lời là còn.` |
| Dùng "nha", "nè", "luôn á", "nhé", "đó" — mỗi câu một kiểu | Câu nào cũng y một khuôn, nghe như máy |
| Số viết bằng chữ số: `55kg`, `2-4 ngày`, `1m6` (giọng đọc tự đổi ra chữ) | Viết "năm mươi lăm ký" |
| Giá luôn là chỗ trống `{product.price}`, `{shop.freeship_min}` | Ghi cứng `199k` trong mẫu (bị báo lỗi) |
| 3–6 câu `say` khác nhau cho 1 mẫu | Chỉ 1 câu, LIVE nghe lặp đi lặp lại |
| Tối đa 18 từ / câu `say`, 90 ký tự / câu `text` | Giải thích dài dòng |
| Không biết thì hỏi lại ngắn: `Cho mình xin cân nặng nha.` | Bịa thông tin |

Cấm: chào hỏi mở đầu ("Dạ", "Chào", "Xin chào", "Cảm ơn"), gọi tên người xem, "bạn ơi", "mấy bạn hỏi",
emoji, link, số điện thoại, từ tục.

**`patterns`** = cách người xem THẬT gõ: ngắn, không dấu, teencode. Càng nhiều kiểu càng khớp nhiều câu.

| Teencode | Nghĩa |
|---|---|
| `bn`, `bnhieu`, `nhiu` | bao nhiêu |
| `k`, `ko`, `hông`, `hok` | không |
| `sz` | size |
| `j` | gì |
| `z`, `v` | vậy |
| `đc` | được |
| `sp` | sản phẩm |
| `m6`, `1m6` | 1 mét 6 (chiều cao) |
| `e`, `c`, `a` | em, chị, anh |
| `fs` | freeship |
| `ck` | chuyển khoản |

---

## 4. Định dạng file JSONL

Mỗi dòng là 1 JSON. Dòng bắt đầu bằng `//` và dòng trống được bỏ qua. Dòng đầu là `meta`.

```jsonl
{"type":"meta","format":"sarae-knowledge","version":1}
// ===== Chính sách =====
{"type":"policy","key":"ship_time","value":"2-4 ngày","note":"Toàn quốc"}
// ===== Mẫu câu =====
{"type":"template","id":"size.weight.basic","intent":"ASK_SIZE","category":null,"when":null,"patterns":["{ask.weight} mặc size gì","nặng {ask.weight} mặc gì vừa"],"say":["{ask.weight} mặc {calc.size_for_weight} là vừa nha.","Nặng {ask.weight} thì lấy {calc.size_for_weight} nha."],"text":["{ask.weight} lấy {calc.size_for_weight} vừa nha"]}
// ===== Dữ liệu sản phẩm =====
{"type":"product_facts","product":{"sku":"TK-123"},"facts":{"category":"áo","material":"cotton","colors":["trắng","be"],"sizes":[{"size":"M","weight_min":45,"weight_max":55}],"extras":{"form":"rộng","care":"giặt tay nước lạnh"}}}
{"type":"product_qa","product":{"name":"Áo sơ mi trắng"},"question":"có túi không","intent":"GENERAL","say":["Có một túi ngực nhỏ nha."],"text":["có 1 túi ngực nha"]}
```

### Trường của `template`

| Trường | Bắt buộc | Quy tắc |
|---|---|---|
| `id` | nên có | `[a-z0-9_.-]{3,80}`, dễ đọc: `price.basic`, `size.weight.basic`. Trùng id = cập nhật |
| `intent` | có | Một trong bảng intent bên dưới |
| `category` | không | Chỉ dùng cho sản phẩm thuộc loại này (`"váy"`), `null` = mọi loại |
| `when` | không | 1 điều kiện (bảng bên dưới), `null` = luôn dùng |
| `patterns` | có | 1–30 câu hỏi mẫu (khuyên 4–12) |
| `say` | có | 1–8 câu đọc (khuyên 3–6), ≤ 18 từ |
| `text` | không | 0–8 câu comment (khuyên 2–4), ≤ 90 ký tự |

### Chỗ trống (placeholder)

| Placeholder | Giá trị |
|---|---|
| `{product.name}` | Tên sản phẩm |
| `{product.price}` | Giá, vd `179k`, `1tr2` |
| `{product.old_price}` | Giá gốc (chỉ khi cao hơn giá bán) |
| `{product.discount_percent}` | vd `20%` (chỉ khi có giá gốc) |
| `{product.colors}` | vd `trắng, đen với be` |
| `{product.sizes}` | vd `M, L với XL` |
| `{product.material}` | vd `cotton 100%` |
| `{product.stock}` | Số lượng tồn từ TikTok |
| `{product.category}` | Loại hàng shop đặt (vd `áo`, `váy`) |
| `{product.<key>}` | Thông tin thêm (extras) của sản phẩm, vd `{product.form}` = `rộng` |
| `{ask.weight}` | Cân nặng trong câu hỏi, vd `55kg` |
| `{ask.height}` | Chiều cao trong câu hỏi, vd `160cm` |
| `{ask.color}` | Màu được hỏi |
| `{ask.size}` | Size được hỏi |
| `{calc.size_for_weight}` | Size theo bảng size cho `{ask.weight}` (và chiều cao nếu có), vd `M` hoặc `M hoặc L` |
| `{calc.color_count}` | Số màu |
| `{shop.<key>}` | Chính sách shop, vd `{shop.ship_time}` |

Quy tắc: `{ask.*}` dùng trong `say`/`text` thì **phải có** trong ít nhất 1 `pattern`. Key viết thường không dấu:
`[a-z][a-z0-9_]{1,30}`.

### Điều kiện `when`

| `when` | Đúng khi |
|---|---|
| `asked_color_available` | `{ask.color}` là một màu của sản phẩm |
| `asked_color_unavailable` | có hỏi màu và sản phẩm không có màu đó |
| `asked_size_available` / `asked_size_unavailable` | tương tự cho size |
| `in_stock` / `out_of_stock` | tồn kho > 0 / = 0 |
| `has_discount` | có giá gốc cao hơn giá bán |

Cặp "có / không có" nên viết **2 mẫu cùng patterns**, khác `when` (xem `size.available` và `size.unavailable` trong
file mẫu có sẵn).

### Intent

| Intent | Câu hỏi về | Ví dụ người xem gõ |
|---|---|---|
| `ASK_PRICE` | Giá, giảm giá, mặc cả | `giá bn`, `bớt đi shop` |
| `ASK_SIZE` | Size, tư vấn theo cân nặng | `55kg mặc sz j`, `có size L k` |
| `ASK_COLOR` | Màu | `có màu gì`, `có màu đen k` |
| `ASK_MATERIAL` | Chất vải, co giãn, độ dày | `vải gì`, `có mỏng k` |
| `ASK_STOCK` | Còn hàng | `còn k shop` |
| `ASK_SHIPPING` | Thời gian, phí ship | `ship mấy ngày` |
| `ASK_RETURN` | Đổi trả, hàng lỗi | `đổi size đc k` |
| `ASK_PAYMENT` | Thanh toán, COD | `có cod k` |
| `ASK_PROMO` | Khuyến mãi, mua nhiều | `có voucher k` |
| `ASK_CARE` | Giặt, bảo quản | `giặt máy đc k` |
| `ASK_STYLE` | Form, phối đồ, dịp mặc | `form rộng k`, `phối với gì` |
| `ASK_QUALITY` | Chất lượng, giống hình | `hàng có tốt k` |
| `SHOW_PRODUCT` | Xin xem / mặc thử | `quay gần đi` |
| `ORDER` | Chốt đơn, cách đặt | `chốt`, `lấy 1` |
| `GENERAL` | Câu khác có dữ liệu (túi, khóa, giờ LIVE) | `có túi k` |

---

## 5. Khóa chính sách shop (`{shop.*}`)

Giá trị là **một cụm từ ghép vừa vào câu mẫu** (cột "Câu mẫu đọc ra"). Viết ngắn, không chào hỏi, không dấu chấm cuối.

| Key | Ý nghĩa | Giá trị ví dụ | Câu mẫu đọc ra |
|---|---|---|---|
| `ship_time` | Thời gian giao | `2-4 ngày` | "Ship 2-4 ngày là tới nha." |
| `ship_fee` | Phí ship | `30k` | "Phí ship 30k nha." |
| `freeship_min` | Đơn tối thiểu được freeship | `299k` | "Đơn từ 299k là freeship luôn nha." |
| `return_policy` | Đổi trả (đọc sau "Shop") | `cho đổi size trong 7 ngày, còn tem mác là được` | "Shop cho đổi size trong 7 ngày… nha." |
| `payment` | Cách thanh toán | `COD, chuyển khoản hoặc ví TikTok` | "Thanh toán COD, chuyển khoản… đều được nha." |
| `cod` | COD (đọc sau "Bên mình") | `có COD, nhận hàng rồi mới trả tiền` | "Bên mình có COD, nhận hàng rồi mới trả tiền nha." |
| `promo` | Khuyến mãi đang chạy (đọc sau "Đang có") | `giảm 10% khi mua từ 2 món` | "Đang có giảm 10% khi mua từ 2 món nha." |
| `warranty` | Hàng lỗi / bảo hành (đọc sau "Yên tâm nha,") | `hàng lỗi do shop thì đổi mới trong 7 ngày` | "Yên tâm nha, hàng lỗi do shop thì đổi mới…" |
| `order_how` | Cách đặt, bắt đầu bằng động từ | `bấm giỏ hàng góc trái, chọn màu size rồi đặt` | "Chốt nha, bấm giỏ hàng góc trái… là xong luôn." |
| `shop_hours` | Giờ LIVE (đọc sau "Shop lên LIVE") | `từ 8h tới 11h tối mỗi ngày` | "Shop lên LIVE từ 8h tới 11h tối mỗi ngày nha." |

Chỉ dùng đúng 10 key này cho các ý trên (đừng đặt `ship_days`, `freeship`…), để mọi mẫu dùng chung chạy được.
Muốn thêm ý mới thì tạo key mới, viết thường không dấu, và viết mẫu dùng key đó.

## 6. Thông tin thêm của sản phẩm (extras) mà mẫu có sẵn dùng

| Key | Ý nghĩa | Ví dụ giá trị |
|---|---|---|
| `form` | Form dáng | `rộng`, `ôm nhẹ`, `ống suông, lưng cao` |
| `fit_tip` | Mẹo chọn size | `thích mặc rộng thì lên 1 size` |
| `care` | Cách giặt | `giặt tay nước lạnh, không vắt mạnh` |
| `stretch` | Co giãn | `co giãn 4 chiều`, `không co giãn` |
| `thickness` | Độ dày | `dày vừa, mặc màu trắng không lộ` |
| `lining` | Lót | `có lót trong, không lộ` |
| `length` | Độ dài | `dài qua gối` |
| `mix_tip` | Gợi ý phối | `phối quần jean hay chân váy đều xinh` |
| `occasion` | Dịp mặc | `đi làm, đi chơi đều hợp` |
| `for_who` | Ai mặc | `nam nữ đều mặc được` |
| `origin` | Xuất xứ | `Việt Nam, xưởng nhà may` |
| `pockets` | Túi | `có 2 túi hai bên` |
| `closure` | Khóa / nút | `kéo khóa sau lưng` |
| `waist` | Lưng quần/váy | `lưng thun co giãn` |

Giá trị phải **đọc trôi khi ghép** vào câu: "Mẫu này {product.lining} nha." → "Mẫu này có lót trong, không lộ nha."

---

## 7. Cách một AI agent "dạy" bộ não (từng bước)

```
 viết JSONL ──► validate ──► sửa lỗi ──► import --dry-run ──► import thật ──► commit git
   knowledge/        │  ▲                       │
                     └──┘ (lặp tới khi sạch)     └─ xem số dòng thêm/cập nhật/bỏ qua
```

| Bước | Lệnh / việc (chạy trong thư mục `backend/`) |
|---|---|
| 1. Tạo file | Viết file JSONL trong thư mục `knowledge/` ở gốc repo, vd `knowledge/templates_ao_2026-10.jsonl` (dòng đầu là `meta`, chia mục bằng `//`) |
| 2. Kiểm tra | `python -m app.knowledge_cli validate ../knowledge/templates_ao_2026-10.jsonl` (không cần database) |
| 3. Sửa lỗi | Lỗi báo theo số dòng: placeholder lạ, `{ask.*}` không có trong pattern, giá ghi cứng, câu quá dài, link/số điện thoại… Sửa rồi chạy lại bước 2 tới khi hết lỗi |
| 4. Chạy thử | `python -m app.knowledge_cli import --owner EMAIL ../knowledge/templates_ao_2026-10.jsonl --dry-run` |
| 5. Nhập thật | Bỏ `--dry-run` |
| 6. Sao lưu | `git add knowledge/…` và commit — file trong `knowledge/` là bản gốc |

Mẫu có sẵn của app: `backend/app/knowledge/builtin_vi.jsonl`. Ví dụ đầy đủ một shop: `knowledge/examples/sample_shop.jsonl`.
Muốn sửa một mẫu có sẵn: dùng **cùng `id`** trong file của shop (trùng id = cập nhật).

## 8. Sao lưu & khôi phục

| Việc | Cách |
|---|---|
| Xuất toàn bộ (web) | **Hỏi đáp AI › Sao lưu & nhập** › Xuất |
| Xuất (dòng lệnh) | `python -m app.knowledge_cli export --owner EMAIL ../knowledge/backup_YYYY-MM-DD.jsonl` |
| Xuất (API) | `GET /api/ai/knowledge/export` |
| Khôi phục | Nhập lại file (web, hoặc `import --owner EMAIL FILE`). Trùng id = ghi đè, không tạo bản sao |

Quy tắc vàng: **file trong `knowledge/` là nguồn gốc**, luôn để trong git. Sửa trên web xong thì xuất lại và commit,
để database hỏng vẫn khôi phục được.

---

## 9. Prompt dán cho Codex / AI khác

```text
Bạn tạo dữ liệu huấn luyện cho AI trả lời người xem LIVE TikTok của một shop quần áo Việt Nam.
Đọc kỹ docs/AI_KNOWLEDGE_FORMAT.md (hợp đồng, phải theo đúng) và docs/AI_KNOWLEDGE_GUIDE.md.
Tham khảo backend/app/knowledge/builtin_vi.jsonl để biết giọng văn và id đã có (không tạo trùng ý).

NHIỆM VỤ: viết file knowledge/<tên>.jsonl gồm khoảng <N> dòng "template" cho chủ đề <chủ đề / loại hàng>.

LUẬT:
1. Mỗi dòng 1 JSON hợp lệ, UTF-8. Dòng đầu: {"type":"meta","format":"sarae-knowledge","version":1}. Chia mục bằng dòng "// ...".
2. Mỗi template: id dễ đọc, duy nhất ([a-z0-9_.-], vd "style.mix.vay"), intent đúng bảng, category null hoặc loại hàng, when null hoặc 1 điều kiện trong bảng.
3. patterns: 4-12 câu, đúng cách người xem gõ (ngắn, teencode: bn, bnhieu, sz, k, ko, j, z, đc, m6). Đánh dấu chỗ có giá trị bằng {ask.weight}, {ask.height}, {ask.color}, {ask.size}.
4. say: 3-6 câu KHÁC NHAU, tối đa 18 từ, giọng người bán thật: ngắn, ấm, vào thẳng ý, dùng "nha", "nè", "luôn á", "nhé", "đó".
   KHÔNG mở đầu "Dạ", "Chào", "Xin chào", "Cảm ơn"; KHÔNG tên người xem, KHÔNG "bạn ơi", "mấy bạn hỏi"; KHÔNG emoji.
5. text: 2-4 câu comment, tối đa 90 ký tự, được viết tắt nhẹ.
6. Số viết bằng chữ số. Giá/tiền CHỈ qua placeholder ({product.price}, {product.old_price}, {shop.ship_fee}, {shop.freeship_min}); không bao giờ ghi cứng số tiền.
7. Chỉ dùng placeholder có trong hợp đồng. Chính sách shop chỉ qua 10 key: ship_time, ship_fee, freeship_min, return_policy, payment, cod, promo, warranty, order_how, shop_hours. Thông tin sản phẩm thêm qua {product.<extra>} với các key ở mục 6 của guide.
8. {ask.*} dùng trong say/text phải xuất hiện trong ít nhất 1 pattern. Mọi placeholder trong 1 template phải cùng có dữ liệu thì mẫu mới chạy: đừng nhét quá nhiều placeholder vào 1 mẫu.
9. Không bịa sự thật về sản phẩm (không khẳng định "không phai", "mát") nếu không có placeholder chứa dữ liệu đó. Thiếu dữ liệu thì hỏi lại ngắn ("Cho mình xin cân nặng nha.").
10. Không link, không số điện thoại, không từ tục.
11. Cặp có/không (màu, size, còn hàng) viết 2 template cùng patterns, khác when.

SAU KHI VIẾT: chạy (trong backend/) `python -m app.knowledge_cli validate ../knowledge/<tên>.jsonl`, sửa đến khi 0 lỗi,
rồi `python -m app.knowledge_cli import --owner <EMAIL> ../knowledge/<tên>.jsonl --dry-run` và báo cáo kết quả.
Không sửa code. Báo cáo: số template theo intent, lỗi đã sửa.

VÍ DỤ ĐÚNG:
{"type":"template","id":"price.basic","intent":"ASK_PRICE","category":null,"when":null,"patterns":["giá bn","bn tiền","áo này bn","mấy k vậy"],"say":["Mẫu này {product.price} thôi nha.","Cái này {product.price} nè, rẻ lắm luôn á.","Giá {product.price} thôi đó, chốt lẹ nha."],"text":["{product.price} nha, mẫu này đẹp lắm luôn á","giá {product.price} thôi nha"]}
VÍ DỤ SAI (say): "Dạ chào anh chị, sản phẩm có giá 200.000 VND ạ."
```
