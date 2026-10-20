# Audio hook analysis

## Callback shape and class loading

The target is TikTok 47.4.3 (`com.zhiliaoapp.musically`). Source analysis records `com.ss.android.ttve.nativePort.TEAudioDataInterface.onReceive(VEAudioSample)` and the sample accessors `getByteSize()` / `getSampleBuffer()`. `HookMain` resolves that class with TikTok's `appClassLoader`, then selects declared `onReceive` methods with one parameter.

The runtime details available from the installed module are narrower:

- The earlier module log reports hook registration, but does not print the reflective method descriptor or the actual sample object's class.
- The preview observation shows native registration for five methods on `TEAudioDataInterface`; this is not evidence that `onReceive` ran.
- `onReceive` was not invoked in the observed camera preview. The exact runtime descriptor, parameter classloader, sample-buffer class, and backing storage therefore remain unconfirmed on this device.
- The rebuilt module now logs the full reflective method signature and classloader at registration, then logs sample class/classloader, sample-buffer and storage types, buffer size, PCM metrics, processing time, and short reads on callback. Those new diagnostics are source/build evidence only until that module is loaded into TikTok.

At source level, the callback expects `getSampleBuffer()` to expose a buffer object with field `LIZ` backed by `ByteArray` or `ByteBuffer`. It reads sample rate and channel count from `mAudioCaptureSettings`, defaults an invalid rate to 44.1 kHz and maps channel settings to mono/stereo, then writes little-endian signed 16-bit PCM. The source path does not consume a bit-depth setting. Whether that representation matches the actual runtime sample format is **UNVERIFIED** until a callback log provides the concrete object and PCM metadata.

## Decoder and shared PCM feeder

Both manager and injection module use `android/audio-core/.../VirtualAudioFeeder.kt`. The feeder decodes AAC with `MediaExtractor`/`MediaCodec`, converts 44.1/48 kHz and mono/stereo, and emits signed 16-bit little-endian PCM at the requested sample rate/channel count. The module writes the resulting PCM into the hook's sample storage. The offline harness exercises this feeder and AudioTrack, but does not instantiate TikTok's private callback object or call the hook's buffer write path.

`onReceive` currently invokes the feeder synchronously on the callback thread. Its processing-time log will measure that section when callbacks occur. The camera preview logs below show the Camera2 session and virtual-video playlist running while the hook is registered, but because no audio callback ran they do not prove the audio callback cannot delay camera processing.

## Camera-preview observation (not LIVE)

On 2026-10-20, TikTok 47.4.3 was at its camera creation/preview UI; the Camera tab was selected and LIVE was not started. Device logcat in `test-results/camera-preview-hook-check.log` shows:

- 13:43:24 — old module reports the `onReceive` hook installed.
- 13:43:33 — TikTok registers five native methods for `TEAudioDataInterface`.
- 13:43:34 — Camera2 session configures and starts virtual playlist video.
- 13:43–13:48 — playlist advances and Camera2 preview continues; there are no `onReceive invoked`, injected PCM, or AudioRecord-read lines.

Result: hook-registration evidence **PASS** for that installed build; callback invocation count observed in preview **0**. Callback delivery in camera preview is **NOT_TESTABLE_OFFLINE** with this build. This does not establish that callback invocation is LIVE-only. TikTok LIVE audio remains **UNVERIFIED**.

## Offline matrix

`VCAMManager/app/src/androidTest/java/co/uns/vcammanager/VirtualAudioFeederFormatMatrixTest.kt` runs on a device and checks two AAC sources (44.1 kHz mono and 48 kHz stereo) against 44.1/48 kHz mono/stereo AudioTrack outputs. It records RMS, peak, decoder/read latency, write latency, AudioTrack underruns, and drift between two successive AudioTimestamp readings. Current device results are in `test-results/vcam-audio-matrix-results.log`; the connected instrumentation XML records 0 failures.

Historical evidence from the earlier 30-minute muted run is in `test-results/vcam-audio-endurance.log` and the original short audible smoke run is in `test-results/vcam-audio-smoke.log`. These are separate from the newer format-matrix instrumentation results.
