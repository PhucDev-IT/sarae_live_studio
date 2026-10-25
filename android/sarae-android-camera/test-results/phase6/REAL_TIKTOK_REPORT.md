# Phase 6 — Actual TikTok APK experiment, 23/10/2026

## Kết luận

**Functional goal PASS:** ứng dụng TikTok thật `com.ss.android.ugc.trill` **47.4.3/470403**, được patch trong môi trường lab, đã hiển thị MP4 **A đỏ → B xanh → A đỏ** trong preview chuẩn bị LIVE. Hai lần đổi nguồn giữ nguyên Camera2 session và Surface gốc của TikTok. Đây không phải consumer mô phỏng hoặc Surface preview riêng của Sarae.

**Cadence FAIL theo ngưỡng Phase 3 `<100 ms`:** max renderer output gap **162,265 ms** trong cửa sổ switch và **288,636 ms** trên toàn session LIVE preview được ghi. Không thay ngưỡng để PASS. Kết quả tổng hợp là **PARTIAL_PASS**: loader, hook và injection preview PASS; chất lượng cadence của Phase 3 chưa được chứng minh trên TikTok và phép đo hiện tại vượt ngưỡng.

Không nhấn “Phát LIVE”, không quay/đăng nội dung, không kiểm chứng streaming, audio hoặc receiver. Người dùng tự đăng nhập và mở màn hình LIVE camera; agent chỉ tiếp tục từ trạng thái đó. Không kết luận LIVE broadcasting compatible.

## Các gate riêng biệt

| Gate | Kết quả | Bằng chứng |
| --- | --- | --- |
| Snapshot APK thật và khả năng repack | PASS | 61 APK đủ ZIP/CRC, hash local bằng hash thiết bị, version/package/dependency/certificate nhất quán |
| Loader-only install | PASS | `adb install-multiple`: Success, không module, signature bypass 0 |
| Loader-only startup | PASS | PID **32710**, `LSPatch bootstrap completed`, startup tới `NewUserJourneyActivity`, sáu PID sample không đổi, không fatal được ghi |
| Module install/startup | PASS | Update cùng signer lab Success; PID **4022**, module `co.sarae.camera.poc` được embed và hook install/attach trong package TikTok |
| Camera2 interception thực tế | PASS | `createCaptureSession(SessionConfiguration)`, regular, input null, một Surface; redirect vào pipeline hiện có |
| Pixel preview thực tế và A/B switch | PASS | Ba screenshot của UI TikTok cùng ROI đỏ/xanh/đỏ; 2/2 switch complete, cùng original Surface và một session trong bài switch |
| Output PTS trong renderer | PASS | 4.736 output, 0 timestamp không tăng trong active session |
| Max output gap `<100 ms` | **FAIL** | 162,265 ms trong switch window; 288,636 ms toàn active session |
| Java/native crash quan sát được | PASS trong phạm vi thu | Không fatal AndroidRuntime/libc/DEBUG hoặc pipeline error được thấy; không bảo đảm dài hạn |
| LIVE encode/uplink/receiver/audio | **NOT TESTED** | Không bắt đầu broadcast |

[Summary tự động](summary.json) và [analysis output](analysis-output.txt) tách rõ functional goal, ba gate loader/hook/injection, và cadence FAIL.

## Target, artifact và chữ ký

- Branch `feature/tools-android`; Samsung **SM-G996U**, Android **14/API 34**, ARM64, non-root. Package thật giữ nguyên `com.ss.android.ugc.trill`; không đổi package ID, versionCode hoặc targetSdk để chạy thử. Original minSdk 23/targetSdk 36; loader base yêu cầu API 28, thiết bị thử API 34.
- Bộ đang cài được đọc bằng `pm path` và sao lưu đủ **61 APK**, **514.293.113 byte**. Có base, ARM64/language/density config và dynamic features, gồm `df_a_dex`, `df_camera_biz` cùng ABI split. Path không đổi trong lúc pull; hash SHA-256 trên thiết bị bằng local hash cho từng file. Đây là toàn bộ **installed set** tại snapshot, không đảm bảo mọi feature có thể tải trong tương lai.
- [Original APK manifest/hash inventory](original-apks.json), [static validation gốc](original-static.json), [package trước](package-before.txt), [sau pull](package-after-pull.txt). Các APK gốc giữ riêng tại `.tools/phase6/original/`; không đưa binary TikTok vào source/report để phân phối.
- Original certificate SHA-256: `d7811ec4166fea6cc720ba66699dc84b584ac9e6986613a76d4e43d8cbe32b27`.
- Lab certificate SHA-256: `8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b` (Android debug key). Không giả mạo certificate gốc.

| Artifact | SHA-256 |
| --- | --- |
| LSPatch v1.2/487 debug JAR | `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153` |
| Original TikTok base | `80277ad5eaf6eb5d0dcf39537b03274ce417bab8259d661003a6a1fc1ff0f6dd` |
| Loader-only base | `da0010c35c5f80b200cd709b6531952d95ad022d2a93b313524c41bf0d3b95fe` |
| Bridge base | `f829a13fcbbe7168941f656c9b0e0e9c255f8d55e965b1a91a1a4af13e554bab` |
| Sarae module APK | `1a0a87d762c58943509c0978036921a57df136699ed2a924691bdb6da3804d21` |

Hash toàn bộ split, manifest, ABI, DEX count và certificate nằm trong [loader-only inventory](loader-only-static.json), [bridge inventory](bridge-static.json); [artifact inventory](artifact-inventory.json) bao gồm source và fixture. Bộ loader-only 522.657.109 byte; bộ bridge 523.574.705 byte. Sáu mươi split đã ký được tái sử dụng nguyên hash khi nhúng module vào base.

## Cách patch và xác minh

Đã đọc source pinned [ApkPatcher v1.2](https://github.com/JingMatrix/LSPatch/blob/v1.2/patch/src/main/java/org/lsposed/patch/ApkPatcher.java), blob `2a92ad7e3267c44d926ba598b239acd83a099e80`, và [CLI](https://github.com/JingMatrix/LSPatch/blob/v1.2/patch/src/main/java/org/lsposed/patch/LSPatch.java), blob `a4d2cfc86a2f4b8bf0f30ac866b3376c6b28805a`. Patcher nhận toàn bộ bộ APK; base được rewrite appComponentFactory để nạp loader, split được repack/ký cùng key. Origin APK được giữ nguyên hash trong nested `assets/lspatch/origin.apk`.

Loader-only chạy trước, không có embedded module. Chỉ sau gate startup PASS mới build `:app:assembleDebug` và nhúng module. [Patch loader log](patch-loader-only.txt), [patch bridge base log](patch-bridge-base.txt), [build log](build-module.txt). Không build hoặc sửa consumer-test trong Phase 6; engine/EGL Kotlin compilation được ghi UP-TO-DATE.

Cả hai config giữ **`sigBypassLevel=0`, `useManager=false`, `injectDex=false`, không addedPermissions/DocumentsProvider/targetSdk override**. `injectDex=false` không có nghĩa APK nguyên trạng: metaloader, loader, native framework và component factory đã thay như patch log mô tả. Không signature spoofing, integrity bypass, root, phiên bản loader thay thế hoặc unrelated hooks.

Kiểm tra offline: manifest/package/version/split dependencies, ZIP CRC, embedded origin hash và signer của từng APK. Python chỉ cho phép intentional nested-APK overlap trên entry origin có size bị chặn và hash khớp exact input. Lần verify mặc định đầu tiên với một split báo `Missing META-INF/MANIFEST.MF`: split còn minSdk 23 nhưng patcher ký v2. Đã dùng `apksigner verify --min-sdk-version 34` cho đúng thiết bị được thử; v2 xác minh thành công. Đây là cấu hình **verification API target**, không thay manifest hay bypass kiểm tra. Không tuyên bố các APK lab hỗ trợ API 23. [Kiểm chứng v2/API 34](split-signing-api34-check.txt), [tài liệu apksigner](https://developer.android.com/tools/apksigner).

## Install, consent và thao tác người dùng

Người dùng đã xác nhận trực tiếp: **“Đây là máy lab; chấp nhận thay TikTok và mất dữ liệu cục bộ”** trước uninstall. [Authorization record](lab-authorization.json). Bản gốc chỉ được gỡ sau khi backup/validation xong. Cài loader-only thành công; nhúng module sau đó dùng update `install-multiple -r` cùng signer lab, không gỡ lần nữa.

Sau cài mới có dialog Điều khoản/Quyền riêng tư. Automatic approval review từ chối lệnh nhấn “Đồng ý và tiếp tục”; **lệnh đó chưa chạy**, không thử đường vòng. Người dùng sau đó thông báo đã tự đăng nhập và mở LIVE camera, yêu cầu tiếp tục. Agent đọc UI hiện tại rồi chỉ gửi broadcast đổi fixture. Login, consent và điều hướng tới camera do người dùng thực hiện, không phải agent tự thao tác tài khoản. [UI ban đầu](bridge/ui.xml), [màn hình dialog](bridge/screen.png), [UI handoff](actual-preview/ui-before.xml).

## Camera bridge thực tế

`HookEntry` mở thêm đúng package TikTok, chỉ main process và `FLAG_DEBUGGABLE`; consumer riêng vẫn được giữ trong allowlist cũ. `Camera2HookPrototype` nhận scope để ghi đúng package/log folder và log overload/output quan sát được. Chỉ đổi composition/scope và diagnostics; tái sử dụng `StableFrameProvider`, decoder và EGL producer hiện có.

Trace TikTok xác nhận overload:

```text
CameraDeviceImpl.createCaptureSession(android.hardware.camera2.params.SessionConfiguration)
sessionType=0 input=null outputs=1
executorSame=true callbackSame=true parametersSame=true
```

User navigation trước bài switch đã tạo hai session 1280×720 rồi đóng; sau đó LIVE preview có session generation **3** trong full process trace, original Surface **84963549**, dummy **22631203**, EGL **1920×1080**. Không tuyên bố cả process chỉ tạo một session. **Trong controlled A→B→A, không session_observed/session_redirect/egl_created/egl_released mới**, hai switch đều ghi Surface 84963549; chỉ đóng camera/EGL sau khi thu xong và HOME.

CameraService snapshot ghi client TikTok PID 4022, camera device ID `1`, normal session, một ImageReader drain 640×480/YUV420. **HAL ở CONFIGURED, 0 produced frame, không in-flight request** tại snapshot. Original TikTok Surface vẫn được EGL cấp frame MP4. Điều này xác nhận preview đã dùng frame từ pipeline Sarae, không xác nhận capture callbacks, metadata synchronization hay video encoder contract hoàn chỉnh. [Active client/stream](actual-preview/active-client-stream.txt).

## Bằng chứng pixel và switch continuity

Screenshot là màn hình **TikTok chuẩn bị LIVE**, vẫn có nút **“Phát LIVE”** trước và sau test. Không chạm nút đó. Preview gốc được lấy từ chính output trong SessionConfiguration; không mở Surface/app khác để minh họa.

ROI không có overlay: x `[400,680)`, y `[600,1000)` trên screenshot 1080×2400; lấy 1.120 sample RGB mỗi ảnh.

| Screenshot thật | Mean RGB | Nhận dạng |
| --- | --- | --- |
| [A trước switch](actual-preview/A-before.png) | 255, 0, 0 | Fixture A đỏ |
| [B sau switch 1](actual-preview/B-after.png) | 0, 0, 255 | Fixture B xanh |
| [A sau switch 2](actual-preview/A-after.png) | 255, 0, 0 | Fixture A đỏ |

[Pixel evidence](actual-preview/pixel-evidence.json), [command journal](actual-preview/switch-commands.jsonl). A là MP4 640×480/30 FPS, B là fixture Phase 3 320×240/15 FPS; output renderer mặc định 30 FPS. Hai lệnh đều `switchNow`, không mở lại preview. Request→renderer commit lần lượt **271,563 ms** và **145,555 ms**; đây không phải phép đo end-to-end display latency. Mỗi screenshot được chụp sau khi chờ hai giây.

Active session 158,404 giây có **4.736 output**, PTS strictly increasing, khoảng 29,928 output/s tính từ event cadence. Cửa sổ switch có 297 output, khoảng 30,087/s, 0 PTS không tăng. Max gap **288,636 ms** toàn session và **162,265 ms** trong switch window; ngưỡng cũ 100 ms **FAIL**. Chưa tách được ảnh hưởng của TikTok/UI/screencap/GC, không tự gán nguyên nhân hoặc sửa engine theo suy đoán.

Ba ảnh không đen không chứng minh không có frame đen thoáng qua giữa các sample. Không có instrumentation per-frame của compositor TikTok, nên không tuyên bố zero black frames/dropped frames ở toàn display pipeline. Output gap/skipped ticks/source callbacks được giữ nguyên trong JSONL.

## Thời gian và crash diagnostics

**Tổng bảo thủ: 280,900 giây = 4 phút 40,900 giây**, dưới 300 giây, 2 switch, không endurance. Đã dừng mọi thử nghiệm thiết bị sau HOME. [Budget journal](device-budget.jsonl) tính cả:

- Snapshot/hash/pull APK: 26,515 s.
- Loader install/startup/diagnostics: 49,895 s.
- Module install/startup/UI/diagnostics: 36,043 s.
- Read-only handoff observation: 5,534 s.
- Hai switch và cleanup/diagnostics: 11,047 s.
- Preview giữ mở trong lúc xem ảnh/lập luận giữa tool calls: **89,136 s**, được cộng thêm, không bỏ qua.
- Camera activity người dùng đã mở trước lần agent đọc đầu tiên, tính bảo thủ từ camera event sớm nhất trong PID log: **62,729 s**.

Build, patch, CRC/signature analysis và phân tích offline không tính vào ngân sách. Thời gian người dùng đăng nhập/onboarding không được đo như bài test do agent chạy; camera activity nhìn thấy trong log vẫn được cộng như trên. Không suy ra thử 30 phút hoặc stability lâu dài.

Diagnostics: [loader PID/startup](loader-only/startup-logcat.txt), [loader run](loader-only/run.json), [module startup/hook](bridge/startup-logcat.txt), [camera/decoder/EGL/switch PID log](actual-preview/pid-relevant-logcat.txt), [JSONL đầy đủ](actual-preview/jsonl-after/bridge-4022.jsonl), [crash buffer](actual-preview/crash-buffer.txt), [exit-info](actual-preview/exit-info.txt). Exit-info có PACKAGE UPDATED cho PID cũ và isolated WebView cleanup; không gán thành crash. Không fatal Java/native được thấy trong capture; không có quyền đọc trực tiếp private tombstones trên máy non-root.

PID buffer đầy đủ được giữ private trong `.tools/phase6/private/`; report chỉ đưa các tag loader/camera/crash cần thiết. [Private artifact hash](private-evidence.json) và [evidence hashes](evidence-sha256.json) bảo toàn provenance. Full CameraService dumps cũng giữ trong `.tools/phase6/`.

## Trạng thái cuối và rollback

**Bản lab có module vẫn đang cài**, preview đã đóng bằng HOME. Người dùng đã đăng nhập thủ công sau cài lab; không tự uninstall/clear dữ liệu tài khoản mới này. Không chạy rollback trong execution này.

Rollback về đúng 61 APK original đã lưu, **sẽ xóa dữ liệu/tài khoản cục bộ trong app lab hiện tại** và không khôi phục dữ liệu trước lần uninstall đầu. Chỉ chạy khi tiếp tục chấp nhận mất dữ liệu lab:

```powershell
cd D:\Android_Programming\sarae_tool_live_studio\android\sarae-android-camera
./scripts/rollback_phase6.ps1 -RestoreLabSnapshot
```

Script kiểm tra SHA-256 đủ 61 file trước khi gỡ lab, cài lại bộ original bằng `install-multiple`, lưu `rollback-install.txt` và `package-restored.txt`. Có thể cài lại bản chính thức từ Play thay thế, nhưng version/split sẽ phụ thuộc bản được phân phối lúc đó. APK backup không phải backup account/application data.

Không chạy lại `test_phase6_loader.ps1` trên app đang có dữ liệu cần giữ; script yêu cầu flag rõ ràng `-AllowLabDataLoss`. Preparation script cũng chặn ghi đè snapshot gốc đã tồn tại.

Tái phân tích evidence, không chạm thiết bị:

```powershell
./scripts/sample_phase6_pixels.ps1
python scripts/analyze_phase6.py
```

## Giới hạn còn lại / bước tiếp theo

Đã xác minh đúng **một build TikTok lab 47.4.3/470403 trên SM-G996U/API 34, màn hình pre-broadcast LIVE preview và 2 switch-now**. Không suy rộng sang app TikTok chưa patch, version khác, Camera1/NDK, nhiều output, lens/orientation changes, capture metadata, audio, encoding, broadcast hoặc receiver.

Nếu tiếp tục, ưu tiên điều tra cadence gap của target bằng trace ngắn có mục tiêu, giữ nguyên ngưỡng 100 ms; cần xác minh frame/display/capture-result timing trước khi tăng phạm vi. Không cần thêm UI, mock consumer, engine thứ hai hoặc test endurance để trả lời kết quả Phase 6 này.
