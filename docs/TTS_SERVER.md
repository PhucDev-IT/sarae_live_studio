# Sarae TTS Backend (G-OmniVoice) — Thiết kế đã duyệt

> Trạng thái: **kiến trúc A đã duyệt** (2026-11-02). Phase 0 (baseline) đang thực hiện; Phase 1 trở đi chờ duyệt.
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
