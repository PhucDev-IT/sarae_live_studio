# Phase 5 — Integration feasibility, 23/10/2026

## Kết quả

**Assessment PASS; bridge consumer PASS; mục tiêu đưa frame vào TikTok preview: BLOCKED / chưa đạt PASS.** Không có lần injection nào vào TikTok trong phase này. Chưa xác minh tương thích TikTok hoặc LIVE.

Đường chính thức đã tìm được nhận media dạng file: Share Kit/Android share Intent, Green Screen Kit và Content Posting API. Trong catalog và tài liệu công khai được xem xét, **không tìm thấy hợp đồng nhận chuỗi frame/Surface làm nguồn camera liên tục cho TikTok Android**. Đây là kết luận về tài liệu đã kiểm tra, không chứng minh mọi API đối tác riêng đều không tồn tại. Cần làm việc với TikTok nếu muốn đường camera được nhà cung cấp hỗ trợ.

LSPatch là một ứng viên thử nghiệm trong process đích, nhưng cần APK đích đã sửa, ký lại và runtime hook. Nó không inject được vào bản TikTok production đang cài chỉ bằng cách bật module. Chưa có căn cứ để kết luận loader hoặc bridge hoạt động trong TikTok 47.4.3.

## Bằng chứng được tái sử dụng và quan sát mới

- Branch `feature/tools-android`; SM-G996U, API 34, non-root. [Device](device-state.txt).
- [Phase 4](../phase4/TIKTOK_CAMERA_COMPATIBILITY.md): preview của `com.ss.android.ugc.trill` 47.4.3/470403 dùng Java Camera2; snapshot CameraService một output SurfaceTexture PRIVATE 1920×1080, normal session, không input. Exact overload, rotation/mirror/crop và các mode khác vẫn **UNKNOWN**. Không suy ra overload từ targetSdk 36.
- Metadata TikTok trước/sau Phase 5 giống nhau: version, code path, thời điểm cài/cập nhật và signature record. [Trước](tiktok-before.txt), [sau](tiktok-after.txt). Không mở, cài, sửa hoặc patch TikTok; không đăng nhập, thao tác tài khoản, ghi hình hay LIVE.
- Tái dùng Phase 1–3 cho decoder, looping, EGL ổn định, 100-switch baseline và lifecycle đã ghi nhận. Không chạy lại endurance; Phase 3 thực tế 23 phút 54 giây, không phải 30 phút.
- APK base Phase 4 được đọc tĩnh để kiểm tra signer, không pull lại hoặc phân phối APK. SHA-256 `80277ad5eaf6eb5d0dcf39537b03274ce417bab8259d661003a6a1fc1ff0f6dd`; certificate SHA-256 `d7811ec4166fea6cc720ba66699dc84b584ac9e6986613a76d4e43d8cbe32b27`. [apksigner output](tiktok-phase4-signing.txt). Đây là snapshot base cũ, không chứng minh mọi split hiện tại giống snapshot đó.

## Đối chiếu các cơ chế tích hợp

| Cơ chế | Khả năng được tài liệu hỗ trợ | Quan hệ với mục tiêu / bridge |
| --- | --- | --- |
| Share Kit hoặc Android ACTION_SEND + EXTRA_STREAM | Chuyển URI ảnh/video vào workflow chia sẻ TikTok | Có thể nhập MP4 hoàn chỉnh. Không có callback cấp từng frame hay thay nguồn Camera2 trong tài liệu này |
| Green Screen Kit | Chuyển một ảnh/video làm nền hiệu ứng; người dùng có thể trim rồi quay | Có thể đưa video vào nền hiệu ứng. Không xác nhận thay camera preview bằng EGL feed hoặc đổi A/B trực tiếp |
| Content Posting API | Upload/draft/direct post media; app approval và user authorization theo scope | Là workflow đăng nội dung; không có camera Surface contract trong endpoint được kiểm tra. Không gọi API trong phase này |
| Camera2 bridge hiện tại | Redirect output/request trong process consumer đã nạp module, EGL render vào Surface gốc | Đã kiểm chứng consumer riêng. Không có đường lấy Surface TikTok hoặc thay code CameraDeviceImpl trong process production từ controller hiện tại |
| LSPatch | Repack app đích, embed loader/framework và tùy chọn module | Có thể tạo môi trường hook thử nghiệm; sửa APK, ký lại, thay runtime. TikTok loader/API/transform chưa được kiểm chứng |
| OEM/HAL hoặc privileged companion app streaming | Tích hợp tầng hệ thống; companion role bị giới hạn cho preinstalled priv-app | Không phải khả năng của app Sarae thông thường trên máy stock non-root. Cần thay điều kiện triển khai và hỗ trợ OEM |

Nguồn chính thức đã đọc ngày 23/10/2026: [Share Kit Android](https://developers.tiktok.com/docs/en/share-kit-android-quickstart-v2), [Green Screen](https://developers.tiktok.com/docs/en/green-screen-kit), [Content Posting Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-get-started), [developer catalog](https://developers.tiktok.com/). Không triển khai các workflow chia sẻ/đăng bài vì không giải quyết hợp đồng camera liên tục được yêu cầu.

Android cô lập app theo UID/process; Surface phải được consumer cung cấp qua một hợp đồng phối hợp, không thể lấy từ tên package. Đây là suy luận kiến trúc từ [Application Sandbox](https://source.android.com/docs/security/app-sandbox), [BufferQueue](https://source.android.com/docs/core/graphics/arch-bq-gralloc) và code bridge hiện tại. Camera2 public API cấu hình đầu ra camera, không cung cấp API trong tài liệu đã xem để một app tự đăng ký MP4 thành camera hệ thống cho TikTok. [Camera HAL](https://source.android.com/docs/core/camera/camera3) là tích hợp thiết bị; [Companion app streaming](https://source.android.com/docs/core/permissions/app-streaming) yêu cầu role privileged, không phải giải pháp có sẵn cho Sarae trên thiết bị này.

## LSPatch: thay đổi APK, chữ ký và runtime

[README v1.2 của JingMatrix/LSPatch](https://github.com/JingMatrix/LSPatch/blob/v1.2/README.md) mô tả rootless loader bằng việc rewrite APK đích, embed framework và chỉ hook app đã patch. Blob README được đọc: `34227d4126c3fc46d7a4dd6c72a7ca67becad173`.

Bằng chứng trên **APK consumer do dự án sở hữu**: [patch log](patch-own-consumer.txt) ghi thay manifest, thêm metaloader/loader DEX, native library và embed module; [inventory](artifact-inventory.json) xác nhận origin APK, module `co.sarae.camera.poc`, loader v1.2/487, `sigBypassLevel=0`, `injectDex=false`, không thêm permission. Cờ `injectDex=false` không có nghĩa APK không bị sửa: metaloader/loader vẫn được đóng gói như patch log cho thấy. JAR SHA-256 `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153`; không đổi phiên bản loader trong phase này.

Consumer được ký Android debug certificate `8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b`, khác certificate TikTok. Không có private signing key của TikTok trong quy trình này. Theo [Android app update rules](https://developer.android.com/google/play/app-updates), cùng application ID cần certificate phù hợp hoặc valid proof-of-rotation để cập nhật; nếu không phù hợp phải gỡ bản cũ, làm mất dữ liệu cục bộ. Vì thế patch/re-sign không thể được coi là update an toàn cho production TikTok hiện tại. Không thực hiện gỡ app, đổi package ID, giả certificate hoặc vượt kiểm tra integrity.

Các blocker cụ thể:

1. **Không có hook trong process TikTok.** `HookEntry` vẫn chỉ allowlist `co.sarae.camera.consumer`. Mở rộng allowlist cũng không tự nạp runtime vào APK chưa patch.
2. **Loader phụ thuộc app đích.** Phase 2 PASS trên PoC không bảo đảm TikTok PASS. Báo cáo Douyin lịch sử đã có SIGSEGV trong ART VisitRoots/libmetasec_ml với loader không module; nguyên nhân chưa được chứng minh. Xem [Phase 2, mục crash cũ](../phase2/PHASE2_REPORT.md). Không suy ra lỗi đó chắc chắn sẽ xảy ra trên TikTok.
3. **Split/dynamic delivery.** Phase 4 có thay code path/thời điểm update trong khi version giữ nguyên. Cần snapshot nhất quán toàn bộ base/split và xác minh loader hỗ trợ cách đóng gói; không giả định patch một base là đủ.
4. **Camera behavior còn thiếu.** Exact overload TikTok, multi-output ở mode khác, lifecycle, request mutation và transform chưa trace trong process. SessionConfiguration consumer PASS mới chỉ khép một khoảng trống độc lập.
5. **Integrity/runtime và quyền.** App có thể từ chối bản ký lại hoặc loader có thể crash. Chưa xác nhận nguyên nhân nào ở TikTok 47.4.3; nếu gặp thì dừng, không bypass. File MP4 ở thư mục consumer riêng hiện tại cũng không tự đọc được từ package khác; môi trường thử tương lai cần cấp file/URI hợp lệ riêng.

## Thay đổi bridge tối thiểu

Camera2 đã được xác nhận ở Phase 4 và API 28+ công khai có thêm [createCaptureSession(SessionConfiguration)](https://developer.android.com/reference/android/hardware/camera2/params/SessionConfiguration). Phase 5 bổ sung coverage cho **regular session, một OutputConfiguration mặc định, một Surface cụ thể**; đây là kiểm chứng độc lập một thiếu sót đã biết, không khẳng định TikTok dùng overload này.

- Giữ executor, state callback và session parameters gốc khi thay output bằng drain ImageReader; request `addTarget` tiếp tục được map tới dummy Surface.
- Đối chiếu incoming OutputConfiguration với cấu hình mặc định của cùng Surface trước khi redirect. Trên [AOSP Android 14 OutputConfiguration.equals](https://github.com/aosp-mirror/platform_frameworks_base/blob/android-14.0.0_r1/core/java/android/hardware/camera2/params/OutputConfiguration.java), phép so sánh xét sharing, physical ID, dynamic range, stream use case, timestamp base và metadata khác; kiểm tra riêng số Surface. Cấu hình ngoài phạm vi được log `session_unsupported` và để Camera2 xử lý nguyên trạng.
- Legacy List còn hoạt động. Chưa thêm Camera1/NDK, multi-output, shared/deferred output, high-speed, reprocess, extensions, non-default output/session color-space hoặc request removeTarget/updateOutputConfigurations. Các đường này vẫn unsupported/unverified.
- Consumer thêm test-only intent `testSessionMethod=modern|legacy|shared`, không thêm UI. `shared` chỉ bật sharing flag trên **một Surface**, không phải bài multi-output.
- Engine, EGL renderer và stable output lifecycle được tái sử dụng nguyên trạng. Build log ghi `:engine:compileDebugKotlin UP-TO-DATE`. Chỉ build app chứa module và consumer cùng dependency bị ảnh hưởng.

## Kiểm chứng thiết bị và PASS/FAIL

| Test riêng | PID | Thời gian lượt | Frame consumer | Switch đúng pixel | Session / EGL | Max consumer gap | Kết quả |
| --- | --- | ---: | ---: | ---: | --- | ---: | --- |
| SessionConfiguration, 1920×1080 | 29444 | 17,809 s | 488 | 4/4, 2 now + 2 after | 1 / 1, Surface giữ nguyên | 51,092 ms | PASS |
| Legacy List regression check | 29760 | 5,484 s | 125 | 1/1 now | 1 / 1, Surface giữ nguyên | 49,697 ms | PASS |
| Sharing flag, fallback không inject | 30058 | 5,344 s | 117 | Không yêu cầu | 1 / 0 | 66,763 ms | PASS cho bỏ qua cấu hình unsupported |

Hai bài injection: **5/5 switch**, 0 frame đen/UNKNOWN, 0 timestamp không tăng ở consumer và output renderer; EGL release đầy đủ. Sau mỗi switch, analyzer yêu cầu ít nhất ba pixel sample đúng nguồn trong một giây sau commit; thực tế mỗi switch có 30 sample. Log xác nhận `executorSame=true callbackSame=true parametersSame=true`. Lượt shared có 117 pixel sample UNKNOWN, không có redirect/engine producer; không coi đây là injection PASS.

Session parameters của consumer là request dựng từ `TEMPLATE_PREVIEW`, không đặt thêm session key đặc biệt; phép kiểm chứng xác nhận giữ cùng object, không chứng minh mọi tổ hợp key/giá trị của ứng dụng khác có hiệu lực giống nhau. Callback được quan sát chạy trên main thread đúng executor của bài test.

Tổng foreground/cleanup các lượt **28,637 giây**; tổng tương tác thiết bị đo được **33,764 giây**, bao gồm tiền kiểm lỗi 2,116 giây, cài APK riêng, push fixture, đọc metadata và kéo diagnostics. Tất cả lượt dưới 60 giây, tổng dưới 300 giây. Harness ban đầu bị trùng tên function với adb, đã dừng trước khi gọi thiết bị; lượt tiền kiểm tiếp theo lỗi resolve executable trước mở camera và được tính trong tổng. Đã sửa dùng `adb.exe` tường minh. Dừng ngay khi đủ bằng chứng, không chạy stability/endurance thêm.

Không quan sát Java/native fatal trong capture AndroidRuntime/libc/DEBUG của các lượt, không đổi PID trong mỗi lượt. Đây là phạm vi quan sát ngắn; không chứng minh không có mọi crash về sau. Native tombstone private không đọc trực tiếp trên máy non-root. `exit-info` được giữ nguyên với các entry force-stop/package-update lịch sử; không gán chúng thành crash mới.

Bằng chứng đầy đủ: [summary + tiêu chí tự động](summary.json), [modern bridge JSONL](modern/bridge.jsonl), [modern pixel JSONL](modern/consumer.jsonl), [modern relevant logcat](modern/relevant-logcat.txt), [PID logcat](modern/pid-logcat.txt), [exit-info](modern/exit-info.txt); thư mục `legacy/`, `shared/` giữ cùng bộ diagnostics. [Build](build.txt), [budget](device-budget.json), [tiền kiểm](preflight-device-budget.json), [hash/config APK](artifact-inventory.json). Không xóa log lỗi hoặc sửa threshold để PASS.

Tái phân tích không cần thiết bị:

```powershell
python scripts/analyze_phase5.py
```

Tái hiện consumer riêng sau build `:app:assembleDebug :consumer-test:assembleDebug`:

```powershell
java -jar .tools/lspatch-v1.2-487-debug.jar consumer-test/build/outputs/apk/debug/consumer-test-debug.apk -o .tools/phase5-consumer -m app/build/outputs/apk/debug/app-debug.apk -k "$env:USERPROFILE\.android\debug.keystore" android androiddebugkey android -d -f
./scripts/verify_phase5.ps1
python scripts/analyze_phase5.py
```

## Quyết định tiếp theo — chưa thực hiện

**Khuyến nghị trước tiên:** yêu cầu hợp đồng camera input/test build có instrumentation từ TikTok/đối tác. Nếu mục tiêu có thể là nhập MP4 hoàn chỉnh, chọn Share Kit là hướng có tài liệu công khai; cần quyết định thay đổi mục tiêu riêng trước khi triển khai. Không thêm media sharing vào phase này.

Nếu không có đường hợp tác và vẫn muốn đánh giá LSPatch, đây là **đề xuất experiment cần quyết định riêng**, không phải lệnh đang chờ chạy tự động:

1. Dùng thiết bị lab riêng không có TikTok production/tài khoản/dữ liệu cần giữ; xác nhận quyền thử nghiệm trên APK. Giữ stock device hiện tại nguyên trạng. Không dùng đổi package ID hoặc signature spoofing để né xung đột cài đặt.
2. Chốt version 47.4.3/470403, hash/certificate toàn bộ base/split nhất quán, ABI và native libraries; kiểm tra cơ chế repack hỗ trợ split. Không patch nếu chưa xác định được bộ cài đúng. Chuẩn bị rollback bản chính thức trên thiết bị lab.
3. Tạo APK thử nghiệm offline bằng loader đã pin, **không module, không signature/integrity bypass**. Việc này sửa APK và certificate, embed loader/runtime; cần chấp nhận rủi ro app không khởi động, mất khả năng update bằng bản chính thức và mất dữ liệu nếu phải gỡ trên thiết bị lab.
4. Gate loader tối đa 60 giây: startup và creator preview chỉ nếu truy cập được mà không cần login/account operations. Thu AndroidRuntime/libc/DEBUG/PID/exit-info. Nếu crash hoặc bị signature/integrity rejection, chỉ phân tích một lượt bounded theo stack rồi dừng; không thử chuỗi version ngẫu nhiên hoặc vượt kiểm tra.
5. Chỉ nếu gate PASS, dùng instrumentation quan sát exact createCaptureSession overload, output configuration, callback/executor, request targets, transform và lifecycle. Chưa inject video ở bước thu cấu hình. Nếu không vào được preview không đăng nhập thì blocker được ghi nhận.
6. Chỉ sau khi cấu hình nằm trong coverage đã kiểm chứng và có quyết định cho injection, thử fixture A/B trong preview ≤60 giây, tối đa 5 switch; tổng mọi lượt thiết bị của execution ≤5 phút. Cần bằng chứng pixel thực sự ở consumer TikTok và session/Surface ổn định; overlay hoặc Surface riêng không đạt. Không record, post hoặc LIVE. Dừng nếu phải bypass hay thêm đường chưa test.

Hiện chưa thể chọn bước 3–6 như một integration được hỗ trợ. **Blocker chính là quyền truy cập hợp tác/instrumentation vào process TikTok**, tiếp theo là loader trên đúng APK/splits và cấu hình camera thực tế. Thêm tính năng renderer hoặc chạy test lâu hơn không giải quyết các blocker đó. Orientation/lens changes, nhiều mode/output, runtime integrity, audio và LIVE tiếp tục chưa xác minh.
