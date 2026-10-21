# Douyin 33.6.0 — Camera/Audio compatibility study

Ngày kiểm tra: 2026-10-20 (Asia/Saigon). Phạm vi: đọc metadata/DEX/native libraries của APK đang cài và quan sát **camera preview do người dùng tự mở**. Không patch, cài lại, root, quay hay mở LIVE. Source của Android Agent/injection chỉ được đọc.

## Kết luận ngắn

- **A — Camera2: CONFIRMED cho preview đang quan sát.** `VideoRecordNewActivity` ở foreground và Android CameraService báo client `com.ss.android.ugc.aweme` qua `CameraDeviceClient[0]` (camera ID 0, PID 4640). DEX còn chứa `TECameraServer`, `VECameraCapture` và wrapper Camera2. Điều này ủng hộ việc tái sử dụng điểm hook Camera2 cấp Android, nhưng **chưa xác minh** thay Surface bằng MP4 trên Douyin.
- **B — Audio callback: CONFIRMED ở mức DEX, UNKNOWN ở runtime/LIVE.** Douyin có `com.ss.android.ttve.nativePort.TEAudioDataInterface.onReceive(Lcom/ss/android/vesdk/audio/VEAudioSample;)V`, đúng cặp class/method TikTok đã khảo sát. Douyin cũng có nhánh audio riêng `com.ss.avframework.capture.audio.AudioCapturerAudioRecord`/`AudioCapturerOpensles` và `livestreamv2.core.audiorecord.AudioRecordManager`. Không có bằng chứng `TEAudioDataInterface.onReceive` được gọi khi preview hoặc là đường microphone của LIVE.
- **C — Tái sử dụng: LIKELY cho logic Camera2 và bộ giải mã PCM; UNKNOWN cho injection chạy thực tế trên Douyin và LIVE audio.** Module hiện tại yêu cầu cờ bật theo package và đặt các hook audio trong nhánh chỉ dành cho `com.zhiliaoapp.musically`. Cần cấu hình/đánh giá theo package, classloader, callback và định dạng thật trước khi sửa module; không thể dùng nguyên trạng để kết luận tương thích.

## Thiết bị và APK

| Mục | Kết quả | Mức |
|---|---|---|
| Thiết bị | Samsung SM-G996U, Android 14, ABI thiết bị `arm64-v8a,armeabi-v7a,armeabi` | CONFIRMED, `adb getprop` |
| Package | `com.ss.android.ugc.aweme` | CONFIRMED, `pm list packages` |
| Phiên bản | `33.6.0`, versionCode `330601`; minSdk 23, targetSdk 34 | CONFIRMED, `dumpsys package` |
| APK/split | `pm path` trả về **một** `base.apk`, 286,781,694 bytes; không thấy split APK trong `pm path` | CONFIRMED cho bản cài này |
| ABI app | `primaryCpuAbi=arm64-v8a`, secondary null | CONFIRMED |
| Camera/mic | `android.permission.CAMERA` và `RECORD_AUDIO` đều `granted=true` | CONFIRMED |
| SHA-256 bản sao | `69B639EBC289B8090DED33AEE0AFA564259186C10B36F939DD093213DEAFA3C7` | CONFIRMED |

Bản sao APK chỉ nằm ở `android/test-results/douyin-study-20261020/base.apk` (Git ignored); metadata ở `device-package-metadata.txt`. `apkanalyzer dex list` thấy 53 DEX; ZIP có 245 `.so` (bao gồm thư viện dưới `assets/arm64-v8a`). Douyin có plugin tải động trong thư mục app private; bản sao `base.apk` **không** bao quát toàn bộ plugin/runtime classloader.

## Camera pipeline

**Runtime preview.** Khi người dùng tự mở camera (không LIVE), `dumpsys activity` ghi `com.ss.android.ugc.aweme/.shortvideo.ui.VideoRecordNewActivity` là `topResumedActivity`. `dumpsys media.camera -v 2` ghi `Active Camera Clients` với package Douyin, camera ID 0, PID 4640 và `CameraDeviceClient[0]`. Đây là bằng chứng hệ thống cho **Camera2 client** tại preview đó, mạnh hơn việc chỉ tìm chuỗi `Camera2` trong APK. Trước đó CameraService còn ghi kết nối camera ID 1; không suy ra camera trước/sau từ số ID. Log lưu ở `test-results/douyin-study-20261020/cameraservice-preview.txt`.

**DEX.** `classes31.dex` chứa `com.ss.android.ttvecamera.TECameraServer`, `com.ss.android.vesdk.VECameraCapture`, các tham chiếu `CameraDevice`, `CameraCaptureSession`, `ImageReader`, `CameraManager`; `com.bytedance.bpea.entry.api.camera.Camera2Entry.openCamera(CameraManager, String, CameraDevice.StateCallback, Handler, Cert)` là wrapper cấp quyền. `TECameraServer` có logic `supportsCamera2ApiLocked`/fallback Camera2. `com.ss.avframework.livestreamv2.capture.CameraVideoCapturer` tham chiếu `TECameraServer` và `TECameraSettings`, có `onFrameCaptured(TECameraFrame)`; điều này cho thấy khả năng nhánh LIVE dùng cùng tầng TECameraServer, **không chứng minh** đã chạy trong LIVE. APK cũng tham chiếu legacy `android.hardware.Camera`; không loại trừ Camera1 ở chế độ/thiết bị khác. Không thấy descriptor `Landroidx/camera/` trong 53 DEX của base APK, nên không có bằng chứng CameraX ở bản base này; plugin có thể khác.

**Native media liên quan** trong base APK: `libttvesdk.so`, `libkryptonvecamera.so`, `libeffect.so`, `libliveffect.so`, `libvolcenginertc.so`, `libvideodec.so`. Log preview có dòng nạp `libbyteaudio.so` nhưng chỉ chứng minh thư viện được nạp. Chưa xác định chuỗi native camera frame từ từng `.so`.

**So với hook.** `android/SaraeAgent/injection/app/src/main/java/io/twinkle/unreal/HookMain.kt` hook `CameraDevice`/`createCaptureSession` và thay `Surface`; callback `onClosed` dùng `getMethod` để xử lý method kế thừa. Vì runtime Douyin là Camera2, điểm móc Android có thể áp dụng về mặt API. Chưa biết các overload/session mode và vòng đời Surface thực tế của Douyin có khớp; cần probe riêng sau khi có phép patch ở nhiệm vụ khác. **Không sửa camera hook đang chạy cho TikTok** dựa vào phân tích này.

## Audio pipeline

### VE recorder callback: được xác nhận trong DEX

`classes31.dex`, xem `test-results/douyin-study-20261020/TEAudioDataInterface.smali.txt`:

- Class `com.ss.android.ttve.nativePort.TEAudioDataInterface` implements `com.ss.android.vesdk.audio.VEAudioCaptureListener`.
- Method chính xác: `public declared-synchronized onReceive(Lcom/ss/android/vesdk/audio/VEAudioSample;)V`.
- `onInfo(IIDLjava/lang/Object;)V` nhận `VEAudioCaptureSettings`, đọc `getSampleRate()`, `getChannel()`, `getBitSamples()` và gọi `nativeInit(JIII)I`.
- `VEAudioSample` có `getSampleBuffer()`, `getByteSize()`, `getTimeStamp()`; factory tạo từ `byte[]` hoặc `ByteBuffer`.
- Khi `getDataStore()==0`, `onReceive` lấy `LX/0pHr;->LIZ:[B` rồi gọi `nativeSendArrayData(J[BIJJ)I`. Nhánh khác lấy `VEAudioSample$ByteBufferSampleBuffer.getBuffer()` (field `LIZ:ByteBuffer`) rồi gọi `nativeSendBufferData(JLjava/nio/ByteBuffer;IJJ)I`. Hai nhánh đưa kèm kích thước và timestamp vào native.
- Khi cờ `ve_enable_aaudio_mic_record` bật, method bỏ qua phần gửi sample ở đường Java này. Việc cấu hình cờ trên thiết bị hiện tại **chưa biết**.

`com.ss.android.vesdk.audio.TEAudioRecord` chứa `android.media.AudioRecord`, các lời gọi `read(byte[])` và `read(ByteBuffer,int)`. Đây là đường VE recorder khả dĩ cấp `VEAudioSample`. Sample rate, channel, bit depth, buffer size và hiệu lực timestamp **của callback đang chạy** chưa được đo. `VirtualAudioFeeder` tạo signed 16-bit little-endian PCM, đổi 44.1/48 kHz và mono/stereo; DEX cho thấy `bitSamples` là cấu hình riêng, do đó chỉ tương thích khi format thực tế khớp 16-bit hoặc có chuyển đổi được xác minh.

### Nhánh audio LIVE/RTC: tồn tại riêng, chưa kiểm thử

Base DEX có `com.ss.avframework.capture.audio.AudioCapturerAudioRecord` (field `mAudioRecord`, gọi `startRecording`/`read`), `AudioCapturerOpensles` (các native `nativeCreate/start/stop`), `com.ss.avframework.livestreamv2.core.audiorecord.AudioRecordManager.onAudioFrameAvailable(Buffer, int, int, int, long)` và `com.ss.bytertc.audio.device.webrtc.WebRtcAudioRecord`. Native có `libbyteaudio.so`, `libnativeaudio.so`, `libvolcenginertc.so`, `libRealXAudio.so` và `libttvesdk.so`. Đây là **bằng chứng tĩnh về các lựa chọn pipeline**, không xác định pipeline nào chạy cho LIVE trên máy này.

Trong preview, không cài/hook module vào Douyin, không ghi nhận callback `onReceive`; `dumpsys media.audio_flinger` lúc quan sát báo `No input streams.` Điều đó chỉ nói preview ở thời điểm đọc chưa mở luồng thu âm Android; không cho biết trạng thái khi quay hay LIVE. Không có bằng chứng tiếng đã đi vào LIVE.

## Bảng đối chiếu TikTok 47.4.3 / Douyin 33.6.0

| Thành phần | TikTok | Douyin | Tương thích / mức |
|---|---|---|---|
| Camera API | Camera2 injection đã hoạt động thực tế | Camera2 `CameraDeviceClient[0]` trong preview; Camera1 cũng có trong DEX | **LIKELY** dùng lại điểm hook Camera2; hành vi thay Surface chưa thử |
| Camera hook | `CameraDevice`/session callback/Surface của `HookMain` | Wrapper `TECameraServer`/`VECameraCapture`, Android Camera2 runtime | **UNKNOWN** với overload/session/preview output thực tế |
| Audio framework | VE `TEAudioDataInterface` theo source/log cũ; audio LIVE chưa xác minh | VE recorder + avframework `livestreamv2`/OpenSL ES/AudioRecord/RTC trong DEX | **UNKNOWN** đường LIVE nào hoạt động |
| `TEAudioDataInterface` | Đã tìm class/đăng ký hook bằng TikTok PathClassLoader trong lần kiểm tra trước | Định nghĩa class ở `classes31.dex`; chưa hook runtime | **CONFIRMED** class Douyin tồn tại; classloader runtime **UNKNOWN** |
| `VEAudioSample` | Đã khảo sát accessor `getByteSize/getSampleBuffer`; callback preview không chạy | Class ở `classes31.dex`; có buffer, byteSize, timestamp | **CONFIRMED** shape DEX Douyin; đối chiếu runtime **UNKNOWN** |
| `onReceive` | `onReceive(VEAudioSample)` theo source/log TikTok trước; không quan sát callback preview | Chữ ký chính xác `(Lcom/ss/android/vesdk/audio/VEAudioSample;)V` | **CONFIRMED** tên/chữ ký mức DEX Douyin; callback LIVE **UNKNOWN** |
| PCM format | Feeder output s16le; format callback TikTok runtime chưa xác minh | `VEAudioCaptureSettings` có rate/channel/bitSamples/dataStore; giá trị runtime chưa đo | **UNKNOWN**; không được mặc định s16le |
| Classloader | TikTok PathClassLoader đã tìm class trong lần trước | Base `classes31.dex`; app có plugin/classloader động | **UNKNOWN** loader thực tế của callback Douyin |
| Native libraries | TikTok có TT/VE media theo khảo sát trước; danh sách chính xác phụ thuộc APK cài | `libttvesdk`, `libbyteaudio`, `libvolcenginertc`, `libkryptonvecamera`... | **LIKELY** họ thư viện tương tự; binary/ABI và đường LIVE chưa đối chiếu |

Lưu ý so sánh TikTok dựa trên `android/docs/audio-hook-analysis.md`, `android/ANDROID_AGENT_V1_REPORT.md`, source `HookMain.kt` và log preview trước đây. `apkanalyzer` không trích được class `TEAudioDataInterface` trực tiếp từ APK TikTok đã LSPosed-patch đang lưu; vì vậy không coi bảng này là so khớp bytecode hai APK.

## Khả năng tái sử dụng và giới hạn

1. **Camera2 API hook — LIKELY:** Chỉ có bằng chứng Douyin preview dùng Camera2, chưa có regression injection. Cần thử riêng với bản sao/cấu hình có kiểm soát khi được phép; không sửa/cài đè app Douyin hiện tại.
2. **VE audio callback hook — cấu trúc LIKELY, LIVE UNKNOWN:** Tên và descriptor Douyin khớp mục tiêu TikTok. `onReceive` gửi sample vào native VE recorder, nhưng `avframework` cung cấp đường LIVE khác và AAudio có thể bỏ qua nhánh Java. Hook đăng ký thành công cũng chưa chứng minh callback gọi hoặc âm thanh được phát trên LIVE.
3. **Feeder PCM — có thể tái sử dụng thuật toán:** `VirtualAudioFeeder` đã kiểm thử offline, nhưng cần log/quan sát rate, channels, bitSamples, dataStore, buffer class/size/timestamp và callback thread trên Douyin trước khi ghi PCM. Không thử LIVE bằng tài khoản thật.
4. **Điểm không tương thích hiện tại:** `HookMain.kt` dòng gần 99 yêu cầu enable flag theo package; dòng gần 103 đặt hook audio trong nhánh TikTok-only. Douyin vì thế không tự nhận đường audio này. Native OpenSL ES/AAudio không đi qua Java `AudioRecord.read`/`TEAudioDataInterface.onReceive` theo cùng cách. Bản build hiện tại không được coi là module Douyin hoạt động.
5. **Classloader/plugin:** class nằm trong base DEX nhưng Douyin có nhiều plugin động; cần kiểm tra loader lúc runtime trước khi kết luận `findClass` dùng appClassLoader luôn thành công trong mọi tiến trình.

## Bằng chứng và bước tiếp theo không cần LIVE

- Metadata/checksum: `android/test-results/douyin-study-20261020/device-package-metadata.txt`.
- APK sao lưu (không commit): `android/test-results/douyin-study-20261020/base.apk`.
- DEX bytecode: `TEAudioDataInterface.smali.txt`, `VEAudioSample.smali.txt`, `TEAudioRecord.smali.txt`, `TECameraServer.smali.txt`, `VECameraCapture.smali.txt`, `CameraVideoCapturer.smali.txt`, `AudioCapturerAudioRecord.smali.txt`, `AudioCapturerOpensles.smali.txt`, `AudioRecordManager.smali.txt`, `Camera2Entry.smali.txt` trong cùng thư mục.
- Runtime preview: `cameraservice-preview.txt`, `logcat-media-filtered.txt`, `logcat-camera-system-filtered.txt` trong cùng thư mục.
- TikTok đối chiếu: `android/docs/audio-hook-analysis.md` và các log được liệt kê trong tài liệu đó.

Bước nghiên cứu hợp lý nếu sau này được phép: tạo probe/diagnostics **riêng** cho package Douyin trên bản sao thử nghiệm, ghi classloader + method descriptor ở preview, buffer metadata khi callback xuất hiện, và Camera2 session callbacks; kiểm tra luồng quay video thường ở chế độ offline nếu có thể. Không giả lập hoặc bắt đầu LIVE. Nếu không có callback trong preview/quay offline, ghi `NOT_TESTABLE_OFFLINE` cho callback LIVE và giữ kết luận tiếng LIVE là `UNVERIFIED`.
