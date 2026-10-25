# Tái hiện trên thiết bị thử nghiệm của dự án

Từ gốc `android/sarae-android-camera`, dùng JDK 21/Android SDK. Tải đúng `lspatch-v1.2-487-debug.jar` từ release được dẫn trong báo cáo, đặt dưới `.tools/` và so SHA-256 trước khi dùng. `.tools/` và APK build không được đưa vào Git.

```powershell
./gradlew.bat :app:assembleDebug :consumer-test:assembleDebug
# Gate 1, thực hiện trước khi nhúng module. -d giữ test-intent của app debug.
java -jar .tools/lspatch-v1.2-487-debug.jar app/build/outputs/apk/debug/app-debug.apk -o .tools/patched-empty -d
adb install -r .tools/patched-empty/app-debug-487-lspatched.apk
```

Patcher mặc định ký bằng key riêng. Nếu đã cài app debug như trong lượt báo cáo, dùng `-k <debug.keystore> android androiddebugkey android` để giữ cùng signer; không gỡ app khi chưa lưu bằng chứng. Chạy workflow A/B Phase 1 >=120 giây với PID polling và thu `adb logcat --pid=<PID> -v threadtime`. Chỉ khi gate PASS mới chạy:

```powershell
java -jar .tools/lspatch-v1.2-487-debug.jar consumer-test/build/outputs/apk/debug/consumer-test-debug.apk -o .tools/patched-consumer -m app/build/outputs/apk/debug/app-debug.apk -d
adb install -r .tools/patched-consumer/consumer-test-debug-487-lspatched.apk
adb shell pm grant co.sarae.camera.consumer android.permission.CAMERA
adb shell mkdir -p /sdcard/Android/data/co.sarae.camera.consumer/files
adb push test-fixtures/A.mp4 /sdcard/Android/data/co.sarae.camera.consumer/files/A.mp4
adb push test-fixtures/B.mp4 /sdcard/Android/data/co.sarae.camera.consumer/files/B.mp4
adb shell am start -n co.sarae.camera.consumer/.ConsumerActivity
adb shell pidof co.sarae.camera.consumer
adb shell am broadcast -a co.sarae.camera.TEST_SWITCH -p co.sarae.camera.consumer --es source B
adb shell am broadcast -a co.sarae.camera.TEST_SWITCH -p co.sarae.camera.consumer --es source A
```

Chờ 3 giây giữa các broadcast, lặp 12 switch, giữ app foreground ít nhất 120 giây. Thu logcat PID gồm SaraeConsumer/SaraeBridge, `adb logcat -d -b crash`, `adb logcat -d -s AndroidRuntime:E libc:F DEBUG:F`, `adb shell dumpsys activity exit-info co.sarae.camera.consumer`. Không xóa logcat toàn máy. Đặt tên log theo file báo cáo rồi chạy `python scripts/analyze_phase2.py`; inventory được tạo bởi `python scripts/phase2_inventory.py`. Lượt đối chứng chạy consumer APK gốc trước patch và ghi pixel physical riêng.
