# Phase 6 — Full E2E, reliability và vận hành

Ngày kiểm chứng: 2026-11-03. Nhánh `feature/tts-voice-server`. Kiến trúc A giữ nguyên.

**Local: PARTIALLY READY.** Toàn tuyến kỹ thuật offline đã pass trên PC RTX 3060 12 GB, nhưng còn chờ người dùng nghe duyệt WAV, nghiệm thu bộ cài release và chạy bền theo ca vận hành. **Remote production: BLOCKED / chưa nghiệm thu** vì chưa có VPS/VPN/TLS để kiểm chứng. Không mở TikTok LIVE, stream hoặc recording; không cài Scheduled Task, push, merge hay deploy.

## Luồng và phạm vi thay đổi

```text
Web --JWT/owner--> Backend --orchestrator token--> voice-server GPU
                   |                                 |
                   | command/job reference           | WAV/read token
                   v                                 v
             Windows Agent <--- direct hoặc Backend relay
                   |
                   v
               OBS Sarae Voice
                   |
             PLAYING / PLAYED --> Backend --> Web monitor
```

Web không nhận orchestrator/read token. Agent được cấp quyền tải theo owner/Agent/job; relay kiểm tra lại quyền. GPU service độc lập với lifecycle Agent.

| File/nhóm | Thay đổi |
|---|---|
| `backend/app/voice_policy.py` | Khóa theo owner và kiểm tra giọng đang dùng |
| `ai_voice.py`, `voice_profiles.py`, `live_sessions.py` | Guard API, serialize với start/finish, preview theo profile |
| `tts_orchestrator.py` | Serialize publish job/command với clear/end để tránh job xuất hiện sau cancellation |
| `cloud_agents.py` | Bổ sung `session_id` trong command envelope để telemetry có đúng phiên |
| `agent-core/.../voice/jobs.py` | Metric riêng `obs_startup_ms` |
| `web/src/VoiceSettings.tsx`, `voiceApi.ts` | Preview không lưu default; Speak qua OBS với cấu hình đã lưu; monitor |
| Backend/Agent/Web tests, `scripts/tts_phase6_report.py` | Kiểm thử và xuất bằng chứng đã loại credentials |

Session hiện không snapshot toàn bộ owner config khi start: mỗi utterance đọc config hiện tại, rồi job snapshot revision. Vì vậy Backend trả **409 / VOICE_IN_USE** khi đổi voice spec có liên quan OmniVoice, thay WAV/transcript hoặc xóa profile đang mặc định/được utterance của phiên tham chiếu. Bảo vệ cả RUNNING và DRAFT đã gắn Agent (start chưa rõ kết quả). Tạo profile, rename, preview và sửa profile không dùng vẫn được phép. Fallback policy giữ hợp đồng cũ; Edge→Edge/ElevenLabs không bị áp ngữ nghĩa khóa mới.

Khóa RLock này dành cho Backend **một process**; không phải distributed lock. Không chạy nhiều worker/replica khi chưa thay cả RAM token registry và khóa nghiệp vụ bằng cơ chế chia sẻ. Khóa có thể giữ trong lúc gọi upstream/start, nên mutation cùng owner phải chờ; owner khác độc lập.

POST `/api/ai/voice/preview` nhận thêm `voice_id` tùy chọn (32 hex). Khi có override, chỉ đọc profile thuộc owner, dùng OmniVoice/demo_default, không ghi Voice Settings và không fallback. Request cũ tiếp tục dùng config đã lưu. OmniVoice trên Web dùng “Nghe thử”; Edge/ElevenLabs giữ “Lưu & nghe thử”. Speak OBS đọc config đã lưu, yêu cầu Agent online và xác nhận phát; không tự lưu default.

## Ứng dụng thực tế và môi trường cô lập

Đã chạy executable Tauri **debug v0.3.0** thật với UI Vite, Rust IPC và Core sidecar được build lại bằng PyInstaller. Không chỉ gọi VoiceEngine bằng script. UI quan sát được Cloud đã kết nối, OBS 33 đã kết nối, Agent đang chạy; heartbeat có capability OmniVoice. Kích hoạt bằng API test và secret DPAPI trong `SARAE_AGENT_HOME` riêng. Mỗi đợt dùng owner, database, app copy và home riêng; overlay tắt để hạn chế tác động OBS.

Web thật port 5176 → HTTP Backend port 8794 → GPU service port 8783 → ứng dụng Windows → OBS đang mở. OBS được xác nhận không stream/record trước test. Web upload reference đã duyệt `voice_ref_pause_7s_test.wav`, Auto Whisper, sync, preview, lưu config, kiểm tra readiness, chọn Agent, bấm Speak; monitor nhận PLAYED. Không bắt nhập transcript thủ công. Preview profile override được kiểm tra cả khi config mặc định vẫn Edge, không ghi đè config.

Không thay executable trong bản cài đang vận hành. Bộ cài release chưa được nghiệm thu; khi phát hành phải rebuild sidecar và desktop cùng source. Agent không khai báo dependency Torch/CUDA; GPU model chỉ nằm ở voice-server. Packaged `--self-check` pass, frozen=true, DPAPI roundtrip=true. Service GPU sống qua các lần đóng/mở app, không bị Agent khởi chạy bản GPU thứ hai.

## Độ trễ thực đo

Số liệu chi tiết: [latency.csv](tts-phase6/latency.csv), [latency.json](tts-phase6/latency.json), [samples.csv](tts-phase6/samples.csv). Sau lượt Speak từ Web, vòng đo lặp gọi Backend orchestrator với text cố định để loại thời gian thao tác UI/AI; command vẫn đi qua HTTP Agent thật tới OBS thật. Đơn vị ms. p50 median, p95 nearest rank chỉ khi n≥20. Phân nhóm theo nguồn kết quả thực tế, không theo tên scenario đơn thuần.

| Nhóm chính | n | E2E p50 | E2E p95 |
|---|---:|---:|---:|
| Sinh mới, direct | 30 | 2826 | 3913 |
| Result cache, direct | 27 | 355 | 407 |
| Result cache, relay | 29 | 348 | 418 |

| Thành phần p50 / p95 | Sinh mới direct | Cache direct | Cache relay |
|---|---:|---:|---:|
| Backend orchestration | 74 / 126 | 46 / 83 | 47 / 92 |
| Command delivery đến claim | 39 / 65 | 34 / 69 | 44 / 64 |
| GPU queue | 0 / 0 | 0 / 0 | 0 / 0 |
| Inference | 2610 / 3718 | 0 / 0 | 0 / 0 |
| Agent chờ audio ready | 2530.5 / 3609 | 47 / 77 | 47 / 63 |
| Download | 14 / 16 | 0 / 15 | 0 / 0 |

Các thành phần có overlap, không cộng percentile để suy ra E2E. Download 0 nghĩa là thấp hơn độ phân giải timer, không phải không có HTTP. Queue 0 ở các nhóm chính không chứng minh GPU luôn rảnh; job dài được thử riêng.

| Nhóm bổ sung | n | E2E median | Khoảng quan sát |
|---|---:|---:|---:|
| Intro rồi body, cache | 5 | 1997 | 1939–3874 |
| Prepared cache | 5 | 276 | 250–308 |
| Các câu sau một lần Agent reconnect | 5 | 295 | 281–311 |
| Cold generation trong direct cache scenario | 4 | 2295.5 | 2278–2402 |
| Cold generation trong relay scenario | 3 | 2203 | 2067–2276 |

Không báo p95 cho các nhóm này. Nhóm reconnect đo các câu sau khi đã reconnect, không đo tổng downtime. Intro E2E tính tới PLAYING của body nên bao gồm phần intro trước đó.

Đợt soak SQLite file riêng: **100/100 PLAYED**, 166.097 giây cho toàn test. Câu ngắn “Dạ vâng ạ.”, một lần generated 2142 ms và 99 cache hit p50/p95 338/388 ms. Giữ thành cohort riêng vì độ dài khác. Metric OBS startup được bổ sung sau các batch chính: 99 cache samples của soak p50/p95 **156/172 ms**, một generated **172 ms**; thêm một generated ở batch trước **156 ms**. Không gán metric mới cho các sample cũ.

E2E = khoảng Backend bắt đầu nhận text → command claim + Agent monotonic acceptance → OBS PLAYING. Khoảng claim→Agent acceptance chưa có metric riêng, nên đây là ước lượng quan sát cùng PC; không dùng để bảo đảm latency xuyên mạng. Không bao gồm AI tạo nội dung. `obs_startup_ms` đo từ gọi play_file tới quan sát PLAYING; `playback_start_ms` cũ còn chứa chờ/tải/queue. OBS PLAYING không chứng minh người xem TikTok đã nghe.

Batch đầu bị native access violation ở `sqlalchemy/engine/_result_cy...pyd` sau 84 lượt đã hoàn tất, không phải assertion pass của toàn batch. Fixture cũ dùng một connection SQLite memory StaticPool cho nhiều luồng HTTP. Đã đổi test sang SQLite file/pool tương tự Backend thực tế; soak 100 lượt sau đó pass. Đây là bằng chứng giới hạn của fixture; chưa khẳng định đã chứng minh tuyệt đối nguyên nhân native crash hoặc độ bền nhiều giờ. Các row lưu trước crash chỉ được tính khi đã PLAYED, có WAV và event tương ứng.

## WAV và OBS

**208/208** WAV nguyên byte khớp SHA-256 với cache tương ứng của Agent; **208/208** mono PCM16 24 kHz. Checkpoint, `demo_default`, num_step/guidance, pitch/speed và post-processing không đổi. Không resample/EQ/cắt/nối waveform trong bộ nghe.

Bộ nghe được xuất vào `phase6-review/listen.html` trong thư mục visualization của chat, gồm WAV body và intro riêng. Có câu tiếng Việt tên Lan/Mai, sản phẩm, giá, size và nha/nhé/ạ; một batch đầu còn chữ số thô trong câu test, không coi đó là bộ chuẩn hóa văn bản hoàn chỉnh. **Chưa có đánh giá nghe của người dùng** về mất từ đầu, cắt đuôi, click hoặc timbre. Byte equality và ASR không thay thế nghe duyệt. Trình nghe giữ intro/body riêng, không mô phỏng chính xác mixer OBS.

Ứng dụng thật báo PLAYING→PLAYED, thứ tự intro/body theo queue; video volume sau lượt phát bằng giá trị trước test, đã kiểm tra sai số 0.001 dB. Ducking/clear/restore và không overlap có regression Agent kiểm soát. Chưa đo loudness mix thực tế hoặc thu loopback để khẳng định video không lấn tiếng. Không thay video scene/timeline. Settings/mute nguồn Sarae Voice và volume video được lưu, khôi phục trong finally; lần fixture crash đã khôi phục từ cấu hình OBS đã lưu rồi chạy các lượt sau. Kiểm tra cuối không stream/record.

## Lỗi và bảo mật

| Kịch bản | Kiểu kiểm chứng / kết quả |
|---|---|
| Service offline trước job | App thật: 503, không fallback |
| Direct lỗi, relay còn | App thật: PLAYED qua relay |
| Direct và relay lỗi | App thật: FAILED, fallback none |
| Job dài / readiness | GPU thật: ready_for_live=false |
| Hết hạn khi chờ GPU | GPU + app thật: EXPIRED, không phát muộn |
| Clear trong waiting_audio | App thật: CANCELLED |
| Agent crash/restart trước phát | App copy cô lập: accepted tombstone, duplicate command không replay |
| Voice-server restart khi queued | Process test cô lập: old job 410 JOB_LOST, utterance FAILED, không tạo job mới |
| Backend restart sau tạo job | Backend process mới + app restart: PLAYED cùng job qua relay, không tái tạo job |
| Agent mất Backend/reconnect | App thật: reconnect; outbox/dedup kiểm soát; 5 câu hậu reconnect pass |
| OBS unavailable | Agent fake transport: lỗi có giới hạn, không fallback none; không tắt OBS của người dùng |
| Cancel 410 / relay mất / outbox overflow | Agent tests: pass; telemetry có thể mất, tombstone không mất |
| Session ended / clear race create_job | Backend đồng thời: serialize, CANCELLED và từ chối phát phiên đã kết thúc |
| Multi-Agent/owner, token, Agent cũ | Backend/Agent fixtures: owner khác và Agent khác không tải/nhìn utterance, capability/schema và token isolation pass |

Chi tiết các lỗi thật có JSON trong `docs/tts-phase6/*result*.json`. Restart Backend thử cả Agent offline rồi khởi động lại, không phải mọi interleaving của hệ thống phân tán. Không có hai PC thật; không tuyên bố nghiệm thu multi-machine. Clear được chèn khi chờ audio, còn hủy giữa từng byte download được kiểm soát bằng tests, chưa có fault injection mạng vật lý.

## Regression và tái lập

| Nhóm | Kết quả |
|---|---|
| Backend toàn suite | 391 tests OK, 4 skipped |
| Backend sau lifecycle/locking fixes | 33 TTS + 25 LiveSessions pass |
| Agent toàn suite | 452 pass |
| Agent OmniVoice sau bổ sung lỗi | 26 pass, gồm 4 case mới |
| Voice-server có process tests | 79 pass, 19 GPU-opt-in skipped |
| Web typecheck / production build | Pass; warning chunk >500 kB đã tồn tại |
| Web browser/component | 10 pass |
| PyInstaller self-check | Pass |
| Full app riêng faults/restarts/soak | Pass theo ma trận trên |

Regression bao gồm Edge, ElevenLabs (mock, không tiêu credits), Manual Speak, AI reply/fallback, prepare, auto comment, readiness, profile/owner isolation, OBS ducking/queue. Các fixture nghiệp vụ LIVE không mở TikTok LIVE thật. GPU thật được chạy qua E2E, không chạy lại benchmark Phase 0. Full suites chạy trước các sửa lifecycle cuối; các nhóm liên quan đã chạy lại sau sửa. Có warning socket/deprecation từ harness hiện có.

Test opt-in `backend/tests/test_tts_full_application.py` yêu cầu OBS đã mở/không stream-record, Vite desktop1420/Web5176, sidecar test đã build ở `.phase6/packaged`, model service test8783 và reference Downloads đã duyệt. Dùng `SARAE_PHASE6_E2E=1` với Backend Python; mặc định skip để CI không điều khiển desktop. Các mode `SARAE_PHASE6_FAULTS`, `SARAE_PHASE6_RESTART_VOICE_PID`, `SARAE_PHASE6_RESTART_BACKEND`, `SARAE_PHASE6_SCENARIOS`, `SARAE_PHASE6_SAMPLES` được mô tả trực tiếp ở test. Chỉ chạy trên môi trường cô lập, không tái sử dụng token/data production. `scripts/tts_phase6_report.py INPUT OUTPUT` xuất thống kê và copy WAV nguyên byte.

## Vận hành local

1. Backup database Backend, storage WAV/profile, Agent home và secrets theo chính sách hiện có. Giữ nguyên home khi nâng cấp: cache/journal/DPAPI credentials không đặt trong thư mục binary tạm.
2. Chạy duy nhất một voice-server trên GPU theo launcher hiện có `voice-server/scripts`; giữ pinned checkpoint/preset. Bind loopback cùng máy hoặc IP VPN cụ thể. Xác minh health/model/error trước warmup; không load thêm bản model từ Agent.
3. Backend chạy **1 worker**. Đặt `SARAE_TTS_SERVER_URL`, `SARAE_TTS_SERVER_TOKEN`; `SARAE_TTS_AGENT_URL` là URL Agent truy cập được (bỏ trống để dùng relay). Token upstream khớp `SARAE_VOICE_ORCHESTRATOR_TOKENS`, đọc từ secret env/file được bảo vệ; không ghi vào Web. `CORS_ORIGINS` chỉ chứa origin Web cần dùng; build frontend với `VITE_API_URL` đúng Backend API.
4. Mở OBS, kiểm tra Sarae Voice và audio routing; mở Agent đã rebuild đúng capability. Không coi Cloud connected đồng nghĩa GPU ready. Tạo/sync profile → lưu giọng → readiness chủ động/warmup → preview → Speak offline → quan sát PLAYED và nghe.
5. Nếu GPU job dài, chờ/kết thúc đúng luồng trước chuẩn bị LIVE. Không retry tạo job mới khi timeout mơ hồ, không gia hạn câu cũ. Với JOB_LOST/EXPIRED/FAILED, operator quyết định một câu mới còn phù hợp.
6. Khi dừng, clear và xác nhận mixer phục hồi; đóng Agent không tắt GPU service độc lập. Supervisor fake-process restart đã có tests; không cài task tự khởi động trong Phase 6. Bộ cài/supervisor thực tế cần nghiệm thu trước vận hành unattended.

## Retention và giám sát

| Dữ liệu | Chính sách hiện có / việc vận hành |
|---|---|
| Result WAV cache | TTL 86400 s, max 1 GiB; cleanup định kỳ 300 s, giới hạn không phải quota tổng mọi storage |
| Debug outputs | Mặc định tắt; cleanup theo TTL khi bật, tránh bật lâu trên production |
| VoiceClonePrompt | RAM LRU 16; disk là dữ liệu dẫn xuất theo fingerprint, theo dõi dung lượng, không tự xóa reference WAV |
| Jobs/idempotency GPU | RAM, finished retention 900 s và deadline; restart trả lost, không tái dựng job tự động |
| Read token / lease | TTL hữu hạn; token job mất khi restart, lease cần renew; không biến expired thành deadline mới |
| Agent audio cache | Tối đa 300 file MP3/WAV, Omni download ceiling 20 MiB/file; worst case khoảng 6 GiB, theo dõi disk |
| Backend tts_utterances | Chưa có purge tự động; đề xuất audit 30–90 ngày sau khi xác định nhu cầu điều tra, cần migration/maintenance riêng |
| Agent SQLite tombstones | Giữ bền, chưa bật purge; tuyệt đối không xóa chỉ vì audio cache/telemetry hết hạn |
| EventOutbox | Persistent sequence, giới hạn 1000; mất mạng dài có thể drop event cũ, không được dùng mất event làm lý do replay |
| Agent logs | 2 MB + 5 backup; kiểm tra quyền đọc, không lưu transcript/token vào báo cáo |
| Temp/metrics | Xóa test homes và process riêng sau thu bằng chứng; metrics RAM reset khi restart, export riêng nếu cần lịch sử |

Không có maintenance nào trong Phase 6 xóa WAV gốc hoặc storage production. Backup tombstones cùng home trước nâng cấp; không copy một home đang chạy sang hai Agent. Theo dõi GPU errors, queue/deadline, JOB_LOST, outbox overflow, last heartbeat, disk/cache size và tỷ lệ terminal events. Những khoảng trống retention Backend/prompt disk là việc cần hoàn thiện khi vận hành lâu dài.

## Checklist VPS / remote chưa thực thi

- Cấp VPS, VPN/private route giữa Backend–GPU–Agent; không public voice-server ra Internet. TLS khi đi qua mạng không tin cậy; firewall chỉ cho peer cần thiết, không tắt xác thực.
- Kiểm tra URL từ đúng máy gọi: Backend URL tới GPU; direct Agent URL tới GPU; localhost trên VPS không phải PC GPU. Thử cả relay khi direct không tới được. Token orchestration chỉ Backend; read token ngắn hạn theo job, rotate secrets có overlap.
- CORS origin Web production cụ thể, HTTPS frontend/backend, timeout tương thích queue/deadline; không tăng retry thành duplicate job. Đồng bộ giờ/NTP và kiểm tra lệch clock/deadline.
- Lưu bền DB/storage/secrets Backend; một worker tới khi hỗ trợ state chia sẻ. GPU data dir bền, backup reference/profile; Agent nâng cấp giữ home/DPAPI và journal trên chính Windows user đã kích hoạt.
- Rebuild/kiểm tra bộ cài release, heartbeat capability; kiểm tra supervisor crash/restart và quy trình rollback. Chỉ đăng ký task/service sau khi người dùng cho phép riêng.
- Chạy lại hai Agent vật lý, owner isolation, Web đổi Agent, TLS/auth errors, direct/relay outages, Backend/GPU/Agent restarts, latency xuyên mạng và một ca soak đủ dài. Xác minh không chạy hai GPU server cùng lúc.
- Người dùng nghe duyệt WAV/mixer thật, kiểm tra đầu/cuối/click/ducking, sau đó mới xét READY cho môi trường tương ứng. Không tự mở LIVE hoặc production từ checklist này.
