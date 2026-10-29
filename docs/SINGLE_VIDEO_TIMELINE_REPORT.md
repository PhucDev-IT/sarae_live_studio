# Báo cáo: Single Video Timeline LIVE

Nhánh `feature/single-video-timeline-live` (từ `dev` @ 41c879b) · 2026-10-29.
Tài liệu kỹ thuật: [SINGLE_VIDEO_TIMELINE.md](SINGLE_VIDEO_TIMELINE.md) · Khảo sát và các quyết định: [SINGLE_VIDEO_TIMELINE_PHASE1_SURVEY.md](SINGLE_VIDEO_TIMELINE_PHASE1_SURVEY.md).

## 1. Kết quả theo phase

| Phase | Nội dung | Commit |
|---|---|---|
| 1 | Khảo sát kiến trúc, chốt các quyết định với người dùng | (tài liệu) |
| 2 | Chế độ `SINGLE_VIDEO_TIMELINE` (mặc định cho phiên tạo từ Web) và `ADVANCED_LEGACY`; video chính, loop | `0ebe2af` |
| 3 | API timeline: phiên bản DRAFT → APPROVED → ACTIVE, kiểm tra marker | `9eb6715` |
| 4 | Agent phát 1 video; scheduler ghim theo vị trí phát; dry-run; cổng LIVE | `4e01746` |
| 4 | Đối chiếu ghim thật trên Console, ghim thủ công, reconcile | `2220b53` |
| 3 (UI) | Web: chọn chế độ/video, Timeline Editor, bảng ghim trên Điều khiển LIVE | `4708096` |
| 5 | AI Timeline Analyzer (FFmpeg, Faster-Whisper, Gemini, vision), cache, bản nháp để duyệt | `3bbd692` |
| 6–7 | Tài liệu luồng, báo cáo này | (commit cuối) |

## 2. Quyết định đã chốt với người dùng

| Câu hỏi | Quyết định |
|---|---|
| Thế nào là "đang LIVE thật" | Đọc trạng thái trên LIVE Console. Không xác định được thì không ghim. |
| AI chạy ở đâu | Backend server |
| Định dạng thời gian | HH:MM:SS (lưu trong DB theo ms) |
| Cách ghim | Mỗi lúc chỉ ghim 1 sản phẩm (ghim B thay A) |
| API khi client không gửi `playback_mode` | `ADVANCED_LEGACY`. Web mới luôn gửi Single làm mặc định. |
| Lineup của phiên Single | Marker chọn sản phẩm có trong catalog BE. Console không có sản phẩm đó thì lệnh ghim báo lỗi, video vẫn chạy. Lineup tự lấy từ timeline. |
| Ghim thủ công | Mặc định giữ ghim thủ công tới khi bấm "Tiếp tục timeline". Tùy chọn: tự quay lại lịch ở marker kế tiếp. |

## 3. File thay đổi (46 file, +5233 / −96 so với `dev`)

| Khu vực | File mới | File sửa |
|---|---|---|
| Backend | `app/live_timeline.py`, `app/timeline_ai/{__init__,api,pipeline,providers}.py`, `requirements-timeline-ai.txt` | `live_sessions.py`, `live.py`, `cloud_agents.py`, `agent_media.py`, `playback_policy.py`, `shop_schema.py`, `config.py`, `main.py` |
| Migration | `alembic/versions/20261029_0016_single_video_timeline.py`, `20261029_0017_timeline_ai.py` | — |
| Windows Agent | `sarae_agent/timeline/{__init__,scheduler,executors}.py` | `playback/engine.py`, `cloud/commands.py`, `runtime.py`, `config.py`, `auto_pin.py` |
| Web | `TimelineEditor.tsx`, `TimelineAIPanel.tsx`, `TimelinePinPanel.tsx`, `SingleVideoSetup.tsx`, `libraryVideos.ts`, `timelineAiApi.ts`, `timeline-editor.css` | `LiveSessions.tsx`, `LiveControl.tsx`, `liveSessionsApi.ts`, `App.tsx`, `live-sessions.css`, `live-control.css` |
| Test | Backend: `test_live_timeline_{mode,api,play,manual}.py`, `test_timeline_ai.py`. Agent: `test_timeline_{scheduler,reconcile}.py` | `tests/support.py` |
| Tài liệu | `SINGLE_VIDEO_TIMELINE.md`, `SINGLE_VIDEO_TIMELINE_PHASE1_SURVEY.md`, báo cáo này | `AUTO_PIN_WINDOWS_AGENT.md`, `LIVE_SESSION_MANAGEMENT.md`, `PLAYBACK_POLICY.md` |

Không sửa Android Agent. Không xóa API, bảng, cột hay dữ liệu nào.

## 4. Migration

| Revision | Thêm | Dữ liệu cũ | Downgrade | Đã thử |
|---|---|---|---|---|
| `20261029_0016` | 4 cột `live_sessions` (`playback_mode`, `main_media_id`, `video_loop`, `active_timeline_id`) và 2 bảng `live_timelines`, `live_timeline_markers` | Phiên cũ nhận `ADVANCED_LEGACY` | Xóa cột/bảng mới; phiên và lịch sử giữ nguyên | upgrade → downgrade → upgrade trên DB thật (tạm): PASS |
| `20261029_0017` | Bảng `timeline_analyses`, `timeline_ai_cache` | — | Xóa 2 bảng | upgrade → downgrade → upgrade: PASS |

Schema khởi động (`shop_schema.upgrade`) cũng tự thêm các cột/bảng này, kể cả với DB cũ (có test riêng).
**Rollback phải làm cả hai việc:** trả code về bản cũ **và** chạy `alembic downgrade 20261023_0015`. Nếu chỉ downgrade DB, code mới khởi động sẽ tự tạo lại các cột.

## 5. Test

| Bộ test | Trước | Sau | Kết quả |
|---|---|---|---|
| Backend (`unittest`) | 266 | **316** (+50) | PASS (1 skip có sẵn từ trước) |
| Windows Agent (`pytest`) | 320 | **355** (+35) | PASS |
| Web | — | `tsc -b`, `vite build` | PASS |
| Web trên trình duyệt (Backend + dữ liệu tạm) | — | các luồng ở mục 5.2 | PASS |

### 5.1 Các kịch bản bắt buộc ở Phase 6

| Kịch bản | Test | Kết quả |
|---|---|---|
| Một video phát liên tục 60 phút | `EngineTimelineTests.test_an_hour_on_loop_without_switching_files` (130 phút giả lập, 2 vòng loop, 1 file duy nhất); `SchedulerTests.test_continuous_hour_pins_each_marker_once` | PASS |
| Timeline ghim nhiều sản phẩm | như trên, cùng `test_start_plays_the_main_video_with_the_active_timeline` (Backend) | PASS |
| Seek, pause, resume, loop | `test_seek_applies_only_the_state_at_the_new_position`, `test_polling_the_same_position_and_pause_never_pin_twice`, `test_pause_and_seek_through_the_engine`, `test_loop_runs_the_schedule_again_from_the_start`, `test_resume_continues_from_a_position_not_a_product` | PASS |
| Agent mất kết nối / reconnect | `test_unknown_state_is_read_back_before_acting`, `test_an_agent_running_another_version_gets_the_active_one_again`, `test_an_ack_that_never_comes_times_out_and_the_late_one_starts_nothing` | PASS |
| Mất kết nối API ghim | `test_failures_back_off_then_stop_until_the_next_marker`, `test_console_executor_reports_failures`, `test_reads_are_spaced_and_a_failed_one_changes_nothing` | PASS |
| User ghim thủ công | `test_manual_pin_holds_until_resume`, `test_manual_pin_can_return_at_the_next_marker`, `test_a_pin_changed_on_the_console_holds_the_timeline`, `test_manual_pin_holds_the_timeline_first` (Backend), `test_hold_and_resume_commands` | PASS |
| Hai marker trùng thời gian | `test_same_time_markers_the_later_one_wins_once`, `test_markers_are_validated_and_ordered`, `test_same_position_the_later_marker_wins…` | PASS |
| Sản phẩm bị xóa hoặc ngừng đồng bộ | `test_a_product_removed_from_the_catalog_blocks_activation`, `test_product_without_pin_data_fails_once_and_never_loops`, `test_start_plays_…` (sản phẩm không có trong giỏ Console không chặn Start) | PASS |
| Timeline bị sửa khi phiên đang chạy | `test_a_version_activated_during_live_is_pushed_without_restarting_playback`, `test_new_version_during_live_keeps_what_is_pinned` | PASS |
| AI đề xuất sai / không tìm được sản phẩm | `test_only_real_products_and_low_confidence_goes_to_review`, `test_nothing_found_and_bad_answers`, `test_vision_confirms_or_suggests_uncertain_segments`, `test_draft_from_suggestions_needs_review_before_approval` | PASS |
| Chế độ legacy vẫn hoạt động | Toàn bộ 266 test Backend và 320 test Agent cũ; `test_older_clients_without_a_mode_keep_the_product_queue`; `test_legacy_sessions_never_get_a_hold` | PASS |
| Không ghim khi chưa thật sự LIVE | `test_nothing_is_pinned_until_live_is_confirmed`, `test_live_gate_reads_the_console_without_blocking_and_expires` | PASS |
| Dừng LIVE thì hủy action đang chờ | `test_stopping_live_cancels_the_action_on_its_way`, `test_video_play_loads_the_timeline_and_stop_clears_it`, `test_finish_stops_the_agent` | PASS |
| Mock/dry-run mọi hành động TikTok | Toàn bộ test dùng executor dry-run hoặc giả lập; Agent mặc định `timeline.dry_run: true` | PASS |

### 5.2 Kiểm tra trên trình duyệt (dữ liệu tạm, không đụng DB thật)
- Tạo phiên: mặc định Single; chuyển sang Nâng cao thì ẩn ô chọn video.
- Timeline Editor:
  - thêm mốc;
  - mốc chưa chọn sản phẩm bị báo lỗi và khóa nút Lưu;
  - nhập giờ chính xác `00:05:30`;
  - kéo thả mốc;
  - duyệt mốc AI;
  - Lưu nháp, Duyệt, Kích hoạt;
  - phiên bản đã duyệt chỉ xem được.
- Điều khiển LIVE (heartbeat giả): bảng ghim hiện đúng sản phẩm cần ghim / đang ghim, mốc kế tiếp, giữ ghim thủ công và dry-run.
- Panel AI: báo lỗi rõ khi chưa có API key; modal cấu hình lưu được.
- Đã phát hiện và sửa 6 lỗi giao diện trong lúc thử (xem commit `4708096`). Người dùng phát hiện thêm 1 lỗi khi chạy với dữ liệu thật: thumbnail video chính phủ kín trang Phiên LIVE. Lỗi này đã được sửa ở commit riêng.

## 6. Giới hạn còn lại (cần làm trước khi dùng thật)

| # | Giới hạn | Việc cần làm |
|---|---|---|
| 1 | **Nhãn nhận biết LIVE trên Console chưa kiểm chứng** (`LIVE_LABELS` trong `auto_pin.py`) | Mở Console thật lúc chưa LIVE và lúc đang LIVE, chỉnh nhãn. Trước khi làm xong việc này hãy giữ dry-run. |
| 2 | **Chưa chạy trên OBS / TikTok thật.** Toàn bộ test dùng OBS giả và Console giả | Diễn tập 1 phiên với dry-run, rồi tắt dry-run trên 1 máy thử |
| 3 | **AI chưa chạy thật**: máy chủ chưa cài faster-whisper và chưa có API key Gemini | `pip install -r backend/requirements-timeline-ai.txt`, thêm key, thử trên video LIVE thật để chỉnh prompt và ngưỡng |
| 4 | Độ chính xác của mốc ghim khoảng **±1 giây** (watchdog đọc vị trí OBS mỗi giây) | Đủ cho ghim sản phẩm; muốn chính xác hơn thì giảm chu kỳ watchdog |
| 5 | Ghim thủ công trực tiếp trên Console chỉ được phát hiện sau **tối đa 30 giây** (chu kỳ đọc lại) | Có thể giảm `reconcile_seconds` |
| 6 | Resume sau khi Agent khởi động lại phát từ vị trí mà Web gửi (mặc định 0) | Có thể lưu vị trí cuối cùng để tự tiếp tục |
| 7 | Ô chọn sản phẩm trong Timeline Editor chưa có tìm kiếm | Thêm combobox có tìm kiếm nếu catalog lớn |
| 8 | **Deploy theo thứ tự Backend trước, Agent sau.** Backend cũ từ chối các event mới của Agent | Ghi vào quy trình release |
| 9 | Job AI chạy trong tiến trình Backend, mỗi lần 1 job; nếu Backend khởi động lại thì job đang chạy dừng ở RUNNING | Có thể thêm bước dọn job treo lúc khởi động hoặc đưa sang worker riêng |
