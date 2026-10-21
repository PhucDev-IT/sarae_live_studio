# Douyin 40.8.0 — phân tách lỗi crash khi vá (2026-10-20)

Thiết bị: Samsung SM-G996U, Android 14, ADB `RFCR10DGF1T`. Không root, không mở LIVE. Tất cả các phép thử tương tác chỉ gồm mở app, cuộn feed và đổi tab; không quay/phát LIVE.

## Kết luận đã kiểm chứng

**Chưa có bản Douyin vá hoạt động.** Douyin 40.8.0 gốc và TikTok 47.4.3 đã vá đều chạy qua phép thử cuộn/tab. Douyin 40.8.0 đóng gói bằng LSPatch 1.2/487 **không nhúng module nào** vẫn crash cùng native stack với bản nhúng VCAM. Vì vậy module VCAM và phần merge source không phải điều kiện cần gây crash. Không đủ bằng chứng để kết luận Douyin cố ý phát hiện bản vá hoặc xác định chính xác lỗi trong ART, native core hay thư viện ứng dụng.

| Cấu hình | Thao tác / thời gian | Kết quả | Log |
|---|---|---|---|
| TikTok 47.4.3 baseline trên máy, SHA-256 `A3FE71E19E013B0963FE22BE92BC614E5AB4EE7F8A7266314139DC37615D5FBA` | 8 cuộn, 4 đổi tab | PASS ngắn: PID 592 giữ nguyên | `comparison/tiktok-actions-pids.csv`, `comparison/tiktok-logcat.txt` |
| TikTok 47.4.3 vá lại bằng đúng module thử Douyin (`VCAMInjection-Douyin-debug.apk`, SHA-256 `0BE629EFCA1872B33ECA172EA76BA48033223BEFA4A04F0EC50530BF11FF5B8C`), LSPatch 1.2/487, sig bypass 0 | Mở app + 8 cuộn + 4 đổi tab | PASS ngắn: PID 9110 giữ nguyên; sau đó cài đè trở lại baseline, hash trên máy khớp bản sao | `comparison/tiktok-current-module-actions-pids.csv`, `comparison/tiktok-current-module-logcat.txt` |
| Douyin 40.8.0 gốc, SHA-256 `0C693B17830E7A799B8C4A9E3D736B3402525C54C54AAD13274FE974EC821D43` | 8 cuộn, 4 đổi tab | PASS ngắn: PID 3452 giữ nguyên | `comparison/douyin-original-actions-pids.csv`, `comparison/douyin-original-logcat.txt` |
| Douyin 40.8.0 + LSPatch 1.2/487 + module VCAM, sig bypass 0 | Khởi động | FAIL: PID 22766 `SIGSEGV` sau ~34 giây; `libart.so art::JavaVMExt::VisitRoots` + `libmetasec_ml.so` offset `0x1f1ba0` | `patched-launch-logcat.txt`, `patched-crash-buffer.txt` |
| Douyin 40.8.0 + cùng LSPatch, **không module**, sig bypass 0 | Khởi động | FAIL: PID 12428 `SIGSEGV` sau ~41 giây, cùng hai frame native và cùng offset `libmetasec_ml.so` | `patched-no-module/full-logcat.txt`, `patched-no-module/crash-after-40s.txt` |
| Douyin 40.8.0 + LSPatch `--injectdex`, không module, sig bypass 0 | Khởi động | FAIL: PID 20884 kẹt tại logo; `VectorNativeHooker.callback` ném `NoSuchMethodError` cho `kotlin.collections.ArraysKt.sliceArray(Object[], IntRange)` do class `ArraysKt` được nạp từ Douyin `classes54.dex` | `patched-injectdex-no-module/process-20884-logcat.txt`, `patched-injectdex-no-module/screen-after-50s.png` |
| Douyin 40.8.0 + NPatch 1.0.9/833, không module, sig bypass 0 | Khởi động | FAIL: PID 26160 `ClassNotFoundException` cho `com.ss.android.ugc.aweme.app.host.AwemeHostApplication` trong `base.apk`, trước khi vào feed | `npatch-1.0.9/no-module/crash-after-50s.txt`, `npatch-1.0.9/no-module/full-logcat.txt` |

Cảnh báo `Original AppComponentFactory not found` cũng xuất hiện trên TikTok vá chạy ổn trong phép thử; không thể coi cảnh báo này là nguyên nhân Douyin crash. Kết quả TikTok và Douyin gốc là kiểm tra ngắn theo thao tác đã nêu, không chứng minh ổn định lâu dài. Không có camera preview với bản Douyin vá, nên Camera2 injection và audio Douyin vẫn **UNVERIFIED**.

## Trạng thái cuối trên điện thoại

- Douyin: **APK gốc 40.8.0** được cài lại; `sha256sum` của APK đang cài khớp `0C693B17...EC821D43`. App mở được; người dùng đã cho phép đồng ý điều khoản nên đã chọn Đồng ý. Không cấp quyền thông báo; không thao tác đăng nhập. Lần gỡ/cài làm mất dữ liệu riêng của bản cài trước, gồm trạng thái đăng nhập nếu lưu trong app.
- TikTok: **APK baseline 47.4.3** được cài đè trở lại; `sha256sum` trên máy khớp `A3FE71E1...5D5FBA`. Không gỡ package TikTok nên dữ liệu app không bị xóa bởi phép thử này.
- VCAM Injection source: không thay đổi trong lượt phân tích này. Không commit/push, không sửa camera hook đang chạy trên TikTok.

## Hướng tiếp theo

Lỗi hiện nằm trước việc quan sát callback camera. Không sửa module VCAM dựa trên các native tombstone này. Cần làm cho một bản Douyin **không module** vượt qua khởi động, feed, cuộn và đổi tab trước; sau đó mới nhúng module và kiểm tra camera preview. `injectDex` cần cách ly hoặc sửa dependency Kotlin trong loader; NPatch 1.0.9 cần sửa đường nạp class gốc. Hai hướng này đòi hỏi chỉnh patcher, kiểm thử trên thiết bị/emulator cô lập và chứng minh bằng log trước khi thay bản Douyin người dùng. Không dùng LIVE để debug, không kết luận audio LIVE hoạt động.

NPatch JAR được tải từ [release chính thức v1.0.9](https://github.com/7723mod/NPatch/releases/tag/v1.0.9); SHA-256 tải về `D7E69D464B90AA888A5D555B94D38DAB94A4AAB19D80F863D238B6715A0FCEA3` khớp digest từ GitHub API. Không bật `--hidelibs` hay signature bypass.

Tất cả APK/log/screenshot thử nghiệm nằm trong `android/test-results/douyin-40.8.0-20261020/` (Git ignored). Báo cáo lần vá đầu: `DOUYIN_40_8_0_PATCH_ATTEMPT.md`.