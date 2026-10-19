# Sarae Windows Agent — Hợp đồng IPC (GUI ↔ Agent Core) và Cloud API

IPC protocol **v1**. Nguồn chuẩn: `agent-core/src/sarae_agent/ipc/` (Python) và `desktop/src/types/ipc.ts` (TypeScript).

## 1. Khung truyền

```text
React ──invoke('core_request',{method,params,timeoutMs})──▶ Rust CoreManager
Rust  ──stdin: 1 dòng JSON──▶ sarae-agent-core.exe ──stdout: 1 dòng JSON──▶ Rust ──▶ Promise (response)
                                                                              └──▶ emit('core://event') (event)
```

* Mỗi thông điệp = **một object JSON ASCII trên một dòng** (`ensure_ascii`, kết thúc `\n`), tối đa 1 MB.
* **stdout chỉ chứa khung IPC.** Core nhân bản handle stdout gốc cho IPC rồi trỏ fd 1 sang stderr, nên `print()` hay
  thư viện native không thể chèn rác vào khung. Log đi qua sự kiện `log.entry`, file log và stderr.
* `core.ready` luôn là sự kiện **đầu tiên**.

| Loại | Hướng | Dạng |
|---|---|---|
| request | GUI → Core | `{"v":1,"type":"request","request_id":"g1-7","method":"media.scan","params":{}}` |
| response | Core → GUI | `{"v":1,"type":"response","request_id":"g1-7","ok":true,"result":{...},"error":null}` |
| response lỗi | Core → GUI | `{"v":1,"type":"response","request_id":"g1-7","ok":false,"result":null,"error":{"code":"OBS_NOT_RUNNING","message":"…","details":{}}}` |
| event | Core → GUI | `{"v":1,"type":"event","event":"media.scan_progress","data":{...},"ts":"2026-10-18T05:37:36.422+00:00"}` |

### Chống deadlock / timeout giả

| Vấn đề | Cách xử lý |
|---|---|
| GUI đọc chậm làm treo worker | Mọi khung vào hàng đợi, **một** thread ghi; khi đầy chỉ bỏ `log.entry`, không bao giờ bỏ response |
| Request chậm chặn request khác | Reader chỉ parse rồi giao cho thread pool 8 luồng (test: `obs.connect` treo không chặn `system.ping`) |
| Quét thư viện lâu → timeout | `media.scan` trả ngay `{job_id,state:"accepted"}`, tiến độ qua `media.scan_progress`/`media.scan_finished` |
| Core chết khi đang chờ | Rust trả lỗi `CORE_EXITED` cho mọi request đang chờ |
| Timeout | Rust: mặc định 30 s, `activation.activate` 45 s, `voice.speak` 60 s (giới hạn 1–120 s) |

## 2. Methods (GUI → Core)

Params được kiểm tra bằng Pydantic (`extra="forbid"`): sai kiểu/thừa trường → `INVALID_PARAMS`; tên lạ → `METHOD_NOT_FOUND`.
**Không có method chạy lệnh shell.**

| Method | Params | Kết quả |
|---|---|---|
| `system.status` | — | `{core, agent, cloud, obs, media, playback, monitor}` |
| `system.ping` / `system.methods` | — | `{pong}` / `{methods, cloud_command_kinds}` |
| `system.shutdown` | — | `{accepted}` rồi Core dừng mọi thứ và thoát (exit 0) |
| `config.get` | — | Cấu hình + `obs.password_set` + `paths` (không có bí mật) |
| `config.set` | `{cloud?, obs?, monitor?, agent?, media?:{rescan_minutes}, obs_password?, clear_obs_password?}` | Cấu hình mới |
| `activation.status` | — | `{activated, agent_id, activated_cloud_url, activated_at, hostname, credential_error}` |
| `activation.activate` | `{activation_key, cloud_url?}` | như trên |
| `activation.deactivate` | — | Xoá credential **trên máy này** |
| `agent.start` / `agent.stop` / `agent.restart` / `agent.status` | — | `AgentStatus` |
| `media.folders.list` / `.add` / `.remove` | `{path}` cho add/remove | `{folders:[{path,exists,video_count}]}` |
| `media.scan` / `media.scan_cancel` | — | `{job_id,state:"accepted"}` / `{cancelling}` |
| `media.list` | `{offset?,limit?≤1000,query?}` | `{total,offset,items[]}` |
| `media.summary` | — | `{video_count,folder_count,scanned_at,scan}` |
| `obs.status` | — | Trạng thái + `media_input_ok`, `voice_input_ok`, `media{state,duration_ms,cursor_ms}` |
| `obs.connect` | `{host?,port?,password?}` | Trạng thái OBS |
| `obs.disconnect` | — | Trạng thái OBS |
| `playback.status` / `.pause` / `.resume` / `.stop` / `.next` / `.previous` | — | `PlaybackStatus` |
| `playback.play` | `{media_id}` | `PlaybackStatus` (`PLAYBACK_CONFLICT` nếu đang phát: dừng trước) |
| `playback.playlist` | `{media_ids[1..300], loop?}` | `PlaybackStatus` |
| `playback.loop` | `{loop}` | `PlaybackStatus` (phiên sản phẩm luôn lặp) |

`PlaybackStatus` = `PlaybackEngine.status()` (đúng dữ liệu gửi lên Cloud: `running`, `queue_ids`, `session_mode`, `product_id`,
`pending_product_id`, `obs{…, has_password}`…) cộng các trường cho GUI: `active`, `media_ids`, `current_media_id`,
`current_filename`, `last_error`, `started_at`, `needs_manual_resume`, `source`.
| `monitor.status` / `.start` / `.stop` | — | `{state, config, events, templates}` |
| `monitor.config` | `{interval_seconds?,threshold?,telegram_enabled?,telegram_chat_id?,autostart?}` | Cấu hình monitor |
| `monitor.templates.list` | — | Ảnh mẫu đang có (chỉ đọc: ảnh do SUPER_ADMIN cung cấp qua Cloud, GUI không thêm/xoá) |
| `voice.speak` | `{text≤450, voice?}` | `{status:"sent_to_obs",source,voice,chars}` |
| `diagnostics.export` | — | `{path, folder, bytes}` |
| `logs.recent` | `{limit?≤500}` | `{entries[]}` |

## 3. Events (Core → GUI)

| Event | Khi nào | `data` |
|---|---|---|
| `core.ready` | Core sẵn sàng (luôn đầu tiên) | `{version, ipc_protocol, pid, methods, status}` |
| `core.error` | Lỗi khởi tạo / Core trùng | `{code, message, fatal}` |
| `core.stopped` | Trước khi thoát | `{reason}` |
| `agent.status_changed` | Đổi trạng thái Agent / kích hoạt | `AgentStatus` |
| `cloud.connection_changed` | connecting/connected/disconnected/rejected/stopped | `CloudStatus` |
| `media.scan_progress` | ~0,4 s/lần khi quét | `{job_id, scanned_files, found, folder}` |
| `media.scan_finished` | Quét xong/huỷ/lỗi | `{job_id, state, total, added, removed, duration_ms, errors}` |
| `media.sync_finished` | Đồng bộ metadata lên Cloud xong | `{synced, at}` |
| `obs.status_changed` | Kết nối OBS đổi | `ObsStatus` |
| `playback.changed` | Bất kỳ thay đổi playlist | `PlaybackStatus` |
| `monitor.status_changed` | Đổi trạng thái giám sát | `MonitorStatus` |
| `monitor.captcha_detected` / `monitor.captcha_cleared` | 2 khung liên tiếp vượt/dưới ngưỡng | `{at, score, template, image_path, window_title}` / `{at}` |
| `command.received` / `command.finished` | Lệnh Cloud nhận / xong | `{command_id, kind, status, duration_ms?, error?}` |
| `log.entry` | Log INFO+ | `{ts, level, logger, code, correlation_id, message}` |

## 4. Mã lỗi

| Nhóm | Mã |
|---|---|
| IPC | `INVALID_JSON`, `INVALID_FRAME`, `INVALID_REQUEST_ID`, `INVALID_PARAMS`, `METHOD_NOT_FOUND`, `UNSUPPORTED_VERSION`, `FRAME_TOO_LARGE`, `INTERNAL_ERROR` |
| Rust shell | `CORE_NOT_READY`, `CORE_TIMEOUT`, `CORE_EXITED`, `CORE_IO_ERROR`, `CORE_CRASHED`, `CORE_KILLED`, `CORE_ALREADY_RUNNING` |
| Kích hoạt | `ACTIVATION_KEY_INVALID`, `ACTIVATION_KEY_USED`, `LICENSE_EXPIRED`, `AGENT_LIMIT_REACHED`, `ALREADY_ACTIVATED`, `ACTIVATION_IN_PROGRESS`, `DPAPI_FAILED`, `CREDENTIAL_CORRUPT` |
| Cloud | `CLOUD_URL_MISSING`, `CLOUD_URL_MISMATCH`, `CLOUD_UNREACHABLE`, `CLOUD_TIMEOUT`, `CLOUD_SERVER_ERROR`, `CLOUD_BAD_RESPONSE`, `CLOUD_VALIDATION_ERROR`, `AGENT_UNAUTHORIZED`, `AGENT_REVOKED`, `AGENT_NOT_ACTIVATED`, `AGENT_RUNNING`, `AGENT_ALREADY_RUNNING`, `AGENT_NOT_CONNECTED`, `ACK_REJECTED` |
| Media | `FOLDER_NOT_FOUND`, `FOLDER_TOO_BROAD`, `FOLDER_ALREADY_ADDED`, `FOLDER_NOT_IN_LIST`, `FOLDER_LIMIT`, `SCAN_IN_PROGRESS`, `INVALID_MEDIA_ID`, `MEDIA_NOT_INDEXED`, `MEDIA_FILE_MISSING`, `MEDIA_OUTSIDE_ALLOWED`, `MEDIA_NOT_ALLOWED` |
| OBS/Phát | `OBS_NOT_RUNNING`, `OBS_AUTH_FAILED`, `OBS_TIMEOUT`, `OBS_CONNECT_FAILED`, `OBS_DISCONNECTED`, `OBS_INPUT_MISSING`, `OBS_REQUEST_FAILED`, `OBS_NOT_APPLIED`, `OBS_UNAVAILABLE` (503), `PLAYBACK_CONFLICT` (409), `INVALID_REQUEST` (422) — `PlaybackError` giữ `status_code` của engine Cloud |
| Monitor/TTS | `MONITOR_UNSUPPORTED`, `MONITOR_DEPENDENCY_MISSING`, `TEMPLATE_MISSING`, `INVALID_TEMPLATE`, `TTS_BUSY`, `TTS_TIMEOUT`, `TTS_FAILED`, `TTS_UNAVAILABLE` |
| Lệnh Cloud | `UNSUPPORTED_COMMAND`, `INVALID_COMMAND_PAYLOAD` |

## 5. Cloud API dùng bởi Agent

Nguồn chuẩn: `backend/app/cloud_agents.py` (+ `identity.py`, `agent_library.py`). Base URL người dùng nhập, chuẩn hoá về
`https://<host>[/prefix]/api` (HTTP chỉ cho localhost). Agent xác thực bằng `X-Sarae-Agent-Key: <agent_credential>` +
`agent_id` đã đăng ký. Mã lỗi Agent suy ra từ HTTP status + endpoint; nếu Cloud gửi header `X-Sarae-Error` thì dùng mã đó.

| API | Actor | Ghi chú |
|---|---|---|
| `POST /api/identity/activate` `{activation_key, hostname}` | Agent (chưa có credential) | → `{agent_id, agent_credential, owner_id}`; 403 → `ACTIVATION_KEY_INVALID` (hoặc `LICENSE_EXPIRED`), 409 → `AGENT_LIMIT_REACHED` |
| `POST /api/agents/heartbeat` `{agent_id, hostname, verification, obs, local_playback, playback}` | Agent | `playback` = `PlaybackEngine.status()`; Cloud đối chiếu phiên LIVE với nó. 401 → `AGENT_UNAUTHORIZED`, 403 → `LICENSE_EXPIRED` (cả Agent bị thu hồi) — đều dừng worker |
| `GET /api/agents/commands/{agent_id}/next?wait=0..25` | Agent | Long-poll; `{command:{command_id, kind, payload, claim_token, attempt, expires_at} \| null}` |
| `POST /api/agents/commands/ack` `{agent_id, command_id, claim_token, success, result}` | Agent | `result` ≤ 65000 ký tự (JSON khi thành công, thông điệp lỗi khi thất bại); ACK lặp → `duplicate:true`; ACK trễ → `late:true`; 404/409 → `ACK_REJECTED` |
| `POST /api/agents/events` `{agent_id, events[≤200]:{seq, type, session_id, product_id, detail}}` | Agent | `type` ∈ `PRODUCT_SWITCHED`/`PLAYBACK_ENDED`/`ERROR`; `seq` trùng bị bỏ qua |
| `POST /api/agent-library/sync` `{agent_id, videos[≤10000]}` | Agent | `media_id, filename, size_bytes, modified_ns`, tuỳ chọn `duration_seconds`, `thumb_jpeg_base64` (JPEG ≤ 40 KB); trả `thumbs_missing` |
| `GET /api/agents/status` | OWNER/SUPER_ADMIN (Bearer) | Chỉ Agent thuộc quyền |
| `POST /api/agents/{agent_id}/commands` `{kind, payload, idempotency_key?, wait?}` | **Chỉ OWNER sở hữu** | `Idempotency-Key` trùng → cùng lệnh; 409 nếu Agent offline |
| `GET /api/agents/{agent_id}/commands[/{command_id}]`, `POST …/{command_id}/cancel` | OWNER | Trạng thái `PENDING/RUNNING/SUCCESS/FAILED/TIMEOUT/CANCELLED` |
| `GET /api/agent-library/{agent_id}` | OWNER/SUPER_ADMIN | Danh sách video + `duration_seconds`, `thumb_url` |
| `GET /api/captcha-templates/agent?agent_id=`, `GET /api/captcha-templates/agent/{id}/image?agent_id=` | Agent | Bộ ảnh mẫu CAPTCHA `{templates:[{id, sha256, bytes}]}`; Agent giữ đúng bộ này (tải khi kết nối Cloud, rồi 6 giờ/lần) |
| `GET/POST /api/captcha-templates`, `DELETE /{id}`, `GET /{id}/image` | **Chỉ SUPER_ADMIN** | Quản lý ảnh mẫu (PNG/JPEG ≤ 3 MB, tối đa 50); OWNER không truy cập được |

### Kiểu lệnh (`kind`) và payload

Lane `background`: `tts_speak`, `media_rescan`, `monitor_*`; còn lại lane `live`. Cloud chỉ giao lại (khi mất ACK) các kind
idempotent (`obs_status`, `obs_inputs`, `obs_configure`, `media_rescan`, `monitor_*`, pause/resume/stop/cancel_pending).

| kind | payload | Hành động trên Agent (`PlaybackEngine` trừ khi ghi khác) |
|---|---|---|
| `product_play` | `{session_id ≥1, product_id ≥1, media_ids[1..300], mode ∈ immediate/after_current}` | Bắt đầu phiên sản phẩm hoặc chuyển sản phẩm (ngay / sau video hiện tại → `PRODUCT_SWITCHED`) |
| `playback_cancel_pending` | `{}` | Bỏ sản phẩm đang chờ |
| `playback_pause` / `_resume` / `_next` / `_previous` / `_stop` | `{}` | Điều khiển phát |
| `playback_select` / `playback_seek` | `{index 0..9999}` / `{ms 0..86400000}` | Chọn video / tua (422 nếu quá thời lượng) |
| `local_playlist_start` / `local_media_play` | `{media_ids, loop}` / `{media_id}` | Phát playlist / 1 video (409 nếu đang phát) |
| `local_media_next` / `local_media_stop`, `obs_pause` / `obs_resume` / `obs_next` / `obs_previous` / `obs_stop` | `{}` | Tên cũ, giữ tương thích Web |
| `obs_status` | `{}` | `PlaybackEngine.status()` |
| `obs_configure` | `{host (localhost), port, password, input_name, dual_mode, source_a, source_b}` | Lưu vào `config.json` + mật khẩu DPAPI (rỗng = giữ); trả status, không bao giờ trả mật khẩu |
| `obs_inputs` | `{host, port, password}` | Danh sách input OBS + `in_scene` |
| `media_rescan` | `{}` | Quét lại thư mục + đồng bộ ngay → `{count, thumbs_sent, thumbs_pending}` |
| `tts_speak` | `{text 1..450, voice ∈ vi-VN-HoaiMyNeural/NamMinhNeural}` | Đọc qua nguồn OBS giọng nói |
| `monitor_start` / `monitor_stop` / `monitor_config` | `{}` / `{interval_seconds, threshold, telegram_enabled, telegram_chat_id}` | CAPTCHA Monitor |

Payload được kiểm tra **hai lần**: Cloud (`validate_payload`, 422) và Agent (`cloud/commands.py`, `INVALID_COMMAND_PAYLOAD`).
