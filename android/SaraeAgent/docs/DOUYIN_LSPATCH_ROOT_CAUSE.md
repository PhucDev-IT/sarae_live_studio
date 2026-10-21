# Douyin 40.8.0 / LSPatch 1.2 (487): điều tra native crash

Ngày phân tích: 2026-10-20. Thiết bị trong log: Samsung SM-G996U, Android 14, arm64; package `com.ss.android.ugc.aweme`. Phân tích chỉ dùng APK và log đã lưu tại `android/test-results/douyin-40.8.0-20261020/` (viết tắt `R/` dưới đây). Không cài/gỡ ứng dụng, không chạy LIVE, không thay đổi source hay cơ chế bảo vệ của Douyin.

## Kết luận

- **CONFIRMED:** Douyin 40.8.0 vá bằng LSPatch **không nhúng module** đã crash ở PID 12428 sau 41 giây; bản nhúng VCAM crash ở PID 22766 sau 34 giây. Cả hai là `SIGSEGV/SEGV_ACCERR`, cùng thread name `Thread-8`, cùng frame `libart.so` offset `0x2f09c4` (`art::JavaVMExt::VisitRoots`) và `libmetasec_ml.so` offset `0x1f1ba0`, cùng Build ID tương ứng. VCAM không phải điều kiện cần để tái hiện lỗi. Bằng chứng: `R/patched-no-module/crash-after-40s.txt:2-26`, `R/patched-crash-buffer.txt:106-130`.
- **CONFIRMED:** Douyin gốc hoàn thành bài thử ngắn 8 cuộn + 4 đổi tab, PID 3452 ổn định; TikTok 47.4.3 dùng cùng module và LSPatch hoàn thành bài thử ngắn tương tự, PID 9110 ổn định. Đây là đối chứng ngắn, không chứng minh ổn định dài hạn. Bằng chứng: `android/SaraeAgent/docs/DOUYIN_CRASH_ISOLATION_REPORT.md`, `R/comparison/*-actions-pids.csv`.
- **LIKELY:** Vấn đề tương thích xuất hiện khi Douyin chạy trong môi trường do LSPatch tạo ra (bootstrap/ART hook, AppComponentFactory thay thế, classloader và native namespace khác). Dữ liệu hiện có **không định vị được cơ chế gây hỏng cụ thể** trong ART, LSPatch hay thư viện Douyin.
- **UNKNOWN:** Có hay không việc kiểm tra tính toàn vẹn hoặc phát hiện can thiệp dẫn đến crash. Frame `libmetasec_ml.so` và dòng `HookDetectModule` không tự chứng minh điều đó; Douyin gốc cũng log `HookDetectModule` (`R/comparison/douyin-original-logcat.txt:246539`). Không đề xuất vô hiệu hóa các cơ chế này.

## APK/DEX, manifest và native libraries

Số liệu tĩnh từ `R/analysis/apk_inventory.tsv` và bản in manifest trong `R/analysis/manifest-*.xml`. SHA-256 của APK gốc là `0c693b17830e7a799b8c4a9e3d736b3402525c54c54aad13274fe974ec821d43`.

| Thuộc tính | Douyin gốc | LSPatch 487 + VCAM | LSPatch 487 không module | Kết luận |
|---|---|---|---|---|
| APK SHA-256 | `0c693b17…ec821d43` | `0c923661…93299b7` | `fc4fc44d…38c363` | **CONFIRMED:** file đầu ra khác |
| DEX ở root APK | 56 | 1 (`classes.dex` bootstrap) | 1 (`classes.dex` bootstrap) | **CONFIRMED** |
| APK gốc nhúng | Không áp dụng | `assets/lspatch/origin.apk` | giống bên trái | **CONFIRMED:** byte SHA-256 của origin nhúng trùng APK gốc ở cả hai bản vá |
| Native `.so` | 267 | 271 | 271 | **CONFIRMED:** cả 267 entry `.so` gốc có cùng đường dẫn và SHA-256; chỉ thêm 4 bản `assets/lspatch/so/*/liblspatch.so` |
| `libmetasec_ml.so` | SHA-256 `1fe03703…bc027c` | cùng byte | cùng byte | **CONFIRMED:** không phải do file thư viện này bị đổi byte trong APK |
| `Application` | `AwemeHostApplication` | giống gốc | giống gốc | **CONFIRMED** |
| `AppComponentFactory` | `AwemeAppComponentFactory` | `LSPAppComponentFactoryStub` | `LSPAppComponentFactoryStub` | **CONFIRMED:** manifest gốc dòng 1485, bản vá dòng 1486 |
| min/target SDK | 23 / 34 | 28 / 34 | 28 / 34 | **CONFIRMED:** manifest dòng 13-14 |
| Config LSPatch | Không có | `useManager=false`, `sigBypassLevel=0`, `injectDex=false`, API 102, version 1.2/487, core `canary-3106` | cùng config, không có module asset | **CONFIRMED:** `assets/lspatch/config.json`; không có bằng chứng signature bypass được bật |

`extractNativeLibs=true` và tên `Application` giữ nguyên. Bản `--injectdex` có 57 DEX ở root, `injectDex=true`, cùng 267 thư viện gốc, nhưng phát sinh lỗi Java/Kotlin khác; không được gộp lỗi này với native SIGSEGV. NPatch 1.0.9 giữ `AppComponentFactory` gốc trong manifest nhưng chỉ có 2 DEX ở root và thất bại với `ClassNotFoundException` cho `AwemeHostApplication`; đó cũng là lỗi khác.

## Timeline khởi tạo (giờ địa phương +07, PID của tiến trình app)

Các dòng nguồn có số dòng ở `R/analysis/timeline-key-events.tsv`; file đó dẫn về logcat đầy đủ. Mốc `nativeloader ... ok` xác nhận tải thư viện, **không** xác nhận `JNI_OnLoad` riêng của `libmetasec_ml.so` đã được gọi hoặc thành công. Dòng `SoLoader: Success:The JNI_OnLoad method initialized successfully` không nêu tên thư viện, nên không gán nó cho `libmetasec_ml.so`.

| Sự kiện | Gốc PID 3452 | LSPatch + VCAM PID 22766 | LSPatch rỗng PID 12428 | Trạng thái |
|---|---|---|---|---|
| Factory/bootstrap | 18:33:44.957 factory gốc được tạo | 18:26:30.745 MetaLoader; 30.766 `liblspatch.so` namespace `clns-5`; 31.782 cảnh báo không thấy factory gốc | 18:42:57.963 MetaLoader; 57.984 `liblspatch.so` `clns-5`; 18:43:01.459 cùng cảnh báo | **CONFIRMED** |
| DEX/classloader | caller DEX trực tiếp trong `base.apk` | 18:26:34.845 VCAM thấy `PathClassLoader` trỏ `cache/lspatch/origin/1104752646.apk`; 18:26:34.871 bootstrap xong | 18:43:05.326 bootstrap xong; caller native sau đó trỏ origin cache tương tự | **CONFIRMED:** đường DEX; **UNKNOWN:** parent hierarchy đầy đủ |
| `libmetasec_ml.so` | 18:33:45.739 từ `.../lib/arm64/`, ns `clns-5`, caller `base.apk!classes11.dex` | 18:26:37.345 cùng tên thư viện từ `.../lib/arm64/`, ns `clns-6`, caller origin cache `!classes11.dex` | 18:43:07.584 cùng mẫu `clns-6`, caller origin cache | **CONFIRMED:** namespace/caller khác; **UNKNOWN:** tác động lên JNI |
| `AwemeHostApplication.onCreate` | 18:33:45.543 xuất hiện trong stack Java được log | 18:26:38.725 stack có `AwemeHostApplication.onCreate` và `LSPatch_.callApplicationOnCreate` | 18:43:08.825 xuất hiện trong stack Java | **CONFIRMED:** có dấu vết gọi; không suy ra toàn bộ lifecycle hoàn tất |
| Plugin classloader | 18:33:45.637 cảnh báo unknown path; dynamic plugin còn tải về sau | 18:26:39.285 cảnh báo `DyMiraClassLoader` unsupported | 18:43:09.192 cùng cảnh báo | **CONFIRMED:** cảnh báo; **UNKNOWN:** parent/namespace/plugin binary tương đương |
| Native khác | 18:33:45.964 `vcnnetwork` JNI; 46.011 `mdl2` JNI; 46.276 `libttmverify` `clns-5`; 46.980 `HookDetectModule` JNI | 18:26:39.668/39.717 `vcnnetwork`/`mdl2`; 43.285 `libttmverify` `clns-6`; 47.923 `HookDetectModule` JNI | 18:43:09.439/09.463; 12.601 `libttmverify` `clns-6`; 16.514 `HookDetectModule` JNI | **CONFIRMED:** các mốc log; không chứng minh thư viện nào khởi phát crash |
| Kết quả | 8 cuộn + 4 tab, PID giữ | 18:27:02.694 SIGSEGV | 18:43:36.369 SIGSEGV | **CONFIRMED** |

Ở bản có VCAM, `VectorModuleClassLoader` xuất hiện 18:26:32.277; bản rỗng không có module asset và không có `VcamDouyin` log. `Modules initialized` ở bản rỗng chỉ là log chung của loader, không có nghĩa VCAM đã nạp. Cả bản gốc lẫn bản vá đều có cảnh báo classloader/plugin; cần đối chiếu đúng PID và mốc giờ trước khi xem cảnh báo nào là đặc thù. `HookDetectModule` xuất hiện ở bản gốc, nên không xem riêng dòng này là bằng chứng chống vá. Log sau thời điểm fatal signal có thể do các thread còn ghi trước khi tiến trình chết; không gán chúng làm nguyên nhân crash.

## Tombstone: so sánh hai crash 40.8.0

| Trường | Có VCAM | Không module | Đánh giá |
|---|---|---|---|
| Signal/code | `SIGSEGV` (11), `SEGV_ACCERR` (2) | giống | **CONFIRMED** |
| PID/TID/thread/uptime | 22766 / 22885 / `Thread-8` / 34s | 12428 / 12553 / `Thread-8` / 41s | **CONFIRMED** |
| Fault address / `pc` / `x23` | `0x7377d099c4` (ba giá trị bằng nhau) | `0x737d7049c4` (ba giá trị bằng nhau) | **CONFIRMED**; địa chỉ tuyệt đối khác nhau, có thể do ánh xạ khác nhau (**LIKELY**), chưa có memory maps để chứng minh |
| Frame #00 | `libart.so` `art::JavaVMExt::VisitRoots+0`, pc offset `0x2f09c4`, Build ID `cecb684d19b4f44ee3cbd66142c315f4` | giống | **CONFIRMED** |
| Frame #01 | `libmetasec_ml.so` pc offset `0x1f1ba0`, Build ID `5f4a3bbfda0d8f672de2148f3f0225284adcef05` | giống | **CONFIRMED** |
| Registers | Có x0-x29, lr/sp/pc/pst trong crash buffer | Có cùng tập trường; giá trị phần lớn khác | **CONFIRMED** |
| Frame sâu hơn / maps | `2 total frames`, không có memory maps trong bản logcat lưu | giống | **CONFIRMED:** thiếu dữ liệu, không được tái dựng call chain |

Độ tương đồng của signal, thread name, hai frame/offset và Build ID là rất cao; đây là cùng **crash fingerprint** (**CONFIRMED**), chưa chứng minh cùng một lỗi bộ nhớ ở tầng thấp (**UNKNOWN**). Frame #00 là nơi CPU fault, frame #01 là caller được unwinder thấy; không đủ để quy trách nhiệm trực tiếp cho `libmetasec_ml.so`, ART hay LSPatch. `SEGV_ACCERR` là lỗi quyền truy cập bộ nhớ; không có map nên chưa phân biệt chính xác trang code/data hay quyền trang. Không có frame trước #00 hoặc sau #01. `libmetasec_ml.so + 0x1f1ba0` là offset trong backtrace, chưa có symbol nội bộ để đặt tên hàm. Tombstone cũ ở 18:12 thuộc Douyin 33.6.0 (Build ID/offset `libmetasec_ml.so` khác), không trộn vào so sánh 40.8.0.

## Phân loại giả thuyết và các loader khác

| Giả thuyết / lỗi | Bằng chứng và giới hạn | Kết luận |
|---|---|---|
| VCAM hoặc source merge là điều kiện cần gây crash | Bản rỗng không có module vẫn crash cùng fingerprint | **CONFIRMED: bác bỏ điều kiện cần**; module có thể đổi thời gian crash nhưng chưa chứng minh |
| Tương tác giữa bootstrap/hook ART của LSPatch và luồng native của Douyin | `liblspatch.so` nạp trước app; bản gốc chạy bài thử ngắn; hai bản vá crash trong `VisitRoots`, cùng caller `libmetasec_ml.so` | **LIKELY** là vùng tương thích cần điều tra, **UNKNOWN** cơ chế trực tiếp |
| Đổi classloader/namespace gây lỗi JNI cụ thể | Caller `base.apk!classes11.dex`/`clns-5` đổi thành origin cache/`clns-6`; lỗi đến sau native load | **CONFIRMED:** có đổi; **UNKNOWN:** quan hệ nhân quả |
| `AppComponentFactory` warning gây crash | Warning có ở cả Douyin vá và TikTok vá chạy qua bài thử ngắn; issue upstream ghi nhận warning này ở app khác | **UNKNOWN** vai trò với Douyin; warning đơn lẻ không đủ kết luận |
| Native `.so` gốc bị sửa/hỏng | 267/267 entry `.so` có cùng SHA-256 giữa APK gốc và các bản LSPatch | **CONFIRMED:** không có khác biệt byte ở các entry này; không loại trừ tác động lúc runtime |
| `HookDetectModule` chứng minh ứng dụng cố phát hiện bản vá | Cũng có ở Douyin gốc PID 3452 | **UNKNOWN:** không có bằng chứng nhân quả hoặc chủ ý |
| `--injectdex` | `R/patched-injectdex-no-module/process-20884-logcat.txt`: `NoSuchMethodError` cho `ArraysKt.sliceArray(Object[], IntRange)` trong `VectorNativeHooker.callback`, `ArraysKt` từ `base.apk!classes54.dex`; kẹt splash | **CONFIRMED:** xung đột/thiếu API Kotlin được quan sát ở đường loader này; không phải cùng SIGSEGV |
| NPatch 1.0.9 | `R/npatch-1.0.9/no-module/crash-after-50s.txt`: `ClassNotFoundException` cho `AwemeHostApplication` | **CONFIRMED:** lỗi Java/classloader riêng, không xác nhận nguyên nhân LSPatch |

Upstream để đối chiếu thiết kế, **không** coi là bằng chứng root cause của APK 1.2/487 đang dùng:

- [LSPatch source: đóng gói config, metaloader DEX, native loader và APK gốc](https://github.com/LSPosed/LSPatch/blob/master/patch/src/main/java/org/lsposed/patch/LSPatch.java). Repo cũ đã archive; cấu hình APK tại chỗ ghi core `canary-3106`, nên không giả định source cũ trùng byte với bản 487.
- [LSPatch issue #108: `Original AppComponentFactory not found`](https://github.com/LSPosed/LSPatch/issues/108) ghi nhận cùng loại cảnh báo ở app khác, không cung cấp kết luận về Douyin.
- [Vector discussion #513 về hai classloader của `LoadedApk` và factory](https://github.com/JingMatrix/Vector/discussions/513) giải thích vì sao cần phân biệt default/app classloader; không chứng minh hierarchy thực tế của Douyin trong log hiện có.

## Dữ liệu còn thiếu và bước xác minh ít rủi ro

1. **UNKNOWN:** Không có file tombstone đầy đủ từ `/data/tombstones` hoặc bugreport có memory maps, vùng bảo vệ tại fault address, full unwind/thread dump. Logcat lưu chỉ ghi 2 frames. Thiếu symbols/debug build cho `libmetasec_ml.so + 0x1f1ba0` và thông tin disassembly/mapping để xác định lệnh fault.
2. **UNKNOWN:** Chưa có snapshot parent chain của default/app/plugin classloader, native library search path của từng loader ở cả hai bản, hay đối chiếu binary plugin Mira được tải động. `nativeloader` cho biết namespace/caller của một số load, chưa đủ để kết luận toàn bộ dependency/linker state.
3. **UNKNOWN:** Không có log gắn rõ `JNI_OnLoad` của `libmetasec_ml.so` với return value; log `SoLoader` chung không đủ. Chưa có trace gọi vào `VisitRoots` hoặc trạng thái root table trước crash.
4. **Ít rủi ro nhất:** Tiếp tục phân tích **offline** các APK/log đã lưu; nếu bugreport hoặc tombstone từ lần crash trước còn lưu, chỉ đọc và symbolicate chúng. Chỉ khi dữ liệu đó không còn và thật sự cần tái hiện, lập kế hoạch riêng trên thiết bị thử nghiệm, thông báo thao tác/rủi ro cho người dùng trước; không thay bản Douyin gốc hiện tại, không đăng nhập/LIVE, không thử patcher ngẫu nhiên, không can thiệp bảo vệ hay chữ ký.

**Root cause cuối cùng: UNKNOWN.** Nguyên nhân gần nhất quan sát được là access fault ở `libart.so/VisitRoots` khi stack có `libmetasec_ml.so + 0x1f1ba0` trong môi trường LSPatch, độc lập với module. Chưa thể kết luận thành phần nào làm sai trạng thái bộ nhớ hoặc vì sao. Điều kiện chấp nhận một hướng sửa sau này: bản vá **không module** phải vượt qua khởi động và bài thử feed/tab có log đối chứng trước khi kiểm tra VCAM; hiện chưa đạt điều kiện đó.

## Bổ sung: lần chạy debug sau khi người dùng bấm Đồng ý (2026-10-20, 19:37–19:40)

APK LSPatch 1.2/487 **debug** có VCAM, SHA-256 `E65FB8969B60583A00A2B9C9A1AF7FF90CFC83932B2B14A568B5CEDF5AEB47B6`, được cài trên SM-G996U theo yêu cầu của người dùng. Log: `R/diagnostic-debug-487/after-consent-logcat-20261020-194139.txt` và `R/diagnostic-debug-487/dropbox-native-crash.txt`.

- **CONFIRMED:** PID 20021 khởi động 19:37:09.809, LSPatch bootstrap hoàn tất 19:37:13.515, VCAM ghi nhận classloader 19:37:13.487. Người dùng cho biết hộp thoại nguyên tắc bảo vệ thông tin cá nhân được để mở, khi ấy app không crash. Log thấy một thao tác chạm ở `SplashActivity` 19:40:24.917–24.987 (dòng 300005/300017); log không ghi nhãn nút, nên chỉ **LIKELY** đây là lần bấm Đồng ý do người dùng mô tả.
- **CONFIRMED:** Sau lần chạm, cửa sổ Splash thay đổi lúc 19:40:25; PID có nhiều hoạt động UI/media hơn, `libbdheif.so` được nạp lúc 19:40:31.298 (dòng 310183), `AudioTrack` báo underrun lúc 19:40:38.783 (dòng 312829), và OpenGLRenderer báo khung hình chậm 1077 ms lúc 19:40:38.810 (dòng 312863). Đây là dấu hiệu lag quan sát được, không đủ chứng minh thành phần media gây crash.
- **CONFIRMED:** `NPTH` log “Native crash has been detected!” lúc 19:40:38.788 (dòng 312848); fatal signal lúc 19:40:39.024 (dòng 312948). Crash cách lần chạm khoảng 14.1 giây, trong khi PID tồn tại khoảng 3 phút 29 giây từ bootstrap. Tombstone Dropbox 19:40:40 ghi PID 20021/TID 20136 `Thread-8`, `SIGSEGV/SEGV_ACCERR`, fault/pc/x23 `0x737b2c79c4`; frame #00 `libart.so VisitRoots +0` offset `0x2f09c4`, frame #01 `libmetasec_ml.so` offset `0x1f1ba0`, cùng Build ID như hai crash 40.8.0 trước (Dropbox dòng 487–523). Vẫn chỉ có 2 frames; không có memory maps.
- **LIKELY:** Việc đóng hộp thoại cho phép luồng khởi tạo feed/media tiếp tục, và crash bộc lộ trong giai đoạn này. **UNKNOWN:** thao tác Đồng ý có trực tiếp gây lỗi bộ nhớ hay chỉ làm tiến trình chạy đến đường code vốn lỗi với LSPatch. Douyin gốc trước đây đã qua bài thử feed/tab ngắn sau khi vào app; bản LSPatch không module cũng có cùng fingerprint, nên chưa có căn cứ quy lỗi cho VCAM hoặc kết luận cơ chế bảo vệ chủ động gây crash.

**Trạng thái:** Bản debug không khắc phục crash; nó bổ sung mốc kích hoạt và cùng fingerprint. Không thay đổi module hoặc camera hook theo suy đoán từ mốc này.

