`api-82.jar` là Xposed API 82 compile-only, dùng cho module legacy trong thử nghiệm LSPatch. Byte được lấy từ bản API đã có tại `obs-playback-stability/android/VCAMInjectionModule/app/libs/api-82.jar` trong monorepo; không lấy hook implementation. Hash được lưu trong `test-results/phase2/artifact-inventory.json`.

API classes được loader cung cấp lúc chạy; Gradle không đóng gói jar này vào app. Tham chiếu API upstream: https://api.xposed.info/reference/packages.html.
