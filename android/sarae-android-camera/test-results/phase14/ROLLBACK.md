# Rollback cache module về Phase 11, giữ APK/account

Đây là hướng dẫn dự phòng, **chưa thực thi**. Không cần reinstall APK, clear data hoặc uninstall.

1. Chỉ rollback khi người dùng không quay/phát LIVE hoặc thao tác TikTok; đưa máy về HOME, xác minh CameraService không có active client. Không force-stop app khi còn hoạt động người dùng.
2. Kiểm hash local `.tools/phase14/rollback-module.apk` phải là `2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0`. Module cached hiện hành phải là Phase 14 `53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351`; nếu khác, dừng để không ghi đè một update mới hơn.
3. Sau khi xác minh các điều kiện trên, dùng ADB có timeout 3–8 s mỗi lệnh, theo đúng procedure cache cũ:

```text
adb shell am force-stop com.ss.android.ugc.trill
adb push .tools/phase14/rollback-module.apk /data/local/tmp/sarae-phase14-rollback.apk
adb shell run-as com.ss.android.ugc.trill cp /data/local/tmp/sarae-phase14-rollback.apk cache/lspatch/co.sarae.camera.poc/183496786.apk.restore
adb shell run-as com.ss.android.ugc.trill sha256sum cache/lspatch/co.sarae.camera.poc/183496786.apk.restore
```

4. Chỉ nếu hash staged file đúng Phase 11, activate bằng rename:

```text
adb shell run-as com.ss.android.ugc.trill mv cache/lspatch/co.sarae.camera.poc/183496786.apk.restore cache/lspatch/co.sarae.camera.poc/183496786.apk
```

5. Người dùng mở app bình thường; xác minh cache hash và runtime marker trước test khác. Không tự bắt đầu LIVE. Thao tác này khôi phục code Phase 11, không chứng minh Phase 11 tương thích LIVE.

Cache có thể bị hệ điều hành/app xóa; installed base vẫn embed module Phase 10. Đây là lab cache deployment, không phải cơ chế update bền vững. Phải kiểm hash/load trước mọi lượt kiểm chứng sau.
