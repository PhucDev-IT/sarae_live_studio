# Sarae TTS Backend (G-OmniVoice) — Thiết kế đã duyệt

> Trạng thái: **kiến trúc A đã duyệt** (2026-11-02). Phase 0–2 đã nghiệm thu; Phase 3 (tích hợp Sarae Backend) hoàn thành, chờ duyệt (mục 17). Phase 4 chưa bắt đầu.
> Ký hiệu: ✅ đã xác minh trong source/đo thực tế · 🔶 giả định, cần kiểm chứng.

## 1. Quyết định đã chốt

| # | Quyết định |
|---|---|
| D1 | **Phương án A**: Sarae Backend điều phối tập trung và gọi TTS Backend; Windows Agent chỉ tải audio (ưu tiên direct, relay qua Backend khi cần) và phát qua pipeline OBS sẵn có. |
| D2 | TTS Backend là service độc lập trong `voice-server/` (monorepo), Python env và lifecycle riêng, không import `backend/` hay `agent-core/`. |
| D3 | Voice profile gốc (WAV + transcript) do Sarae Backend quản lý; TTS Backend chỉ giữ dữ liệu dẫn xuất (bản sao WAV theo rev, `VoiceClonePrompt` trong RAM và `.pt`). |
| D4 | Fallback mặc định `none` ở mọi ngữ cảnh; chỉ đổi provider khi owner đã cấu hình. |
| D5 | Baseline chất lượng = `omnivoice-demo` chính thức, checkpoint `g-group-ai-lab/g-omnivoice`, tại `127.0.0.1:7862`. Không fine-tune, không thêm hậu xử lý. |
| D6 | Phase 0–1 giữ nguyên cơ chế chia đoạn long-form của thư viện OmniVoice. |
| D7 | Transcript tham chiếu mặc định do Whisper ASR sinh giống demo, lưu lại một lần; transcript thủ công là tùy chọn (mục 8.1). |

Phương án C (TTS bridge kết nối outbound tới Backend) được giữ làm dự phòng nếu không dùng được mạng riêng VPS ↔ PC GPU. API TTS Backend không đổi khi thêm bridge.

## 2. Hiện trạng đã xác minh (tóm tắt)

- ✅ Audio đang được sinh **trên Windows Agent**: `VoiceEngine` (`agent-core/src/sarae_agent/voice/tts.py`) có queue 4 câu, TTL, cache theo key, prepare thread, `clear()`, ducking; `ProviderRegistry` (`voice/providers.py`) có Edge và ElevenLabs.
- ✅ Backend gửi `tts_speak` / `tts_prepare` / `tts_clear` qua bảng `agent_commands`; Agent nhận bằng long-poll HTTPS (`cloud_agents.py`), lane `background`, Idempotency-Key, claim_token, journal phía Agent.
- ✅ `tts_speak` **không** nằm trong `RETRYABLE_KINDS`: hết lease thì thành TIMEOUT, không giao lại.
- ✅ Payload Agent dùng `extra="forbid"`: Agent cũ từ chối field mới. Agent chưa báo version/capabilities.
- ✅ Agent tính `deadline = clock_agent() + ttl_seconds` **tại lúc nhận lệnh** (`tts.py:183-184`), nên một lệnh giao trễ (Agent reconnect, `SLOW_TTL_SECONDS=180`) được TTL mới. Thiết kế dưới đây bổ sung hạn tuyệt đối.
- ✅ Đã có pattern Agent tải file qua Backend bằng `X-Sarae-Agent-Key` (`agent_media.py:157`) và trả secret cho Agent (`/api/agents/voice-key`).
- ✅ Không có fallback provider tự động (`docs/VOICE_ENGINE.md`).
- ✅ OmniVoice 0.2.1: output 24 kHz; `VoiceClonePrompt.save/load` dạng dict `format_version=1` không chứa thông tin checkpoint; sinh **không tất định** (Gumbel noise, không có seed); `generate` không có lock.
- ✅ Demo tạo prompt bằng `create_voice_clone_prompt(path, ref_text or None)` với preprocess mặc định; transcript trống → Whisper `large-v3-turbo` nhận dạng audio **đã** `remove_silence`; với clip < 20 s, khác biệt duy nhất giữa ASR và transcript thủ công là chuỗi `ref_text`.
- ✅ (người dùng xác nhận, 2026-11-02) Lỗi mất 1–2 từ đầu được giải quyết bằng cách bỏ phần thừa ở đầu và cuối WAV tham chiếu. Đo năng lượng 100 ms: `voice_ref.wav` (8.0 s) kết thúc ở −29.6 dBFS (đang có tiếng nói, bị cắt đột ngột); `voice_ref_pause_7s_test.wav` (7.54 s) cùng phần đầu nhưng phần cuối giảm dần về im lặng. QC tham chiếu (mục 8) phải phát hiện được trường hợp này.

## 3. Kiến trúc

```
┌──────────── Sarae Web ────────────┐
│ VoiceSettings / Voice Profiles    │──JWT──┐   Web không bao giờ gọi trực tiếp TTS
└───────────────────────────────────┘       ▼
┌──────────────────── Sarae Backend (localhost hoặc VPS) ──────────────────────────┐
│ ai_voice.py        cấu hình provider, chính sách fallback, preview (proxy)        │
│ voice_profiles.py  NGUỒN GỐC: WAV + transcript + rev                              │
│ tts_server.py      client TTS: health, sync voice, job, relay; bảng tts_jobs      │
│ live_reply_director / ai_live_batches ── tạo utterance + job ──▶ tts_speak        │
│ cloud_agents.py    command, voice-key(omnivoice), GET /api/agents/tts-audio/{job} │
└───────┬─────────────────────────────────────────────────────────┬─────────────────┘
        │ điều phối: HTTPS + token orchestrator                    │ long-poll (đã có)
        │ (localhost / LAN / Tailscale|WireGuard)                  ▼
┌───────▼──────── voice-server (PC GPU) ────────────────┐    ┌────── Windows Agent ───────────┐
│ auth → admission → PriorityQueue → 1 GPU worker        │    │ OmniVoiceProvider (mới)        │
│ OmniVoice fp16 cuda:0, preset demo_default             │◀───│  GET /v1/tts/jobs/{id}/audio   │
│ voice cache: RAM LRU + .pt + metadata (compat)         │WAV │  read token theo job           │
│ result cache theo nội dung · outputs (TTL)             │───▶│ VoiceEngine giữ nguyên: queue, │
└────────────────────────────────────────────────────────┘    │  cache, OBS, ducking, clear    │
                                                              └────────────────────────────────┘
```

## 4. Job lifecycle và tính nhất quán

### 4.1 Thực thể và khóa

| Thực thể | Nơi sống | Khóa | Vai trò |
|---|---|---|---|
| **Utterance** | Backend (bảng `tts_jobs`, cùng SQLite với `agent_commands`) | `utterance_id = {owner}:{session|none}:{reply_id|manual-uuid}:{part}` với `part ∈ {intro, body}` | Một lần quyết định "đọc câu này". Đơn vị chống phát trùng. |
| **TTS job** | voice-server (RAM) | `job_id`; Idempotency-Key = `utterance_id` | Một lần yêu cầu GPU. Kết quả có thể lấy từ result cache theo nội dung. |
| **Agent command** | Backend `agent_commands` | `command_id`; Idempotency-Key = `tts:{utterance_id of body}` | Lệnh phát. **Chỉ command mới gây ra phát**, job không bao giờ tự phát. |
| **LIVE session** | Backend | `session_id` | Tag của job và command; kết thúc session thì hủy cả hai. |

`tts_jobs(utterance_id PK, job_id, owner_id, agent_id, session_id, part, deadline_at, state, command_id, created_at, updated_at, error)` — Backend dùng bảng này để relay, hủy hàng loạt và đo latency.

### 4.2 Hạn tuyệt đối

- Backend đặt `deadline_at` (UTC, ISO-8601) = thời điểm có text + TTL của câu. Cùng một giá trị được gửi cho TTS job và trong payload `tts_speak` (field mới `deadline_at`).
- TTS: không bắt đầu job đã quá `deadline_at`; trả 410 `TTS_EXPIRED` khi đọc audio của job đã hết hạn.
- Agent: bỏ câu nếu `now_utc > deadline_at + skew` (skew mặc định 2 s), **ngoài** kiểm tra `ttl_seconds` hiện có. Như vậy lệnh giao trễ sau reconnect không được TTL mới.
- 🔶 Đồng hồ Agent có thể lệch: Agent dùng `server_time` có sẵn trong phản hồi long-poll/websocket để ước lượng lệch; nếu không có thì chỉ dùng TTL hiện tại.

### 4.3 Máy trạng thái

```
Utterance (Backend):
  NEW ─create job─▶ JOB_CREATED ─create command─▶ SENT ─Agent event─▶ PLAYING ─▶ PLAYED
   │                    │                           │                      └─▶ STOPPED (clear)
   │                    ├─command lỗi─▶ ABORTED (hủy job)                 
   │                    └─cancel─▶ CANCELLED      ├─Agent: expired ─▶ EXPIRED
   └─TTS lỗi/không ready─▶ FAILED (áp chính sách)  └─Agent: failed  ─▶ FAILED

TTS job (voice-server):
  QUEUED ─▶ RUNNING ─▶ DONE ─(hết TTL output)─▶ PURGED
     │         │  └─▶ FAILED
     ├─cancel─▶ CANCELLED (RUNNING: chạy xong rồi bỏ kết quả)
     └─quá deadline trước khi chạy─▶ EXPIRED
```

### 4.4 Thứ tự thao tác

1. Backend ghi utterance `NEW`.
2. `POST /v1/tts/jobs` với `Idempotency-Key = utterance_id` → `JOB_CREATED` (lưu `job_id`).
3. `create_command(tts_speak, {..., utterance_id, tts_job, intro_job?, deadline_at, read_tokens})` với Idempotency-Key cố định → `SENT`.
4. Agent claim, tải audio, phát, gửi sự kiện `tts_playback` (`playing` / `played` / `expired` / `stopped` / `failed`) qua `POST /api/agents/events` (đã có).

### 4.5 Các tình huống

| Tình huống | Xử lý | Kết quả |
|---|---|---|
| Tạo job thành công nhưng không tạo được command | Backend `DELETE /v1/tts/jobs/{id}` (best-effort), utterance → `ABORTED`, áp chính sách fallback của ngữ cảnh (mặc định `none`: ghi lỗi như hiện tại). Nếu hủy thất bại, job chạy xong và hết hạn vì không ai đọc. | Không phát. Audio có thể còn trong result cache để lần sau dùng lại. |
| Retry tạo job (mạng chập chờn) | Cùng Idempotency-Key → TTS trả lại cùng `job_id`. | Không sinh GPU hai lần. |
| Command bị retry hoặc duplicate | (a) Idempotency-Key của command → `create_command` trả command cũ ✅. (b) `tts_speak` không retryable → không giao lại sau lease ✅. (c) Journal Agent → không chạy lại cùng `command_id` ✅. (d) **Mới**: `VoiceEngine` nhớ `utterance_id` đã nhận trong 15 phút; trùng thì trả `duplicate`, không xếp hàng. | Một utterance phát tối đa một lần, kể cả khi có hai command khác nhau cho cùng utterance. |
| Người dùng tắt auto voice khi job đang chạy | Director ngừng tạo utterance mới; utterance `JOB_CREATED` chưa có command → hủy job, `CANCELLED`. Utterance đã `SENT` **giữ nguyên ngữ nghĩa hiện tại** (vẫn đọc nếu chưa quá hạn); muốn dừng ngay thì dùng **Dừng giọng nói** (`tts_clear`) như hiện nay, và lệnh này nay cũng hủy các job còn chờ. | Không thay đổi hành vi người dùng đang quen. 🔶 Cần bạn xác nhận ngữ nghĩa này. |
| LIVE kết thúc khi audio chưa sinh xong | Khi session kết thúc: `DELETE /v1/tts/jobs?tag=session:{id}` và `tts_clear` tới Agent của session. Agent nếu vẫn đang chờ audio sẽ nhận 410 → `EXPIRED`/`CANCELLED`. | Không phát sau khi LIVE kết thúc. 🔶 Chưa xác minh hiện nay kết thúc session có gửi `tts_clear` không; Phase 3 kiểm tra. |
| Agent reconnect sau khi job đã hết hạn | Command có thể còn PENDING (TTL 180 s) và được claim. Agent kiểm tra `deadline_at` → bỏ, sự kiện `expired`. Nếu chưa quá hạn mà job đã bị TTS purge → 410 → `expired`. | Không đọc câu đã mất giá trị. Không fallback cho câu hết hạn. |
| Audio đã tạo nhưng không được phát | Job `DONE` không được đọc trước `deadline_at` → TTS tính `generated_not_read`; Agent báo `expired`/`stopped`/`failed` → utterance cập nhật trạng thái. Output dọn theo TTL. | Có số liệu để điều chỉnh nhịp (đã sinh nhưng bỏ). |
| Agent tải audio thành công nhưng OBS lỗi | Agent báo `failed` với mã `OBS_*`; utterance `FAILED` ghi rõ **lỗi phát**, khác lỗi sinh `TTS_*`. | Phân biệt lỗi sinh và lỗi phát. |

### 4.6 Bất biến

1. Chỉ command `tts_speak` mới dẫn tới phát; job không có đường phát riêng.
2. Mỗi `utterance_id` có tối đa một command và Agent phát tối đa một lần.
3. Không thành phần nào tạo job mới để "thử lại" cho một utterance đã `SENT`.
4. Câu hết hạn không bao giờ được chuyển sang provider khác.

## 5. Bảo mật audio

### 5.1 Credential

| Credential | Ai giữ | Phạm vi | Thời hạn | Thu hồi |
|---|---|---|---|---|
| `orchestrator` token | Backend (mã hóa trong `credentials.py`) | Toàn bộ API quản lý và job | Dài hạn | Đổi token trong cấu hình TTS (hỗ trợ 2 token song song để xoay vòng) |
| `admin` token | Người vận hành | Benchmark, `/v1/status` chi tiết | Dài hạn | Như trên |
| **Read token theo job** | Agent (trong payload `tts_speak`) | `GET /v1/tts/jobs/{job_id}/audio` của **đúng một job** và **đúng một agent_id** | `exp = deadline_at + 60 s`, tối đa 10 phút | Hủy job (token còn hạn nhưng job trả 410); xoay khóa ký (`kid`) để vô hiệu tất cả |

Read token = `base64url(json{job_id, agent_id, exp, kid}) + "." + HMAC-SHA256(key[kid], payload)`, do voice-server phát hành khi tạo job (`reader_agent_id` trong request). Voice-server không cần lưu trạng thái token.

- Token không được log; Backend che field `read_tokens` trong `command_view`/kết quả command trả cho web.
- Agent gửi kèm `X-Sarae-Agent-Id`; voice-server từ chối khi không khớp `agent_id` trong token (chống dùng lại token giữa các máy).

### 5.2 Relay qua Backend

`GET /api/agents/tts-audio/{job_id}` với `X-Sarae-Agent-Key`:

1. `verify_agent(agent_id, key)` (hàm sẵn có).
2. Tra `tts_jobs` theo `job_id`: phải thuộc **cùng owner và cùng agent_id**, chưa `CANCELLED/ABORTED`, chưa quá `deadline_at`. Sai → 404 (không tiết lộ tồn tại).
3. URL upstream **luôn** dựng từ cấu hình TTS của owner + `job_id` đã kiểm tra regex `^[A-Za-z0-9_-]{8,64}$`. Không nhận URL, host hay path từ request; không follow redirect.
4. Gọi upstream bằng token orchestrator, stream về Agent với giới hạn kích thước (mặc định 20 MB) và `Content-Type: audio/wav` cố định.

### 5.3 Khác

- voice-server mặc định bind `127.0.0.1`; dùng LAN/VPN thì bind IP của interface riêng. Không mở port ra Internet công khai.
- Giới hạn: sync 450 ký tự; job 5.000 ký tự; WAV tham chiếu ≤ 5 MB, ≤ 30 s.
- Log chỉ ghi độ dài text, sha256 rút gọn, voice_id, thời gian; không ghi transcript, token, WAV.

## 6. GPU admission control

Một GPU worker duy nhất, không preempt. Priority queue không đủ vì job dài đang chạy vẫn chặn câu LIVE, nên thêm **admission** trước khi vào queue.

### 6.1 LIVE lease

- Backend gửi `PUT /v1/admission/live` `{owner_id, session_id, ttl_s: 60}` mỗi 30 s khi có ít nhất một session RUNNING dùng provider omnivoice; `DELETE` khi kết thúc.
- Lease tự hết hạn nếu Backend mất kết nối, nên GPU không bị khóa vĩnh viễn ở chế độ LIVE.

### 6.2 Chính sách nhận job

| Lớp job | Không có LIVE lease | Có LIVE lease |
|---|---|---|
| `live` (trả lời bình luận, ≤ 450 ký tự) | Nhận | Nhận, ưu tiên cao nhất |
| `preview` (Test Voice, ≤ 450 ký tự) | Nhận | Nhận nếu queue live trống, tối đa 1 đang chờ |
| `prepare` (cache trước, ≤ 450 ký tự/câu) | Nhận | Nhận nếu queue live trống và ước lượng audio ≤ 20 s; tối đa 1 đang chạy |
| `long` (đoạn dài) | Nhận | **Từ chối** `423 LIVE_ACTIVE` |
| `benchmark` (admin) | Nhận | **Từ chối** `423 LIVE_ACTIVE` |

- Ước lượng thời lượng audio từ số âm tiết (giống `RuleDurationEstimator` của thư viện) để chặn job lớn khi LIVE.
- Thời gian chờ tối đa của một câu live = thời gian còn lại của job đang chạy (bị chặn trên bởi giới hạn 20 s audio của prepare khi có lease). Con số thực tế đo ở Phase 2.
- Queue có giới hạn theo lớp: live 8, preview 2, prepare 50, long 5; đầy → 429 `TTS_BUSY` kèm `Retry-After`.

## 7. Direct / relay / fallback

### 7.1 Phân loại lỗi

| Mã | Lớp lỗi | Phát hiện ở | Ví dụ |
|---|---|---|---|
| `TTS_DIRECT_UNREACHABLE` | Mạng Agent ↔ TTS | Agent | connect refused / timeout / DNS khi gọi direct |
| `TTS_BACKEND_UNREACHABLE` | Mạng Backend ↔ TTS | Backend | tạo job lỗi mạng; relay không tới upstream |
| `TTS_NOT_READY` | TTS đang loading/degraded | Backend (health) hoặc 503 | model chưa load xong |
| `TTS_BUSY` | Backpressure | TTS 429 / 423 | queue đầy, LIVE lease |
| `TTS_INFERENCE_FAILED` | Lỗi sinh audio | TTS (job FAILED) | exception, `TTS_OOM` |
| `TTS_TIMEOUT` | Chờ quá giới hạn nhưng câu **chưa** hết hạn | Agent | job còn RUNNING khi hết thời gian chờ một lượt |
| `TTS_EXPIRED` / `TTS_CANCELLED` | Câu hết giá trị / bị hủy | TTS 410 hoặc Agent kiểm `deadline_at` | |
| `OBS_*` | Lỗi phát | Agent | Media Source không tồn tại |

### 7.2 Quy tắc

1. `audio_path = direct | relay | auto` (cấu hình Agent, mặc định `auto`).
2. `auto`: gọi direct; **chỉ** khi gặp `TTS_DIRECT_UNREACHABLE` mới chuyển sang relay. 4xx/410/lỗi inference từ direct không chuyển relay (relay sẽ cho cùng kết quả).
3. Relay chỉ được dùng khi Backend còn tới được kết quả: Backend kiểm tra trạng thái job ở upstream trước khi stream; không tới được → trả `502 TTS_BACKEND_UNREACHABLE` cho Agent.
4. Agent chờ audio theo vòng `wait ≤ 20 s`, dừng ở `deadline_at`. Quá hạn → `expired`, không fallback.
5. Fallback provider chỉ áp dụng khi chính sách của ngữ cảnh khác `none` **và** lỗi thuộc `TTS_*_UNREACHABLE`, `TTS_NOT_READY`, `TTS_BUSY`, `TTS_INFERENCE_FAILED`. Không bao giờ cho `TTS_EXPIRED`, `TTS_CANCELLED`, `OBS_*`.
6. Chính sách được gửi trong payload (`fallback: {mode, provider spec}`) để Agent áp dụng ngay, không phải hỏi lại Backend khi câu sắp hết hạn. Mỗi lần dùng fallback ghi `fallback_used` vào sự kiện và heartbeat.

```
voice.fallback = {
  live_reply: "none" | "comment_only" | "provider" | "hold_for_operator",   # mặc định none
  prepare:    "none" | "provider",                                           # mặc định none
  manual:     "none" | "provider",                                           # mặc định none
  provider:   { provider: "edge", voice: "vi-VN-HoaiMyNeural", rate: 5 }
}
```

`comment_only` và `hold_for_operator` do Backend xử lý trước khi gửi (không cần Agent).

## 8. Chất lượng audio và QC tham chiếu

- Preset `demo_default` = `OmniVoiceGenerationConfig()` mặc định (`num_step=32, guidance_scale=2.0, t_shift=0.1, denoise=True, preprocess_prompt=True, postprocess_output=True, audio_chunk_duration=15, audio_chunk_threshold=30`), `language=None`, không truyền `speed`/`duration`, model fp16 trên `cuda:0`, text chỉ `strip()`. Output `(audio*32767).astype(int16)` 24 kHz như demo.
- Mọi output lưu metadata: text, voice rev, `ref_text` thực tế, generation config đầy đủ, checkpoint revision, phiên bản thư viện, timings.
- Không resample, không chuyển MP3, không EQ/denoise ngoài. Agent lưu `.wav`.
- **QC tham chiếu (chỉ báo cáo, không sửa WAV):** thời lượng (khuyên 3–10 s, từ chối ngoài 1–30 s), sample rate, peak/clipping, năng lượng 100 ms đầu và **100 ms cuối** (cảnh báo "có thể bị cắt" khi phần cuối > −40 dBFS — đúng trường hợp `voice_ref.wav`), độ dài im lặng/nhiễu đầu, khoảng nghỉ dài, RMS.
### 8.1 Transcript tham chiếu (người dùng chốt 2026-11-02)

- **Mặc định ASR giống demo gốc**: người dùng chỉ upload WAV. Đăng ký voice gọi `create_voice_clone_prompt(ref_wav, ref_text=None)` — đúng đường demo khi để trống Reference Text — nên Whisper `large-v3-turbo` nhận dạng audio đã qua `remove_silence`.
- Lưu cả hai: `asr_text` (chuỗi Whisper thô) và `prompt.ref_text` (sau `add_punctuation`), cùng `.pt` của prompt vừa tạo. **Không chạy lại ASR cho request TTS.**
- Transcript thủ công là **tùy chọn** (xem và sửa trong Voice Settings). Sửa transcript → đổi `rev` → invalidate prompt cache → tạo lại bằng `create_voice_clone_prompt(ref_wav, ref_text=<transcript mới>)`.
- Khi phải mã hóa lại một voice ASR (đổi checkpoint/thư viện), dùng `asr_text` đã lưu làm `ref_text`, không gọi lại Whisper. Với clip ≤ 20 s, đường này chỉ khác đường demo ở chỗ bỏ qua `trim_long_audio` (vốn chỉ chạy với clip > 20 s), nên tương đương; Phase 0 kiểm chứng bằng nhánh S-ASRTXT. Vì vậy WAV tham chiếu > 20 s bị từ chối.
- ASR lỗi hoặc trả chuỗi rỗng → đăng ký thất bại với mã `VOICE_ASR_FAILED`, giao diện cho nhập transcript thủ công.
- Không mặc định transcript thủ công tốt hơn ASR khi chưa có benchmark; nhánh Manual của Phase 0 chạy sau khi có transcript đã kiểm tra.
- 🔶 Whisper tốn thêm VRAM khi nạp; voice-server nạp lười khi đăng ký voice và có thể giải phóng sau khi xong (đo ở Phase 0/1).

## 9. End-to-end latency

Không cam kết con số trước khi đo. Mốc thời gian, mỗi mốc đo trên đồng hồ của thành phần ghi nó:

| Mốc | Thành phần | Ý nghĩa |
|---|---|---|
| `t0` | Backend | Director có text cuối cùng cho câu (sau kiểm tra an toàn) |
| `t1` | Backend | Nhận phản hồi tạo job |
| `t2` | Backend | Command được tạo |
| `t3` | Backend | Agent claim command (thời điểm Backend trả command) |
| `q`, `inf` | voice-server | `queue_ms` (vào queue → bắt đầu GPU), `inference_ms` (gồm nạp prompt từ RAM/đĩa, ghi riêng `prompt_ms`) |
| `a1` | Agent | Bắt đầu tải audio (tính từ lúc claim) |
| `a2` | Agent | Tải xong (bytes, `path=direct|relay`, `cache_hit`) |
| `a3` | Agent | Gọi `play_file` |
| `a4` | Agent | Lần đầu thấy OBS `PLAYING` |

Phân tách báo cáo:

| Thành phần | Công thức |
|---|---|
| Backend orchestration | `t2 − t0` (trong đó tạo job `t1 − t0`) |
| Giao lệnh | `t3 − t2` |
| GPU queue | `queue_ms` |
| Inference | `inference_ms` |
| Chờ audio + truyền mạng | `a2 − a1` trừ phần chồng với `queue_ms + inference_ms` → báo cáo riêng `transfer_ms` (từ header `X-Ready-At` tới lúc nhận đủ byte, đo trên Agent bằng thời gian tải thuần) |
| Khởi động phát | `a4 − a3` |
| **E2E** | `(t3 − t0)` theo đồng hồ Backend + `(a4 − claim)` theo đồng hồ Agent |

Agent gửi `a1…a4` (tương đối so với claim) trong sự kiện `tts_playback`; Backend ghép theo `utterance_id`. Không trừ trực tiếp giữa hai đồng hồ khác máy. Báo cáo p50/p95 theo lớp câu (cache hit / miss, direct / relay).

## 10. API contract voice-server (tóm tắt)

| Method | Endpoint | Scope |
|---|---|---|
| GET | `/health/live` | không |
| GET | `/health/ready` | orchestrator |
| GET | `/v1/status` | orchestrator |
| PUT / GET / DELETE | `/v1/voices/{voice_id}` (PUT idempotent theo `rev`) | orchestrator |
| POST | `/v1/voices/{voice_id}/warmup` | orchestrator |
| POST | `/v1/tts/synthesize` (sync ≤ 450 ký tự, preview) | orchestrator |
| POST | `/v1/tts/jobs` (`Idempotency-Key`, `priority`, `deadline_at`, `reader_agent_id`, `tags`) → `{job_id, read_token, state}` | orchestrator |
| GET | `/v1/tts/jobs/{id}` | orchestrator |
| GET | `/v1/tts/jobs/{id}/audio?wait=0..20` | read token |
| DELETE | `/v1/tts/jobs/{id}` · `/v1/tts/jobs?tag=…` | orchestrator |
| PUT / DELETE | `/v1/admission/live` | orchestrator |
| GET | `/metrics` | orchestrator |

Mã lỗi: xem mục 7.1, cùng `VOICE_NOT_FOUND` 404, `VOICE_STALE` 409, `LIVE_ACTIVE` 423, `INVALID_PARAMS` 422, `TTS_AUTH` 401/403.

## 11. File dự kiến tác động

| Thành phần | File | Phase |
|---|---|---|
| Mới | `voice-server/` (bench, engine, voices, queue, jobs, auth, api, tests, scripts) | 0–2 |
| Backend | `app/tts_server.py` (mới), `app/voice_profiles.py` (mới) + migration, `app/ai_voice.py`, `app/cloud_agents.py`, `app/live_reply_director.py`, `app/ai_live_batches.py`, `app/live_sessions.py` | 3 |
| Agent | `voice/providers.py`, `voice/tts.py` (deadline tuyệt đối, utterance dedup, job ref, cache `.wav`), `cloud/commands.py`, heartbeat/capabilities, `config.py` | 4 |
| Web | `VoiceSettings.tsx`, `VoiceProfiles.tsx` (mới), `LiveSessions.tsx` | 5 |
| Docs | `docs/VOICE_ENGINE.md`, tài liệu này | 0–6 |

Không thay đổi: Edge/ElevenLabs provider, playback OBS, ducking, timeline, ghim sản phẩm, OBS scene.

## 12. Phase

| Phase | Mục tiêu | Acceptance criteria |
|---|---|---|
| 0 Baseline | Script độc lập tái hiện demo 7862 (D-ASR, S-ASR, S-ASRTXT, R-CUT; Manual khi có transcript đã kiểm tra); đo load, prompt, inference, RTF, VRAM; kiểm tra mất từ đầu | Nhánh thư viện tương đương demo; S-ASRTXT trùng S-ASR (đường "ASR một lần, lưu lại"); có bảng số liệu; xác định file tạm Gradio có giống file gốc |
| 1 TTS Backend độc lập | Engine, voice + prompt cache (`compat`), `/synthesize`, health, auth, 1 worker | Output qua API tương đương Phase 0; restart dùng lại `.pt`; invalidate đúng |
| 2 Queue/Job/Metrics/Vận hành | Jobs, admission + LIVE lease, deadline, cancel, idempotency, read token, result cache, `/metrics`, Windows service | Không sinh trùng; job quá hạn không chạy; job long bị từ chối khi có lease; tự phục hồi sau kill |
| 2b Nghiên cứu long-form | So sánh chia đoạn nội bộ với chia theo job | Benchmark nghe mù; không đổi mặc định |
| 3 Sarae Backend | Bảng `tts_jobs`, utterance lifecycle, voice profiles, preview proxy, relay, chính sách fallback, readiness + warmup ở bước Đồng bộ | Test Edge/ElevenLabs cũ pass; các tình huống mục 4.5 có test |
| 4 Windows Agent | Provider, `deadline_at`, utterance dedup, read token, direct/relay, sự kiện latency | Phát WAV trên OBS preview; không phát trùng; Agent cũ được báo rõ |
| 5 Web | Provider, profile + QC, chính sách fallback, readiness | Thao tác đủ mà không mở LIVE |
| 6 E2E | Toàn tuyến, đo latency mục 9 | Có p50/p95 thực đo; các lỗi mục 7 xử lý đúng |

## 13. Phase 0 — cách chạy

Xem `voice-server/README.md`.

## 14. Kết quả Phase 0 (run `p0_20261102_2251`, RTX 3060 12 GB)

Môi trường: Python 3.12.16, torch 2.8.0+cu128, omnivoice 0.2.1, transformers 5.14.1, checkpoint `f2b73211…`,
Whisper `large-v3-turbo` `41f01f3f…`. Golden set 15 câu × 3 lần + 1 đoạn dài (50 s). Tham chiếu chính
`voice_ref_pause_7s_test.wav` (sha256 `25b48ad4…`), tham chiếu bị cắt `voice_ref.wav` (`416d4f8d…`).

| Kiểm tra | Kết quả |
|---|---|
| Demo nhận đúng file gốc | ✅ Gradio chuyển nguyên byte (sha256 khớp) |
| `.pt` lưu → nạp lại | ✅ token và text trùng; audio cùng seed trùng từng mẫu; nạp 8,5 ms; file ≈ 12,7 KB |
| "ASR một lần, lưu transcript, mã hóa lại" (S-ASRTXT) | ✅ token và `ref_text` trùng S-ASR; 15/15 file trùng từng mẫu (Δ = 0) |
| Cùng seed chạy lại | ✅ trùng từng mẫu (thư viện tất định khi đặt seed; demo không đặt seed) |
| Thư viện (S-ASR) so với demo (D-ASR), 45 câu ngắn | Thời lượng trung bình 3,302 s và 3,300 s; tốc độ 5,02 và 5,05 từ/giây; Whisper không nghe thấy từ đầu: 8/45 và 7/45; **xóa hẳn từ đầu: 0/45 và 0/45**; không clip; đuôi là 100 ms số 0 của thư viện (không click) |
| Đoạn dài 50 s | Hai nhánh cùng thời lượng 50,14 s (độ dài do bộ ước lượng của thư viện quyết định); thư viện tự chia đoạn ở cả hai |
| Tham chiếu bị cắt đuôi (R-CUT) | Không nghe thấy từ đầu **24/45**, xóa hẳn **7/45**, 2 lần Whisper ra câu không liên quan → tái hiện lỗi mất từ đầu. QC gắn cờ `end_may_be_cut` cho `voice_ref.wav` (100 ms cuối −29,6 dBFS) và không gắn cho bản đã sửa |

Lưu ý khi đọc số liệu:
- "Không nghe thấy từ đầu" theo Whisper gồm nhiều lỗi chính tả của ASR ("shop" → "sốt/xốp", "Váy" → "Báy"); chỉ số so sánh tương đối giữa các nhánh, phải nghe mù để kết luận (`listening/sheet.csv`).
- Một file D-ASR (s03 r2) bị Whisper "ảo giác" chuỗi lặp dài, kéo WER trung bình của D-ASR lên 0,64; mức âm và thời lượng file bình thường 🔶 cần nghe lại.
- Câu có chữ số thô ("259.000 đồng", "200k") được đọc không ổn định ("259 ngàn/nghìn…"); Sarae vẫn phải gửi dạng `spoken()`.
- Kiểm tra lại bằng ASR thứ hai (`bench/recheck_asr.py`: faster-whisper medium, timestamp từng từ, độ giống khi bỏ dấu): ở S-ASR, S-ASRTXT và D-ASR **không có trường hợp nào mất từ đầu thật** (mọi lần bị gắn cờ đều là ASR đọc sai chính tả: shop → sốt/xốp/xúc, Váy → Báy). Các cờ "mất từ cuối" còn lại (S-ASR 3, D-ASR 4) đều rơi vào đuôi "size S, M, L nha": cả hai ASR đều nghe thấy "nha" nhưng gộp ba chữ cái thành "SML". Đây là cách ASR tách từ, không phải TTS mất chữ, và xuất hiện ở cả thư viện lẫn demo. Ở R-CUT còn 6 cờ `tts_suspect` và 10 cờ `listen` ở đầu câu (bản sao nằm trong `recheck/`).
- **Số liệu thời gian của run p0 không hợp lệ** (đã đo lại ở 14.1): hai demo (7862 và 7863) giữ khoảng 6,5 GB VRAM cùng lúc, nên tổng nhu cầu vượt 12 GB và WDDM đẩy bộ nhớ ra RAM. Thời gian sinh bị chia hai cụm 2–2,5 s và 13–15 s cho cùng loại câu; đoạn dài mất 280 s (demo chạy riêng chỉ mất 16 s). Cần đo lại khi chỉ còn một tiến trình dùng GPU.
- Số đo tham khảo (cùng điều kiện tranh chấp): load model 2,2 s (cache HF và cache đĩa nóng), load Whisper 1,6 s, tạo prompt có ASR 2,4 s, không ASR 0,6 s; VRAM cấp phát sau load 1,89 GiB, thêm Whisper 3,40 GiB, đỉnh khi sinh 3,62 GiB (reserved tới 5,9 GiB); demo mỗi câu ngắn khoảng 3,8 s tính cả ASR, prompt và HTTP.

### 14.1 Hiệu năng khi GPU không tranh chấp (run `perf_20261102_2336`)

Chỉ một tiến trình G-OmniVoice (trước khi chạy GPU dùng 640 MiB cho desktop); checkpoint, `demo_config`, WAV và đường ASR giữ nguyên như baseline chất lượng. Các ứng dụng desktop khác vẫn dùng khoảng 30–40 % GPU khi rảnh. Lúc tải nặng, GPU ở 100 %, P2, 1897 MHz, 72 °C.

| Nhóm | Ký tự (median) | Audio s (median) | Inference s median / p95 | RTF median | Đỉnh cấp phát / reserved GiB |
|---|---|---|---|---|---|
| live_short (n=24) | 34 | 2,07 | **1,87 / 1,95** | 0,89 | 2,00 / 4,13 |
| live_typical (n=24) | 93 | 5,06 | **2,25 / 2,43** | 0,45 | 2,04 / 4,13 |
| live_max (n=9) | 347 | 18,4 | 4,62 / 5,06 | 0,25 | 2,18 / 4,48 |
| para_1min (n=3) | 954 | 50,1 | 14,3 / 14,4 | 0,29 | 2,12 / 4,48 |
| long_3min (n=2) | 2167 | 114,0 | 34,9 / 35,3 | 0,31 | 2,12 / 4,48 |

| Khởi động và prompt | Kết quả |
|---|---|
| Tiến trình mới (n=3, checkpoint đã cache, cache đĩa nóng) | import 9,8 s + `from_pretrained` 1,9 s + câu đầu 1,9 s; tổng khoảng 16,9 s tính cả tạo prompt. 🔶 Chưa đo cold boot sau khi khởi động lại máy |
| VRAM model | 1,90 GiB cấp phát; thêm Whisper lên 3,40 GiB; giải phóng Whisper về 1,90 GiB |
| Prompt từ WAV có ASR | Nạp Whisper lần đầu 2,2 s; mỗi lần tạo prompt median 0,78 s, p95 1,21 s |
| Prompt từ transcript đã lưu | median 0,075 s |
| Nạp `.pt` | dưới 1 ms (12,8 KB) |
| Đóng gói WAV trong RAM (phần API thêm vào) | 0,16 ms (câu ngắn) đến 6,6 ms (2 phút). Chưa có endpoint nên chưa đo HTTP |
| Soak 200 request liên tiếp, một prompt | Inference median 2,01 s, p95 2,62 s; bộ nhớ cấp phát sau mỗi request luôn 1,8997 GiB (không rò rỉ); RSS 2,49 → 2,48 GiB; RTF median 10 % đầu 0,76, 10 % cuối 0,67; probe cùng seed ở đầu và cuối **trùng từng mẫu** (không mang trạng thái giữa các request) |

Nhận định cho LIVE:
- Có một chi phí sàn khoảng 1,8 s mỗi lần sinh (32 step × CFG), gần như không phụ thuộc độ dài câu. Câu LIVE điển hình (5 s audio) mất khoảng 2,2–2,4 s GPU, tức GPU sinh nhanh hơn tốc độ phát khoảng 2 lần.
- Câu có `intro` (tên người xem) cần hai lần sinh: thêm khoảng 1,8 s nếu phần thân chưa có trong cache. Phase 3 cần tính chi phí này khi quyết định cách tách intro.
- Chuẩn bị 40 câu trước LIVE mất khoảng 40 × 2,2 s ≈ 90 s GPU.
- Job dài chiếm GPU 14 s (1 phút audio) đến 35 s (2 phút audio). Điều này xác nhận cần admission control khi LIVE; giới hạn 20 s audio cho prepare tương ứng khoảng 5 s chặn GPU.
- Chưa đo: độ trễ end-to-end (Backend → Agent → OBS) và HTTP. Phần này đo ở Phase 1/6, chưa có cam kết latency.

## 15. Phase 1 — service độc lập (`voice-server/src/sarae_voice`)

### 15.1 Cấu trúc

| Module | Vai trò |
|---|---|
| `config.py` | `Settings` từ biến môi trường `SARAE_VOICE_*` / `voice-server/.env`; checkpoint, revision và version thư viện được pin theo Phase 0 |
| `engine.py` | `OmniVoiceEngine`: load một lần (`snapshot_download(revision=f2b73211…)` → `from_pretrained(..., float16)`), `create_voice_clone_prompt` (Whisper khi không có transcript), `generate` với preset `demo_default`; `FakeEngine` cùng interface cho test |
| `gate.py` | `GpuGate`: một thread GPU duy nhất cho mọi lời gọi model; giới hạn số lời gọi đang chờ (429); không bắt đầu sau `queue_timeout_s` thì rút ra (503); event loop chỉ `await` |
| `voices.py` | `VoiceStore`: `ref.wav` giữ nguyên byte, `profile.json`, `prompts/<compat>.pt + .json`, LRU trong RAM, `rev` và `compat` như mục 4 và 8.1 |
| `api.py`, `schemas.py` | FastAPI, Pydantic, Bearer token theo scope, lỗi dạng `{"error": {"code", "message"}}`, OpenAPI tại `/docs` |
| `__main__.py` | `python -m sarae_voice`: uvicorn với app object, nên chỉ chạy một tiến trình |

### 15.2 API contract thực tế

| Method | Path | Scope | Thành công | Lỗi chính |
|---|---|---|---|---|
| GET | `/health/live` | — | 200 `{"status":"alive"}` | — |
| GET | `/health/ready` | — | 200 `{"state":"ready","error":null,"busy","pending"}` | 503 với `state` `loading`/`error` và `error.code` (`TTS_GPU_UNAVAILABLE`, `TTS_VERSION_MISMATCH`, `TTS_LOAD_FAILED`, `TTS_GPU_FAILED`) |
| GET | `/v1/status` | orchestrator | 200: engine (checkpoint, revision, versions, preset, load_timings, GPU), memory, limits, gate, prompt_cache, synthesis p50/p95 | 401 |
| PUT | `/v1/voices/{voice_id}` | orchestrator | 201 tạo hoặc đổi, 200 không đổi (cùng WAV, cùng transcript); body là `VoiceProfile` | 401, 413 `PAYLOAD_TOO_LARGE`, 422 `INVALID_AUDIO`/`INVALID_PARAMS`/`VOICE_ASR_FAILED`, 429, 503 |
| GET | `/v1/voices` · `/v1/voices/{voice_id}` | orchestrator | 200 | 404 `VOICE_NOT_FOUND` |
| PUT | `/v1/voices/{voice_id}/transcript` | orchestrator | 200 `VoiceProfile` (`{"transcript": null}` quay về text Whisper đã lưu, không chạy lại Whisper) | 404, 422 |
| DELETE | `/v1/voices/{voice_id}` | orchestrator | 204 | 404 |
| POST | `/v1/voices/{voice_id}/warmup` | orchestrator | 200 `{"voice_id","rev","cache":"ram"\|"disk"\|"encoded","ms"}` | 404, 503 |
| POST | `/v1/tts/synthesize` | orchestrator | 200 `audio/wav` mono PCM16 24 kHz; header `X-Request-Id, X-Voice-Id, X-Voice-Rev, X-Preset, X-Sample-Rate, X-Audio-Ms, X-Queue-Ms, X-Inference-Ms, X-Prompt-Cache, X-Prompt-Ms, X-Server-Ms, X-Checkpoint-Revision, X-Trace` (compat của prompt, generation config, versions) | 404, 422 `TEXT_TOO_LONG`/`INVALID_PARAMS`, 429 `TTS_BUSY`, 503 `TTS_NOT_READY`/`TTS_QUEUE_TIMEOUT`/`TTS_GPU_FAILED`, 507 `TTS_OOM`, 500 `TTS_INFERENCE_FAILED` |

Text: Phase 1 gộp khoảng trắng (`" ".join(text.split())`); từ Phase 2 chỉ `text.strip()`, đúng như demo (mục 16.6). `seed` là tùy chọn và mặc định không đặt, giống demo; chỉ dùng cho test tái lập. Token admin được dùng mọi endpoint của orchestrator. Service không khởi động nếu chưa có token.

### 15.3 Kết quả đo (RTX 3060, chỉ một tiến trình CUDA)

| Hạng mục | Kết quả |
|---|---|
| Unit test (FakeEngine) | 45 pass: auth, loading/ready/error, voice và cache, idempotent, invalidate, restart, compat mới, `.pt` hỏng, validation, OOM/inference/CUDA, 50 request liên tiếp, đồng thời (max active = 1, event loop không bị chặn), 429, queue timeout, OpenAPI |
| GPU integration test | 13 pass: status đúng revision và version; transcript Whisper và số token **trùng Phase 0**; file `.pt` trùng prompt Phase 0; **6 câu qua API trùng từng mẫu với Phase 0**; đoạn 954 ký tự → 50,1 s audio trong 16,1 s; 30 request liên tiếp, VRAM cấp phát giữ 1,9 GiB; 3 request đồng thời chỉ có 1 lời gọi GPU cùng lúc (chờ 0 / 2,4 / 4,8 s); restart → warmup `disk` 9,5 ms, không mã hóa lại, audio trùng trước restart |
| Qua HTTP thật (`run_http.py`) | **45/45 câu golden trùng từng mẫu với Phase 0**; Whisper cho đúng kết quả của S-ASR (đã được kiểm tra lại ở mục 14) |
| Khởi động | Từ lúc spawn tiến trình đến khi ready: 18,1 s và 21,0 s (restart), trong đó import khoảng 16 s, load khoảng 3,6 s. Lần đầu trong ngày (cache đĩa còn lạnh) là 98 s 🔶 |
| Đăng ký voice (Whisper) | 4,46 s phía server (gồm nạp Whisper), qua HTTP 4,53 s |
| Prompt cache | RAM: 2,6 ms qua HTTP. Sau restart, đọc từ `.pt`: 14,5 ms server, 55 ms HTTP. Mã hóa lại: 0 lần |
| Thời gian qua HTTP (client) | golden median 2,42 s / p95 2,57 s; live_short 2,35 / 2,62 s; live_typical 2,31 / 2,47 s; live_max 4,70 / 5,15 s |
| Overhead HTTP (client − server) | median 2,4–2,8 ms (câu ngắn), live_max median 6 ms, p95 22 ms (WAV khoảng 900 KB) |
| VRAM | Model 1,90 GiB; đỉnh cấp phát 5,47 GiB (reserved 5,79) trong lúc đăng ký voice có Whisper; sau khi giải phóng Whisper về 1,90 GiB; đỉnh khi chỉ sinh audio 2,0–2,2 GiB |

Chênh lệch live_short (2,35 s qua HTTP so với 1,87 s ở 14.1): đã đo trong cùng một phiên, cùng 6 câu × 3 lần. Kết quả: thư viện trực tiếp 1,88 s, engine + GpuGate 1,88 s, FastAPI (TestClient) 1,88 s. **Service không làm chậm inference.** Con số của lần chạy HTTP là dao động trạng thái GPU trong lần chạy đó 🔶. Phase 2 cần đo lặp lại nhiều lần trước khi đặt ngưỡng admission.

### 15.4 Còn tồn tại

- Chưa có job, priority, LIVE lease, read token, relay, fallback, TTL dọn `data/outputs` (Phase 2+). `SARAE_VOICE_SAVE_OUTPUTS` mặc định tắt vì chưa có dọn dẹp.
- Import thư viện chiếm khoảng 16 s mỗi lần khởi động; lần đầu sau khi máy khởi động có thể tới khoảng 100 s. Chưa có Windows service hay tự restart (Phase 2).
- Lỗi CUDA nghiêm trọng đưa engine sang `error` và cần restart tiến trình; chưa tự phục hồi.
- Lời gọi GPU đã bắt đầu thì không hủy được: client ngắt kết nối thì kết quả bị bỏ, nhưng GPU vẫn chạy hết lượt đó.
- Giới hạn 450 ký tự cho endpoint đồng bộ; đoạn dài chỉ chạy được khi tăng `SARAE_VOICE_MAX_TEXT_CHARS`. Đoạn dài thuộc job của Phase 2.
- Một token cho mọi voice: chưa phân quyền theo owner ở phía voice-server (Sarae Backend kiểm tra owner ở Phase 3).

## 16. Phase 2 — job, scheduler, LIVE admission, bảo mật, vận hành

### 16.1 Module mới và module đã sửa

| Module | Vai trò |
|---|---|
| `gate.py` (mở rộng `GpuGate`) | Một worker thread GPU; priority `system < live < preview < prepare < long < benchmark`; mỗi lớp có queue giới hạn riêng (429); deadline (không bắt đầu sau hạn); hủy khi còn queued; cờ hủy khi đang chạy; `hold` để giữ long/benchmark khi đang LIVE; fair-share chống treo; `close()` + `join()` khi tắt; `run()` của Phase 1 giữ nguyên |
| `jobs.py` | Vòng đời job, snapshot voice/prompt/config, idempotency, result cache, read token, purge |
| `results.py` | Result cache riêng với prompt cache; khóa theo owner, voice, rev, compat, checkpoint, config, text, seed; TTL và giới hạn dung lượng; file của job chưa hết hạn được ghim, không bị xóa |
| `tokens.py` | Read token HMAC-SHA256 theo từng job |
| `admission.py` | LIVE lease có TTL, nhiều session cùng lúc |
| `metrics.py` | Registry Prometheus nhỏ, không cần thêm dependency |
| `instance.py` | Khóa file: mỗi data dir chỉ một tiến trình |
| `supervisor.py` | Khởi động lại khi crash hoặc lỗi CUDA, dừng êm qua stop-file |
| `api.py`, `schemas.py`, `config.py`, `__main__.py`, `voices.py`, `engine.py` | Endpoint mới, middleware đo HTTP, dọn dẹp định kỳ, graceful shutdown, `text.strip()`; `VoiceStore.peek` lấy prompt không cần GPU; FakeEngine có `block` để test tất định |

`max_pending` của Phase 1 được thay bằng giới hạn queue theo lớp (`SARAE_VOICE_QUEUE_*`). Endpoint đồng bộ thuộc lớp `preview`.

### 16.2 API Phase 2 (bổ sung vào 15.2)

| Method | Path | Auth | Thành công | Lỗi chính |
|---|---|---|---|---|
| POST | `/v1/tts/jobs` (header `Idempotency-Key` tùy chọn) | orchestrator | 202 `created: new` hoặc `result_cache`; 200 `created: replay`. Body: `JobView` + `read_token` + `read_token_expires_at` (chỉ trả trong response này) | 404 `VOICE_NOT_FOUND` (kể cả sai owner), 409 `IDEMPOTENCY_CONFLICT`, 422 `TEXT_TOO_LONG`/`DEADLINE_PASSED`/`INVALID_PARAMS`, 423 `LIVE_ACTIVE`, 429 `TTS_BUSY`, 503 |
| GET | `/v1/tts/jobs/{id}?wait=0..20` | orchestrator | 200 `JobView` (không bao giờ chứa token) | 404 `JOB_NOT_FOUND`, 410 `JOB_LOST` (job của lần chạy trước), 410 `JOB_GONE` (đã quá thời gian giữ) |
| GET | `/v1/tts/jobs/{id}/audio?wait=0..20` | read token + `X-Sarae-Agent-Id`, hoặc orchestrator | 200 `audio/wav` (header `X-Job-Id, X-Source, X-Audio-Ms, X-Queue-Ms, X-Inference-Ms`); 202 `JobView` khi đang queued/running | 401 `TTS_AUTH`/`TOKEN_EXPIRED`, 403 `TOKEN_SCOPE`, 409 `JOB_FAILED`, 410 `JOB_CANCELLED`/`JOB_EXPIRED`/`JOB_LOST`/`RESULT_GONE` |
| DELETE | `/v1/tts/jobs/{id}` | orchestrator | 200 `JobView` (queued → cancelled; running → `cancel_requested`, cancelled khi trả về; done → cancelled, mất quyền đọc) | 404/410 |
| DELETE | `/v1/tts/jobs?tag=session:123` | orchestrator | 200 `{"cancelled": n}` | 422 |
| PUT | `/v1/admission/live` `{owner_ref, session_id, ttl_s 5–300}` | orchestrator | 200 `{live_active, leases[], gpu{state,busy,running{class,elapsed_s,estimated_remaining_s},queued}, ready_for_live, reason}` | 422 |
| GET / DELETE | `/v1/admission/live` (`?owner_ref=&session_id=` khi DELETE) | orchestrator | như trên (+ `removed`) | — |
| GET | `/metrics` | orchestrator | Prometheus text | 401 |

`JobView`: `job_id, state, cancel_requested, voice_id, voice_rev, prompt_compat, preset, generation_config, priority, chars, seed, deadline_at, reader_agent_id, owner_ref, tags, created_at, started_at, finished_at, source (generated | result_cache), error, timings (queue_ms, inference_ms, prompt_cache, estimated_audio_s), audio (bytes, audio_ms, sample_rate, sha256), reads`.

Deadline mặc định khi không gửi: live 60 s, preview 60 s, prepare 15 phút, long và benchmark 1 giờ; tối đa `max_deadline_s` (1 giờ). Live, preview và prepare tối đa 450 ký tự; long và benchmark tối đa 5000.

### 16.3 Vòng đời job và scheduler

```
POST -> [admission] -> [owner + voice] -> fingerprint -> Idempotency-Key? -- trùng --> cùng job (replay) / khác --> 409
                                                   |-> result cache hit -> done (source=result_cache), không dùng GPU
                                                   |-> snapshot prompt (RAM/.pt) -> GpuGate.submit (429 nếu lớp đầy)
queued --(worker: ưu tiên tuyệt đối, FIFO trong lớp, fair turn)--> running --> done -> result cache (ghim)
  | quá deadline khi tới lượt -> expired (không bao giờ chạy)        | hủy -> chạy nốt, bỏ audio -> cancelled
  | DELETE -> cancelled (rút khỏi queue)                              | lỗi -> failed (TTS_OOM, TTS_INFERENCE_FAILED...)
  | lớp long/benchmark khi có lease LIVE -> giữ lại (không bắt đầu) cho tới khi hết lease hoặc hết deadline
```

- **Snapshot**: lúc tạo job, server chụp `voice_rev`, `prompt_compat`, generation config và chính `VoiceClonePrompt` (từ RAM hoặc `.pt`). Đổi voice khi job đang chờ không đổi audio của job đó (test `test_voice_change_while_queued_keeps_the_snapshot`). Nếu prompt phải mã hóa lại mà voice đã đổi rev thì job lỗi `VOICE_CHANGED`.
- **Fingerprint** (idempotency) gồm voice_id, rev, compat, text, preset, config, seed, owner_ref, reader_agent_id, priority. Deadline và tags không thuộc fingerprint, nên retry tính lại deadline vẫn là cùng job.
- **Fair-share** (thay cơ chế aging thử đầu tiên): ưu tiên tuyệt đối; một job lớp thấp đã chờ `starvation_s` (30 s) được 1 lượt sau mỗi `fair_every` (4) lượt lớp cao. Cơ chế aging ban đầu đẩy prepare lên ngang live sau 30 s, khiến live trong một đợt 30 job phải chờ tới 55 s; đã bị loại bỏ.
- **Restart**: job chỉ nằm trong RAM. `job_id` bắt đầu bằng boot id (6 hex), nên id của lần chạy trước trả 410 `JOB_LOST` và không bao giờ được sinh lại; read token cũ cũng hết hiệu lực vì khóa ký đổi theo tiến trình.
- **Dọn dẹp** (mỗi `cleanup_interval_s`): job đã kết thúc quá `job_retention_s` và đã qua deadline thì bị xóa (truy cập sau đó trả `JOB_GONE`; job `done` chưa ai đọc được tính vào `generated_not_consumed`); file result quá TTL hoặc vượt `result_max_bytes` (LRU, trừ file đang ghim); file `.tmp` mồ côi; output debug.

### 16.4 Bảo mật read token — mô hình thực tế

- Token = `base64url({j: job_id, a: reader_agent_id, exp, k: key_id}).base64url(HMAC-SHA256)`. Khóa ngẫu nhiên theo từng tiến trình (hoặc `SARAE_VOICE_READ_TOKEN_SECRET`). `exp = min(deadline + 60 s, now + 900 s)`.
- Mỗi lần đọc audio, server kiểm tra: chữ ký, hạn, `job_id` trong token khớp đường dẫn, `X-Sarae-Agent-Id` khớp `a`, rồi đến **trạng thái job**. Job bị hủy hoặc hết hạn thì trả 410 dù token còn hạn; đây là cách thu hồi.
- Token chỉ trả về trong response tạo job (gọi bằng token orchestrator). `GET` job, `/v1/status`, `/metrics` và log không chứa token (có test kiểm tra). Agent không bao giờ cần token orchestrator.
- **Giới hạn**: `X-Sarae-Agent-Id` là giá trị do client tự gửi, không chứng minh danh tính máy; nó chỉ ngăn dùng nhầm token giữa các agent. Năng lực thực sự nằm ở việc **giữ token**. Ai có token trong thời hạn thì đọc được đúng audio của job đó. Vì vậy:
  - Phase 3–4 phải chuyển token chỉ qua kênh command đã xác thực của Sarae (`X-Sarae-Agent-Key`, HTTPS) và che token khỏi command view trên Web.
  - Kết nối Agent → voice-server cần TLS hoặc mạng riêng (Tailscale/WireGuard); trên HTTP thuần trong LAN, token có thể bị nghe lén.
  - Muốn ràng buộc máy thật thì cần mTLS hoặc Backend relay (Agent xác thực bằng khóa Agent với Backend). Phase 2 chưa giải quyết điều này.
  - Một token orchestrator dùng chung cho mọi owner. Phân quyền owner ở voice-server chỉ dựa trên `owner_ref` do Backend gửi (Backend là bên được tin cậy).
- Result cache không làm lộ audio giữa các owner: khóa chứa `owner_ref` và voice; voice có `owner_ref` thì job khác owner nhận 404. Audio trong cache chỉ được đọc qua token của chính job mới.
- Giới hạn kích thước: WAV tham chiếu 5 MB; output tối đa 32 MB; text 450 hoặc 5000 ký tự. Relay qua Backend (Phase 3) không nhận URL từ client.

### 16.5 LIVE admission

- Có lease LIVE: job `long`/`benchmark` mới bị từ chối 423; job đã nằm trong queue thì bị giữ lại, không bắt đầu; `prepare` chỉ được nhận nếu ước tính audio ≤ 20 s.
- Lease không được gia hạn thì tự hết hạn. Có thể có nhiều lease từ nhiều owner và session.
- **Job dài đã chạy trước khi lease được tạo**: không preempt được. `PUT /v1/admission/live` trả `ready_for_live: false`, `reason: LONG_JOB_RUNNING`, kèm `running.estimated_remaining_s`. Con số này là ước tính theo độ dài text, **không phải cam kết**. Đo thực tế với job 114 s audio: ước tính còn 29,6–30,6 s, thực tế live phải chờ 31,5 s (benchmark) và 42,2 s (trong pytest). Ước tính có thể lệch khoảng 40 %.
- Hệ quả cho Phase 3: bước Đồng bộ trước LIVE phải gọi lease sớm, đọc `ready_for_live`, rồi hiển thị trạng thái "GPU bận, còn khoảng X s" và chờ hoặc cho operator quyết định. Không được bắt đầu LIVE với giả định GPU đã sẵn sàng.

### 16.6 Tương thích với Phase 1

- `demo_default`, checkpoint, generation config, Whisper mặc định, prompt cache RAM và `.pt`, restart: giữ nguyên. Toàn bộ 45 test Phase 1 vẫn pass; chỉ đổi tham số `max_pending` sang `queue_preview`.
- **Whitespace**: Phase 1 gộp khoảng trắng, còn demo dùng `text.strip()`. Phase 2 sửa cả endpoint đồng bộ lẫn job thành `text.strip()`, có test xác nhận engine nhận đúng chuỗi đã strip (giữ nguyên khoảng trắng kép và xuống dòng ở giữa). Kết quả đã đo của Phase 0/1 không bị ảnh hưởng, vì mọi câu golden và perf đều là một dòng, không có khoảng trắng kép. Lưu ý: thư viện xóa `\n` mà không chèn khoảng trắng, nên Sarae Backend nên gửi text một dòng (Agent hiện đã gộp khoảng trắng).
- Audio qua job trùng từng mẫu với Phase 0 (3 câu có seed, `test_gpu_jobs.py`), giống như endpoint đồng bộ (45/45 ở Phase 1).

### 16.7 Kết quả kiểm thử

| Bộ test | Kết quả |
|---|---|
| Unit (FakeEngine) | **78 pass** (45 Phase 1 + 33 Phase 2): lifecycle; read token (sai job, sai agent, thiếu header, chữ ký giả, hết hạn, job bị hủy, không xuất hiện trong status/metrics); idempotency replay và 409; result cache (hit, khác seed, khác checkpoint, cô lập owner); thứ tự priority; fair turn; job lớp thấp mới tạo không chen trước live; queue đầy theo lớp; 36 job đồng thời (một lane, đúng priority, FIFO trong lớp); hủy khi queued, running, done; hủy theo tag; deadline; snapshot voice; job dài trước LIVE và job long bị giữ; nhiều lease và lease hết hạn; OOM; giới hạn output; JOB_LOST sau restart; dọn job và result (JOB_GONE, `generated_not_consumed`); giới hạn dung lượng cache; metrics; graceful shutdown; stop-file và CUDA failure yêu cầu thoát; instance lock |
| Process (FakeEngine, `SARAE_VOICE_PROCESS_TESTS=1`) | **1 pass**: supervisor khởi động lại sau khi tiến trình con bị kill (PID mới); instance thứ hai trên cùng data dir thoát với mã 2; dừng êm qua stop-file (server exit 0) |
| GPU (model thật) | **19 pass** (13 Phase 1 + 6 Phase 2): audio qua job trùng Phase 0 và result cache trả cùng bytes; job dài 114 s đang chạy khi tạo lease → `LONG_JOB_RUNNING`, live chạy sau khi job dài xong, job bị hủy khi queued thì không chạy; hủy job dài đang chạy thì audio bị bỏ; 30 job hỗn hợp chỉ một lane, VRAM cấp phát giữ 1,9 GiB |

### 16.8 Benchmark nhiều job cùng chờ (HTTP thật, RTX 3060)

| Kịch bản | Kết quả |
|---|---|
| A — 30 job cùng lúc (10 live, 10 preview, 10 prepare; câu LIVE điển hình), chạy lại riêng | Tổng 67,8 s; inference p50 2,44 s, p95 2,65 s; chờ queue live p50 10,1 s / max 19,9 s; preview p50 32,3 s / max 44,9 s; prepare p50 55,1 s / max 65,3 s (prepare được 1 lượt công bằng); tạo job qua HTTP p50 2,7 ms / p95 4,8 ms; tải WAV bằng read token p50 26,8 ms / p95 41,8 ms (khoảng 225 KB); GPU 98 %, 1918 MHz, 69 °C |
| A — lần chạy đầu | Vài lượt inference mất 25–37 s (p95 25,5 s), nên live phải chờ p50 60 s. Chạy lại riêng không tái hiện được. Nghi do tải GPU bên ngoài hoặc trạng thái clock 🔶 |
| B — một câu live mỗi 3 s trong lúc 40 job prepare đang chạy | Live chờ queue p50 1,4 s / p95 2,4 s (tối đa bằng một lượt prepare đang chạy); live inference p50 2,5 s; 50/50 job done; 4 lượt công bằng cho prepare |
| C — job dài 114 s audio đang chạy khi tạo lease | `ready_for_live: false`, `LONG_JOB_RUNNING`, ước tính còn 29,6 s; live thực tế chờ 31,5 s; job long mới bị 423 |
| VRAM | Cấp phát 1,90 GiB khi rảnh; đỉnh 5,47 GiB lúc đăng ký voice bằng Whisper; reserved khi chạy job 2,1–2,6 GiB |

Nhận xét: với một GPU và chi phí sàn khoảng 2,4 s mỗi câu, 10 câu live xếp hàng thì câu cuối chờ khoảng 20 s, mà TTL live mặc định là 60 s. Sarae (director) nên tiếp tục đọc từng câu một như hiện tại, không đẩy nhiều câu live cùng lúc. Câu nào không kịp thì `expired`, không đọc trễ.

### 16.9 Vận hành Windows

- Dev: `scripts\run.ps1` (foreground).
- Service: `scripts\service.ps1 install|start|stop|restart|status|logs|uninstall`. Task Scheduler chạy `pythonw -m sarae_voice.supervisor` lúc đăng nhập (`-AtStartup`: lúc khởi động máy, cần lưu mật khẩu).
- Supervisor: khởi động lại khi crash hoặc khi gặp exit 75 (lỗi CUDA), backoff 2–60 s, bỏ cuộc sau 10 lần lỗi nhanh trong 10 phút; exit 2 (cấu hình sai hoặc đã có instance khác) thì không khởi động lại.
- Dừng: `stop` ghi `supervisor.stop`; supervisor ghi `server.stop`; server rút các job đang chờ (cancelled, "Server shutting down"), đợi lời gọi GPU đang chạy tối đa `shutdown_grace_s`, rồi thoát; chỉ kill tiến trình con của chính nó sau thời gian chờ.
- Lỗi CUDA: engine chuyển sang `error` (`TTS_GPU_FAILED`), service ngừng nhận việc, tiến trình tự thoát với mã 75 để supervisor khởi động lại sạch.
- Khóa `data\.instance.lock` và `data\.supervisor.lock`; log trong `data\logs\` (xoay vòng 20 MB).
- Không script nào dừng tiến trình GPU khác. Chưa thử `service.ps1 install` trên máy này vì nó tạo scheduled task (thay đổi cấu hình hệ thống); supervisor đã được test trực tiếp.

### 16.10 Còn tồn tại

- Ước tính thời gian còn lại của job dài lệch tới khoảng 40 %; chỉ dùng để hiển thị, không dùng để hứa.
- Không preempt được lời gọi GPU đang chạy; job dài chạy trước LIVE vẫn chặn câu live (đã đo 31–42 s).
- Bất thường hiệu năng trong lần chạy đầu (lượt sinh 25–37 s) chưa rõ nguyên nhân; Phase 3 nên theo dõi `tts_inference_seconds` p95 và GPU clock.
- Job và idempotency key nằm trong RAM; restart thì mất (trả `JOB_LOST`, đúng thiết kế), Backend phải tạo job mới.
- Read token không chứng minh danh tính máy (16.4); cần TLS/mạng riêng hoặc relay ở Phase 3–4.
- `service.ps1` chưa được chạy thật trên máy này (cần bạn đồng ý tạo scheduled task).
- Import thư viện mất 16–84 s tùy cache đĩa (khởi động lần đầu sau boot chậm).

## 17. Phase 3 — tích hợp Sarae Backend

### 17.1 Module và migration

| File | Vai trò |
|---|---|
| `backend/app/tts_server.py` (mới) | Client HTTP duy nhất tới voice-server: health (cache 5 s), status, voice PUT/GET/DELETE/warmup, synthesize (preview), job create/get/cancel/cancel-tag, audio stream (relay), LIVE lease. URL chỉ lấy từ cấu hình, id kiểm tra bằng regex, không follow redirect, token orchestrator chỉ nằm trong header. Lỗi chia hai loại: `TtsUnavailable` (mạng, timeout, chưa cấu hình, 503) và `TtsError` (mã lỗi do voice-server trả về). Chỉ retry lời gọi idempotent; tạo job luôn mang `Idempotency-Key`. |
| `backend/app/voice_profiles.py` (mới) | Bảng `voice_profiles` + API `/api/ai/voice/profiles` + đồng bộ (`ensure_synced`, `sync_pending`). |
| `backend/app/tts_orchestrator.py` (mới) | Bảng `tts_utterances`; `speak`, `prepare`, `cancel_agent`, `session_ended`; capability của Agent; fallback; readiness; LIVE lease; reconcile; relay + token cho Agent; vòng nền 15 s. |
| `backend/alembic/versions/20261103_0021_voice_profiles_tts_utterances.py` (mới) | Tạo 2 bảng (chỉ thêm; `ensure_schema` lúc khởi động là lưới an toàn giống các module khác). |
| `ai_voice.py` | `VoiceConfig.provider` thêm `omnivoice` (model `demo_default`, rate 0, không settings, voice = id profile của chính owner); `FallbackPolicy`; nhánh preview và Speak thủ công. Edge/ElevenLabs không đổi. |
| `cloud_agents.py` | Heartbeat giữ `voice.capabilities`; schema payload `omnivoice` cho `tts_speak`/`tts_prepare`; `create_command` từ chối 409 `AGENT_NO_OMNIVOICE` khi Agent chưa báo capability. |
| `live_reply_director.py`, `ai_live_batches.py`, `live_sessions.py`, `main.py`, `config.py` | Mỗi nơi thêm một nhánh `provider == "omnivoice"` gọi orchestrator; Dừng giọng nói và kết thúc phiên hủy job; session readiness có thêm khóa `tts`; khởi động schema + vòng nền; 6 biến `SARAE_TTS_*`. |
| Test | `tests/fake_voice_server.py` (voice-server giả qua `httpx.MockTransport`), `tests/test_tts_omnivoice.py` (27 test), `tests/test_tts_real_voice_server.py` (tùy chọn, model thật); `support.py` thêm 2 module vào danh sách patch session. |

Không sửa Windows Agent, Web, OBS hay voice-server.

### 17.2 API phía Sarae Backend

| Method | Path | Ai gọi | Ghi chú |
|---|---|---|---|
| GET / POST | `/api/ai/voice/profiles` | owner (Web) | Danh sách; tải lên multipart `wav` + `name` (+ `transcript` tùy chọn). 201 kèm trạng thái sync (`PENDING` nếu voice-server offline) |
| GET / PATCH / DELETE | `/api/ai/voice/profiles/{id}` | owner | PATCH `{name?, transcript?}`: transcript `null` quay về Whisper; DELETE trả 409 nếu giọng đang được chọn |
| PUT | `/api/ai/voice/profiles/{id}/wav` | owner | Thay WAV: revision mới, transcript quay về Whisper, object cũ bị xóa |
| POST | `/api/ai/voice/profiles/{id}/sync` | owner | Đẩy lại bản hiện tại lên voice-server (ví dụ khi thay máy GPU) |
| GET | `/api/ai/voice/profiles/{id}/wav` | owner | WAV gốc, nguyên byte |
| PUT / GET | `/api/ai/voice/config` | owner | Như cũ, thêm `omnivoice`, `fallback`, `omnivoice_configured` |
| POST | `/api/ai/voice/preview` | owner | Với `omnivoice`: trả `audio/wav` của profile đang chọn ở revision hiện tại; header `X-Voice-Revision`; lỗi có `X-Sarae-Error`; không fallback |
| POST | `/api/ai/voice/speak` | owner | Như cũ; với `omnivoice` trả thêm `utterances` (không có token) |
| POST | `/api/ai/voice/readiness` `{agent_id}` | owner | Kiểm tra chủ động trước LIVE: service, model, sync, warmup, capability của Agent, GPU; không đặt lease |
| GET | `/api/ai/voice/utterances?agent_id=` | owner | Trạng thái utterance (không có text, không có token) |
| GET | `/api/live/sessions/{id}/readiness` | owner | Như cũ, thêm khóa `tts` (thụ động: không sync, không warmup, không lease) |
| GET | `/api/agents/tts/audio/{utterance_id}?agent_id=&wait=` | Agent (`X-Sarae-Agent-Key`) | Relay WAV (stream) |
| POST | `/api/agents/tts/audio/{utterance_id}/token?agent_id=` | Agent | Read token cho đường direct (chỉ có khi `SARAE_TTS_AGENT_URL` được đặt và token còn trong RAM); nếu không thì 404 `USE_RELAY` |

Mã lỗi gửi cho Web nằm ở header `X-Sarae-Error` (cùng pattern với `MEDIA_NOT_READY`), ví dụ: `TTS_BACKEND_UNREACHABLE`, `TTS_NOT_READY`, `TTS_BUSY`, `AGENT_NO_OMNIVOICE`, `VOICE_NOT_FOUND`, `SESSION_ENDED`, `TEXT_TOO_LONG`.

### 17.3 Luồng utterance → job → command

```
director / Speak / prepare
   │  provider == omnivoice ?
   ▼
tts_orchestrator.speak()
   1. phiên còn RUNNING?             không → SpeakRefused SESSION_ENDED (không fallback)
   2. Agent báo capability?          không → AGENT_NO_OMNIVOICE (có thể fallback theo chính sách)
   3. voice-server online + ready?   không → TTS_* (có thể fallback)
   4. profile của owner, ensure_synced (đẩy lại nếu voice-server thiếu hoặc khác rev)
   5. ghi utterance NEW (body, + intro nếu có), commit
   6. POST /v1/tts/jobs  Idempotency-Key = utterance.id, priority live, deadline, reader = agent,
      owner_ref = owner, tags agent:/owner:/session:   → JOB_CREATED   (VOICE_STALE → sync lại 1 lần)
      lỗi → FAILED, hủy các job đã tạo
   7. create_command(tts_speak, payload chỉ có id, key "tts:<utterance>") → SENT
      lỗi → ABORTED, hủy job
```

| Tình huống | Phase 3 xử lý |
|---|---|
| Retry tạo job (mạng chập chờn) | Cùng `Idempotency-Key` → voice-server trả lại cùng job (đã test) |
| Job tạo xong nhưng command lỗi | Utterance `ABORTED`, job bị hủy trên voice-server (đã test) |
| Backend restart giữa job và command | `reconcile()` (khi khởi động và mỗi 15 s): utterance `NEW`/`JOB_CREATED` quá 120 s → `ORPHANED`, hủy job, **không gửi lại** (đã test) |
| HTTP timeout phía Web | Không tự sinh utterance mới; operator bấm lại thì đó là một utterance mới, theo ý operator |
| LIVE kết thúc | `finish_session` → `session_ended`: hủy job theo tag `session:<id>`, utterance → `CANCELLED`, nhả lease; `speak` sau đó trả `SESSION_ENDED` (đã test) |
| Dừng giọng nói | `cancel_agent`: hủy tag `agent:<id>` + `tts_clear` như cũ (đã test) |
| Job hết hạn | Không retry với deadline mới (key cũ trả lại job cũ); relay trả 410 (đã test) |
| Voice đổi revision | Job giữ `voice_rev` lúc tạo (voice-server snapshot prompt); `tts_utterances.voice_rev` ghi lại |
| Trạng thái | `tts_utterances.state` = vòng đời phía Sarae; `tts_job_state` = trạng thái GPU (cập nhật định kỳ); trạng thái phát (PLAYING/PLAYED) **chưa có**, cần sự kiện từ Agent ở Phase 4 |

**Phát tối đa một lần**: Phase 3 bảo đảm mỗi utterance có tối đa một job và tối đa một lệnh `tts_speak` (command key cố định, `tts_speak` không retryable). Việc **không phát trùng** ở phía Agent cần dedup theo `utterance_id` ở Phase 4, nên Phase 3 chưa khẳng định điều này.

### 17.4 Voice profile và storage

- WAV gốc nằm trong storage provider của Media Library (`upload_provider`: R2 hoặc Google Drive; test dùng MEMORY), object `voice-<id>-r<revision>`. SQLite chỉ lưu provider, object id, SHA-256 và thông số audio; đọc lại có kiểm tra SHA-256.
- Kiểm tra lúc tải lên: chỉ nhận WAV PCM, mono/stereo, 8–96 kHz, 1–20 s, ≤ 5 MB (QC đầy đủ do voice-server làm khi sync, kết quả lưu ở `qc.server`).
- Transcript: mặc định Whisper (không gửi transcript, voice-server tự nhận dạng như demo; text Whisper được lưu về `transcript_asr`); sửa thủ công thì revision tăng; trả `null` thì quay về Whisper (cũng tăng revision).
- `revision` tăng khi WAV hoặc transcript thay đổi. `tts_sync_state` có các giá trị PENDING / SYNCED / FAILED, kèm `tts_synced_revision` và `tts_rev`. `ensure_synced` có khóa theo profile (nhiều request đồng thời chỉ upload 1 lần, đã test) và tin kết quả kiểm tra trong 60 s; sau đó kiểm tra lại rev trên voice-server, thiếu thì upload lại (trường hợp thay máy GPU, đã test).
- Voice-server offline: metadata vẫn đọc được; vòng nền tự đồng bộ khi voice-server trở lại (đã test).
- Cách ly owner: mọi truy vấn lọc theo `owner_id` (owner khác nhận 404); id là uuid toàn cục; voice-server nhận `owner_ref` và kiểm tra lại; cấu hình giọng chỉ nhận profile của chính owner.

### 17.5 Readiness, đồng bộ trước LIVE và lease

| Bước | Khi nào | Có giữ GPU không |
|---|---|---|
| Kiểm tra trước LIVE (`POST /api/ai/voice/readiness`) | Khi người dùng bấm kiểm tra hoặc đồng bộ | Không: chỉ sync, warmup và đọc `GET /v1/admission/live` |
| Readiness thụ động (`tts` trong session readiness) | Mỗi lần trang readiness gọi | Không |
| Đặt lease | Vòng nền thấy phiên `RUNNING` của owner dùng omnivoice (≤ 15 s sau khi chạy) | Có, TTL 60 s |
| Gia hạn lease | Mỗi 15 s khi phiên vẫn `RUNNING` | Có |
| Hủy lease | `finish_session` (ngay lập tức) hoặc vòng nền thấy phiên không còn `RUNNING` | — |
| Backend mất kết nối | Không gia hạn được → voice-server tự bỏ lease sau TTL | — |

Readiness trả `ready: false` và cảnh báo `LONG_JOB_RUNNING` kèm ghi chú "ước tính, không phải cam kết" khi GPU đang chạy job dài; ngoài ra còn các cảnh báo `AGENT_NO_OMNIVOICE`, `VOICE_NOT_SYNCED`, `TTS_BACKEND_UNREACHABLE`, `TTS_NOT_READY`. Nhiều phiên dùng chung GPU thì mỗi phiên có một lease riêng (đã test).

### 17.6 Bảo mật relay và read token

- Relay `GET /api/agents/tts/audio/{utterance}`:
  - Xác thực bằng `verify_agent` (khóa Agent hiện có).
  - Utterance phải thuộc **đúng Agent đó và đúng owner của Agent**; nếu không thì 404 (không lộ thông tin tồn tại).
  - Utterance đã hủy/hỏng/hết hạn (cộng 30 s ân hạn) thì trả 410.
  - `job_id` lấy từ DB, không lấy từ request; host lấy từ cấu hình; redirect bị từ chối (502 `TTS_REDIRECT_REFUSED`, không có request nào rời khỏi host đã cấu hình, đã test).
  - Stream theo chunk 64 KB; nếu `Content-Length` vượt `SARAE_TTS_RELAY_MAX_BYTES` thì 502 `AUDIO_TOO_LARGE`, nếu không có header thì cắt khi vượt ngưỡng.
  - Upstream 202 → 202; `JOB_*` → 410/409; mạng lỗi → 502 `TTS_BACKEND_UNREACHABLE`.
- Read token: chỉ giữ trong RAM của Backend (`_tokens`, tự xóa khi hết hạn); **không** lưu trong `tts_utterances`, payload command, response cho Web hay log (đã test cả 4). Agent lấy token qua endpoint `/token` (xác thực khóa Agent, đúng utterance của mình), và chỉ khi `SARAE_TTS_AGENT_URL` được đặt. Backend restart thì mất token → Agent dùng relay.
- Token orchestrator không bao giờ rời Backend.

### 17.7 Fallback

`voice.fallback` = `{live_reply, prepare, manual, provider}`, mặc định `none` ở mọi mục.

| Ngữ cảnh | none | comment_only | hold_for_operator | provider |
|---|---|---|---|---|
| LIVE reply | `voice=failed` + ghi chú mã lỗi; không nói, không comment | `_fallback` sẵn có (giữ câu để trả lời bằng comment) | `voice=review` | Gửi `tts_speak` bằng Edge/ElevenLabs đã cấu hình |
| Prepare | Bỏ qua, ghi monitor | — | — | `tts_prepare` bằng provider dự phòng |
| Speak thủ công | Lỗi HTTP kèm mã | — | — | Provider dự phòng |
| Preview | Luôn trả lỗi, không fallback | | | |

Không bao giờ fallback khi phiên LIVE đã kết thúc, câu đã hết hạn hoặc bị hủy (`fallback_ok = False`, đã test). Fallback khi Agent tải hoặc phát lỗi thuộc Phase 4.

### 17.8 Kết quả kiểm thử

| Bộ test | Kết quả |
|---|---|
| Toàn bộ Backend (unittest) | **383 pass** (356 cũ + 27 mới), 1 skipped (test thật tùy chọn); trước Phase 3 là 356 pass |
| `test_tts_omnivoice.py` (voice-server giả) | 27 pass: CRUD và cô lập owner, WAV trong storage, kiểm tra WAV; revision và sync lại; offline rồi online; thay máy GPU; sync đồng thời (SQLite file); validate cấu hình; preview (WAV, đúng profile, offline, không fallback); Edge không chạm voice-server; Agent cũ không nhận payload mới (cả qua `create_command` trực tiếp); schema payload; speak tạo job + command không có token; retry idempotent; command lỗi → hủy job; voice-server từ chối; reconcile orphan; 5 speak đồng thời; director với 4 chính sách fallback và phiên đã kết thúc; kết thúc LIVE; lease nhiều phiên; readiness; Dừng giọng nói; prepare; relay (ủy quyền, lỗi upstream, redirect, offline, giới hạn kích thước, đã hủy); utterance hết hạn; token chỉ cho đúng Agent và không xuất hiện trong response Web; log không chứa token |
| `test_tts_real_voice_server.py` (voice-server thật, RTX 3060) | 1 pass: sync voice 3,4 s (Whisper trùng Phase 0); preview 1,94 s; readiness ready (warmup `ram`); điều phối speak 58 ms (kiểm tra voice + job + command); relay 1,93 s cho 2,28 s audio (gồm chờ GPU); lease đặt và nhả đúng; voice-server dừng êm bằng stop-file |
| Regression Edge/ElevenLabs | `test_voice_settings`, `test_ai_live_reply`, `test_cloud_agents`, `test_live_sessions`, `test_owner_isolation` pass không cần sửa |

### 17.9 Rủi ro còn lại

- Chưa có xác nhận phát từ Agent: `SENT` chưa có nghĩa là đã phát. "Không phát trùng" cần dedup theo `utterance_id` ở Phase 4.
- Lease được đặt trong vòng ≤ 15 s sau khi phiên `RUNNING` (do vòng nền), không phải ngay lúc bấm Start.
- Token chỉ nằm trong RAM: nếu Backend chạy nhiều tiến trình, token không chia sẻ được (hiện Backend là một tiến trình, đúng ràng buộc sẵn có).
- Xóa profile khi voice-server offline để lại bản sao trên voice-server (không ai tham chiếu được; `sync` không tạo lại). Có thể dọn thủ công.
- `tts_utterances` chưa có job dọn bản ghi cũ (vài chục byte mỗi câu); cần chính sách giữ (ví dụ 30 ngày) khi đưa vào chạy thật.
- Một voice-server cho cả hệ thống (`SARAE_TTS_SERVER_URL` toàn cục); nhiều GPU theo owner chưa hỗ trợ.

### 17.10 Việc Phase 4 cần làm trên Windows Agent

1. **Capability trong heartbeat**: `voice.capabilities = {"providers": ["edge","elevenlabs","omnivoice"], "tts_job": 1, "audio_path": ["relay","direct"]}`. Backend chỉ gửi payload omnivoice khi có dòng này.
2. **Schema payload** (`cloud/commands.py`):
   - `TtsPayload.provider` thêm `"omnivoice"`; thêm `tts: {utterance_id, job_id, voice_rev, deadline_at, audio_path: "auto"|"relay", intro?: {utterance_id, job_id}}`.
   - `TtsPreparePayload` thêm `tts_jobs: [{utterance_id, job_id}]`, khớp thứ tự với `texts`.
3. **`OmniVoiceProvider`**:
   - Tải WAV bằng relay `GET /api/agents/tts/audio/{utterance}?agent_id=&wait=20` với `X-Sarae-Agent-Key`.
   - Nếu `audio_path == "auto"`: thử `POST …/token`, rồi gọi voice-server trực tiếp `GET {base_url}/v1/tts/jobs/{job_id}/audio` với `Authorization: Bearer <read_token>` và `X-Sarae-Agent-Id`; chỉ khi lỗi mạng mới quay về relay.
   - Xử lý 202 (chờ thêm, không quá `deadline_at`) và 410 (bỏ câu, không fallback).
4. **`VoiceEngine`**:
   - Bỏ câu khi `now > deadline_at` (hạn tuyệt đối, ngoài TTL hiện có).
   - Dedup theo `utterance_id` trong 15 phút.
   - Cache theo provider với đuôi `.wav`.
   - Intro và body là hai clip riêng, như hiện tại.
5. **Sự kiện phát** qua `POST /api/agents/events`: `tts_playback {utterance_id, status: playing|played|expired|stopped|failed, code, a1..a4}` (mục 9), để Backend cập nhật utterance và đo độ trễ end-to-end.
6. **Fallback lúc tải hoặc phát lỗi** theo `fallback` (Backend sẽ gửi spec trong payload ở Phase 4).
7. Không đụng ducking, OBS, Edge hay ElevenLabs.
