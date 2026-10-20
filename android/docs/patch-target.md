# Hướng dẫn vá app đích bằng LSPatch (không cần root)

## Bộ tệp

Đặt các tệp sau cùng một thư mục trên máy tính:

- `lspatch-v1.2.jar`: công cụ vá APK.
- `VCAMPro-debug.apk`: module VCAM đã build.
- APK gốc của app đích, ví dụ TikTok 47.4.3.

Java 17 và Android Platform Tools (`adb`) cần có trong PATH. Điện thoại bật USB debugging.

## Vá TikTok 47.4.3 bằng PowerShell

Ví dụ APK gốc đang ở Downloads:

```powershell
$target = 'C:\Users\nguye\Downloads\TikTok+-+Videos,+Shop+&+LIVE_47.4.3_APKPure.apk'
New-Item -ItemType Directory -Force .\patched | Out-Null
java -jar .\lspatch-v1.2.jar -m .\VCAMPro-debug.apk -o .\patched $target
```

LSPatch sẽ tạo APK đã nhúng module trong thư mục `patched`. Dùng đúng tên file mà công cụ vừa tạo ở bước cài đặt:

```powershell
adb install .\patched\<ten-file-lspatched.apk>
```

Nếu Android báo `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, bản APK đã vá có chữ ký khác ứng dụng đang cài. Gỡ bản hiện tại rồi cài APK mới:

```powershell
adb uninstall com.zhiliaoapp.musically
adb install .\patched\<ten-file-lspatched.apk>
```

Gỡ cài đặt sẽ xóa dữ liệu cục bộ của app, có thể bao gồm phiên đăng nhập và bản nháp. Đăng nhập lại sau khi cài. Chỉ dùng APK nguồn bạn có quyền sử dụng; giữ riêng dữ liệu đăng nhập.

## Dùng app quản lý video

1. Cài `VCAM-Video-Manager.apk` lên điện thoại.
2. Mở app, cấp quyền **Truy cập tất cả các file** nếu Android yêu cầu.
3. Chọn một hoặc nhiều video; dùng mũi tên để sắp thứ tự và bật lặp nếu cần.
4. Nhấn **Áp dụng cho TikTok**.
5. Đóng rồi mở lại màn hình camera TikTok.

Module đọc các file theo thứ tự `DCIM/Camera1/playlist/001.mp4`, `002.mp4`... và phát lặp danh sách khi chế độ lặp được bật.

## Phạm vi tương thích

`VCAMPro-debug.apk` đi kèm đã cấu hình cho TikTok package `com.zhiliaoapp.musically` và hook các đường camera đã kiểm chứng trên TikTok 47.4.3. Bản Direct Audio thay PCM trả về từ các overload `AudioRecord.read` (`ByteBuffer`, `byte[]`, `short[]`) bằng track âm thanh của playlist khi TikTok bắt đầu ghi hình. Nó không phát tiếng clip qua loa; âm thanh micro trực tiếp bị thay bằng âm thanh nguồn. Đã xác nhận trên Galaxy S22 Ultra: TikTok gọi `AudioRecord.read(ByteBuffer, int)`, module nạp audio playlist 44.1 kHz stereo và người dùng xác nhận video quay lại có tiếng. Livestream cần kiểm tra riêng vì có thể sử dụng đường thu WebRTC/native khác.

LSPatch chỉ nhúng module vào APK; nó không tự tạo hỗ trợ cho ứng dụng camera bất kỳ. Muốn dùng với app khác cần thêm package đó vào phạm vi module, kiểm tra API camera và API thu âm mà app dùng, rồi build lại module và thử trên đúng phiên bản app đó.

Không cần Magisk hoặc quyền root. Đây là cài APK đã chỉnh sửa ngoài cửa hàng; cập nhật app chính thức có thể ghi đè bản thử nghiệm và cần vá lại.
