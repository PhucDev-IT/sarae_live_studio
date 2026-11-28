# Hybrid Studio: máy khách + server PC nhà

Quyết định ngày 2026-11-28. Đây là hợp đồng giữa **gateway** (server ở PC nhà) và **Studio** (app ở máy khách).
Hai phía làm song song, nên chỉ sửa tài liệu này khi cả hai phía cùng đổi theo.

## Phân vai

| Máy khách (Studio, Tauri/web + `agent-core`) | Server PC nhà (`backend/app/gateway_main.py`) |
|---|---|
| Giao diện, OBS, Chrome LIVE, video, FFmpeg, SQLite `studio-data` | Kích hoạt máy, license, danh sách máy |
| AI trả lời bình luận: **khách tự cấu hình** provider/khóa (`studio/ai.py`, DPAPI) | TTS giọng clone: proxy tới `sarae-voice-server` (127.0.0.1:8780) |
| Giọng Edge (không cần server) | Hàng đợi GPU, ghi nhận usage |

Server **không** host AI, sản phẩm, video hay phiên LIVE.

## Mạng

```
máy khách ──HTTPS──▶ Cloudflare ──tunnel──▶ cloudflared (PC nhà) ──▶ 127.0.0.1:8000 gateway ──▶ 127.0.0.1:8780 voice server
```

- Hostname public: `https://api.puns.id.vn`. Gateway và voice server chỉ bind `127.0.0.1`.
- Mọi request phải xong dưới 100 giây (giới hạn của Cloudflare). TTS dừng chờ hàng đợi sau 60 giây.
- Gateway lấy IP khách từ `CF-Connecting-IP`, rồi mới tới `X-Forwarded-For`, rồi tới địa chỉ của socket.

## Xác thực máy

1. Admin chạy CLI để tạo khách (license: `max_agents`, `expires_at`), sau đó cấp một **mã kích hoạt** dùng một lần.
2. Khách dán mã vào Studio. Studio gọi `POST /api/identity/activate` và nhận `agent_id` + `agent_credential`.
   Credential được lưu bằng DPAPI và không bao giờ ghi ra log.
3. Mọi request sau đó gửi kèm:
   - `X-Sarae-Agent: <agent_id>`
   - `Authorization: Bearer <agent_credential>`

Lỗi luôn có dạng: HTTP status, header `X-Sarae-Error: <CODE>` và body `{"detail": "<câu tiếng Việt>"}`.

| Code | Status | Studio làm gì |
|---|---|---|
| `AGENT_NOT_ACTIVATED` / `AGENT_UNAUTHORIZED` | 401 / 403 | Xóa credential, hiện lại màn hình kích hoạt |
| `AGENT_REVOKED` | 403 | Như trên, kèm thông báo "máy đã bị gỡ" |
| `LICENSE_EXPIRED` | 403 | Khóa app (xem quy tắc bên dưới) |
| `ACTIVATION_KEY_INVALID` / `ACTIVATION_KEY_USED` / `AGENT_LIMIT_REACHED` | 403 / 403 / 409 | Báo lỗi trên màn hình kích hoạt |
| `RATE_LIMITED` | 429 | Thử lại sau `Retry-After` giây |
| `TTS_BUSY` | 429 | Khách đang có một câu đang tạo; Studio chờ câu đó rồi mới gửi tiếp |
| `TTS_QUEUE_TIMEOUT` | 503 | Hàng đợi GPU quá 60 giây; đọc bằng giọng Edge cho câu đó |
| `TTS_UNAVAILABLE` | 503 | Voice server tắt hoặc lỗi; đọc bằng giọng Edge |
| `VOICE_NOT_FOUND` / `INVALID_PARAMS` | 404 / 422 | Báo lỗi cấu hình giọng |

## Endpoint

### `GET /api/health`
Không cần xác thực. Trả về `{"status":"UP","service":"sarae-studio-gateway","version":"…"}`.

### `POST /api/identity/activate`
Không cần xác thực, giới hạn 10 lần mỗi IP trong 10 phút.
- Body: `{"activation_key": "SARAE-ACT-…", "hostname": "<tên máy>", "platform": "WINDOWS"}`
- Trả về 200: `{"agent_id": "agt_…", "agent_credential": "…", "owner_id": "…", "platform": "WINDOWS"}`

Đây là endpoint sẵn có trong `backend/app/identity.py`. Gateway chỉ mở endpoint này, không mở `/login`, `/owners` hay các route admin khác.

### `GET /api/studio/v1/license`
Cần xác thực máy. Trả về:
```json
{"valid": true, "email": "shop_linh" (tên tài khoản; trường vẫn tên là email), "expires_at": "2027-01-01T00:00:00+00:00" | null,
 "max_agents": 2, "agent_id": "agt_…", "hostname": "…",
 "server_time": "<ISO UTC>", "grace_hours": 72}
```

### `GET /api/studio/v1/voices`
Cần xác thực máy. Mọi khách có license đều thấy **tất cả** giọng.
```json
{"online": true, "voices": [{"voice_id": "abc_123", "name": "Giọng A", "rev": "…", "ready": true}]}
```
- `online` là `false` khi voice server tắt. Lúc đó `voices` là danh sách đọc gần nhất (có thể rỗng), với `ready: false`.
- Không bao giờ trả đường dẫn file, transcript, prompt hay token.

### `POST /api/studio/v1/tts/synthesize`
Cần xác thực máy.
- Body: `{"voice_id": "<VOICE_ID>", "text": "<1..450 ký tự>", "preset": "live_fast"}`
- Trả về 200: `audio/wav` (RIFF/WAVE, tối đa 10 MB), kèm header `X-Voice-Revision`.

Quy tắc hàng đợi:
- Mỗi khách (owner) chỉ có **1 câu đang tạo**. Câu thứ hai gửi tới khi câu đầu chưa xong nhận `429 TTS_BUSY`.
- GPU xử lý toàn cục `SARAE_GATEWAY_TTS_CONCURRENCY` câu cùng lúc (mặc định 1). Các câu khác xếp hàng theo thứ tự đến, quá 60 giây thì nhận `503 TTS_QUEUE_TIMEOUT`.
- Tài khoản được bật **ưu tiên** (CLI `priority --on`) có câu chen lên trước mọi câu thường đang chờ. Câu đang tạo dở không bao giờ bị ngắt. Dùng cho tài khoản của chính admin khi LIVE trên PC server.
- Mỗi request đều được ghi vào usage, kể cả request lỗi: owner, agent, ngày theo UTC+7, số request, số ký tự, số lỗi, số byte audio. **Không** chặn theo hạn mức.

Token của voice server (`SARAE_TTS_SERVER_TOKEN`) chỉ nằm trên gateway.

## Quy tắc license ở Studio (chặn cả app)

- Chưa kích hoạt: chỉ hiện màn hình kích hoạt. Core từ chối mọi method khác với `LICENSE_REQUIRED`, trừ các method `account.*` và method trạng thái hệ thống.
- Studio kiểm tra license khi khởi động và sau đó mỗi 6 giờ. Lần kiểm tra hợp lệ gần nhất được lưu (DPAPI) kèm `checked_at`.
- Server không trả lời được (mạng hoặc 5xx): vẫn mở app nếu lần hợp lệ gần nhất chưa quá `grace_hours` (72 giờ). Quá hạn đó thì khóa app.
- Server trả `LICENSE_EXPIRED`, `AGENT_REVOKED` hoặc `AGENT_UNAUTHORIZED`: khóa app, **nhưng không bao giờ cắt phiên LIVE đang chạy**. Phiên đang LIVE (video, OBS, trả lời bằng AI, giọng Edge) chạy tiếp đến khi kết thúc. Trong lúc đó chỉ chặn việc bắt đầu phiên LIVE mới và mở các màn hình khác.
- Biến môi trường `SARAE_STUDIO_SERVER_URL` (mặc định `https://api.puns.id.vn`) dùng để chạy với gateway ở local.
- Biến `SARAE_STUDIO_DEV_UNLICENSED=1` chỉ dành cho dev/test và bỏ qua license. Khi bật, Studio ghi log cảnh báo.

## Giọng ở Studio

- Danh mục giọng gồm: giọng Edge, cộng với `remote:<voice_id>` lấy từ gateway.
- Khi gateway lỗi hoặc tắt (`TTS_UNAVAILABLE`, `TTS_QUEUE_TIMEOUT`, lỗi mạng), câu đó được đọc bằng giọng Edge mặc định (`vi-VN-HoaiMyNeural`) và Studio ghi log. Phiên LIVE không bao giờ dừng vì TTS.
- Chế độ voice server local cũ (`local:<id>`, 127.0.0.1:8780) chỉ còn dành cho dev, bật bằng `SARAE_STUDIO_VOICE_MODE=local`.

## Vận hành server

Phần này dành cho người chạy server ở PC nhà. Code: `backend/app/gateway_main.py` (app), `backend/app/studio_gateway.py` (license, giọng, TTS, usage), `backend/tools/studio_admin.py` (CLI).

### Cấu hình `backend/.env`

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `SARAE_TTS_SERVER_URL` | `http://127.0.0.1:8780` | Địa chỉ `sarae-voice-server`. Để trống thì mọi câu TTS nhận `TTS_UNAVAILABLE`. |
| `SARAE_TTS_SERVER_TOKEN` | token orchestrator | Lấy token đầu tiên trong `SARAE_VOICE_ORCHESTRATOR_TOKENS` ở `.env` của voice server. Token này chỉ nằm trên gateway. |
| `SARAE_GATEWAY_TTS_CONCURRENCY` | `1` | Số câu GPU tạo cùng lúc cho mọi khách. |
| `SARAE_GATEWAY_DOCS` | để trống | Đặt `1` thì mở `/docs` và `/openapi.json`. Chỉ dùng khi debug. |
| `SARAE_DATA_DIR` | để trống | Đây là biến môi trường của Windows, không đọc từ `.env`. Mặc định dữ liệu nằm ở `backend\data` (`identity.sqlite3`, `studio_gateway.sqlite3`). CLI và gateway phải dùng cùng một thư mục. |

Dữ liệu trong `backend\data` là danh sách khách và máy đã kích hoạt. Hãy sao lưu thư mục này định kỳ.

### Thứ tự khởi động

1. **Voice server**: chạy `sarae-voice-server` (127.0.0.1:8780) và chờ model nạp xong (`/health/ready` trả `ready`).
2. **Gateway**: chạy `start-server.cmd` ở thư mục gốc dự án. Lần đầu script tự tạo `backend\.venv` và cài `backend\requirements-gateway.txt`. Gateway chạy ở `http://127.0.0.1:8000` với **một worker** vì hàng đợi TTS và giới hạn request nằm trong bộ nhớ. Kiểm tra bằng `http://127.0.0.1:8000/api/health`.
3. **cloudflared**: `cloudflared tunnel run sarae-studio`. Sau đó kiểm tra `https://api.puns.id.vn/api/health` từ một máy khác.

Gateway vẫn chạy khi voice server tắt: license và kích hoạt vẫn hoạt động, còn TTS trả `TTS_UNAVAILABLE` và Studio đọc bằng giọng Edge.

**PC không được ngủ.** Trong Windows, vào Settings → System → Power, đặt *Sleep* = *Never* khi cắm điện. Nếu máy ngủ thì mọi khách mất giọng clone, và nếu máy tắt quá 72 giờ thì khách bị khóa app.

### Mẫu cloudflared `config.yml`

Đặt ở `%USERPROFILE%\.cloudflared\config.yml`. Tạo tunnel bằng `cloudflared tunnel create sarae-studio`, rồi `cloudflared tunnel route dns sarae-studio api.puns.id.vn`.

```yaml
tunnel: sarae-studio
credentials-file: C:\Users\<user>\.cloudflared\<TUNNEL_ID>.json

ingress:
  - hostname: api.puns.id.vn
    service: http://127.0.0.1:8000
    originRequest:
      connectTimeout: 10s
  - service: http_status:404
```

Chỉ đưa gateway ra Internet. Không thêm ingress cho voice server (8780) hay Studio.

### Quản lý khách bằng CLI

Chạy trong thư mục `backend` bằng Python của `backend\.venv`, ví dụ `.venv\Scripts\python.exe -m tools.studio_admin list`. CLI đọc và ghi thẳng SQLite, nên không cần gateway đang chạy. Các thay đổi có hiệu lực ngay ở request tiếp theo của Studio.

| Lệnh | Việc làm |
|---|---|
| `create-customer --username shop_linh --max-devices 2 --expires 2027-01-31` | Tạo khách và license. Hạn tính đến hết ngày đó theo giờ Việt Nam. Dùng `--never` nếu không có hạn. Mật khẩu được tạo ngẫu nhiên và không in ra, vì khách không đăng nhập. |
| `issue-key --username shop_linh` | Cấp **một** mã kích hoạt dùng một lần cho một máy. Mã chỉ hiện một lần. |
| `list` | Danh sách khách: trạng thái, số máy đang dùng / tối đa, hạn. |
| `extend --username shop_linh --expires 2027-06-30` (hoặc `--never`) | Đổi hạn license. |
| `disable --username …` / `enable --username …` | Khóa hoặc mở license. Máy của khách nhận `LICENSE_EXPIRED`. |
| `devices --username …` | Các máy đã kích hoạt (`agent_id`, tên máy, trạng thái). |
| `revoke-device --agent-id agt_…` | Gỡ một máy. Máy đó nhận `AGENT_REVOKED` và được trả lại một suất máy. |
| `priority --username … --on` / `--off` | Bật/tắt ưu tiên hàng đợi TTS cho tài khoản. `list` hiện `[ưu tiên]`. |
| `usage [--username …] [--days 7]` | Usage TTS theo ngày (UTC+7): số câu, số lỗi, số ký tự, MB audio. |

Phiên LIVE đang chạy của khách không bị cắt khi khóa hoặc gỡ máy (xem "Quy tắc license ở Studio").

### Giới hạn đang áp dụng

- Kích hoạt: 10 lần mỗi IP trong 10 phút.
- Mỗi máy: 120 request mỗi phút cho `/api/studio/v1/*`, vượt quá thì nhận `429 RATE_LIMITED` kèm `Retry-After`.
- Body request tối đa 64 KB.
- Lỗi từ voice server được đổi như sau: `VOICE_NOT_FOUND` → `404 VOICE_NOT_FOUND`; 400/422 → `422 INVALID_PARAMS`; voice server bận (429 `TTS_BUSY`/`TTS_QUEUE_TIMEOUT`) → `503 TTS_QUEUE_TIMEOUT`; mất kết nối, 503, các lỗi khác, hoặc audio không phải RIFF/WAVE hay quá 10 MB → `503 TTS_UNAVAILABLE`.

## Trang admin (chỉ trên PC server)

- Tab **Khách hàng**: tạo khách (nhận ngay mã kích hoạt), cấp thêm mã, đổi số máy tối đa và ngày hết hạn, bật/tắt ưu tiên TTS, khóa/mở license, xem và gỡ máy, xem usage 7 ngày. Mọi việc ở đây giống hệt lệnh CLI `tools.studio_admin`, CLI vẫn dùng được.
- Tab **Giọng clone**: như mô tả bên dưới.

### Quản lý giọng (chỉ admin)

- Chạy `start-admin.cmd` trên PC server, rồi mở http://127.0.0.1:8001. Cần voice server đang chạy.
- Trang này làm được: thêm giọng (WAV mẫu 1 đến 20 giây, tối đa 5 MB, transcript không bắt buộc), sửa transcript hoặc quay về Whisper, nghe thử bằng preset `live_fast` giống hệt khi LIVE, xóa giọng.
- Trang **không** được đưa qua cloudflared. `config.yml` chỉ trỏ `api.puns.id.vn` tới cổng 8000. Ngoài ra trang còn tự chặn: chỉ nhận kết nối từ 127.0.0.1, Host phải là `127.0.0.1:8001` hoặc `localhost:8001`, và mọi API phải có header `X-Sarae-Admin: 1`.
- Giọng mới hiện ở app khách sau lần "Làm mới giọng" kế tiếp. Xóa một giọng thì khách đang chọn giọng đó sẽ đọc bằng Edge.
- Khách không clone được giọng. Gateway không có endpoint tải giọng lên.
