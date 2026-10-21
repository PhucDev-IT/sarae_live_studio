package io.twinkle.unreal

import android.Manifest
import android.app.Application
import android.content.Context
import android.content.ComponentName
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.graphics.SurfaceTexture
import android.hardware.Camera
import android.hardware.Camera.PictureCallback
import android.hardware.Camera.PreviewCallback
import android.hardware.Camera.ShutterCallback
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CaptureFailure
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.params.InputConfiguration
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaMetadataRetriever
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.view.Surface
import android.view.SurfaceHolder
import android.widget.Toast
import com.blankj.utilcode.util.FileUtils
import com.highcapable.yukihookapi.YukiHookAPI
import com.highcapable.yukihookapi.annotation.xposed.InjectYukiHookWithXposed
import com.highcapable.yukihookapi.hook.factory.method
import com.highcapable.yukihookapi.hook.log.YLog
import com.highcapable.yukihookapi.hook.type.android.ActivityClass
import com.highcapable.yukihookapi.hook.type.android.BundleClass
import com.highcapable.yukihookapi.hook.type.java.BooleanType
import com.highcapable.yukihookapi.hook.type.java.UnitType
import com.highcapable.yukihookapi.hook.xposed.proxy.IYukiHookXposedInit
import de.robv.android.xposed.XC_MethodHook
import de.robv.android.xposed.XC_MethodHook.MethodHookParam
import de.robv.android.xposed.XposedBridge
import de.robv.android.xposed.XposedHelpers
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.lang.reflect.Method
import java.nio.ByteBuffer
import java.util.Arrays
import java.util.WeakHashMap
import java.util.concurrent.Executor
import kotlin.math.min

internal fun resolveCamera2CallbackMethod(callbackClass: Class<*>, methodName: String): Method =
    callbackClass.getMethod(methodName, CameraCaptureSession::class.java)

private val hookedCamera2CallbackMethods = java.util.Collections.synchronizedSet(mutableSetOf<Method>())

private const val SARAe_TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
private const val SARAe_MODE_FILE = "sarae-source-mode.txt"
private const val SARAe_STATUS_ACTION = "co.sarae.agent.action.VCAM_INJECTION_STATUS"

private data class AudioInjectionStats(
    var readCalls: Long = 0,
    var pcmBytesFed: Long = 0,
    var underrunBuffers: Long = 0,
    var droppedBuffers: Long = 0,
    var lastSampleTimestamp: Long? = null
)

@InjectYukiHookWithXposed
class HookMain : IYukiHookXposedInit {
    var imageReaderFormat = 0
    var needRecreate = false
    var needToShowToast = true
    var c2OriWidth = 1280
    var c2OriHeight = 720
    var toastContent: Context? = null

    private fun requestedCameraMode(packageName: String): String {
        if (packageName != SARAe_TIKTOK_PACKAGE) return "VIRTUAL_VIDEO"
        val root = File(Environment.getExternalStorageDirectory(), "DCIM/Camera1")
        if (File(root, "disable.jpg").exists()) return "REAL_CAMERA"
        return runCatching {
            File(root, SARAe_MODE_FILE).readText().trim()
                .takeIf { it == "REAL_CAMERA" || it == "VIRTUAL_VIDEO" }
                ?: "REAL_CAMERA"
        }.getOrDefault("REAL_CAMERA")
    }

    private fun reportInjectionStatus(event: String, mode: String, cameraId: String? = null, detail: String? = null) {
        if (toastContent?.packageName != SARAe_TIKTOK_PACKAGE) return
        val appContext = toastContent ?: return
        try {
            val intent = Intent(SARAe_STATUS_ACTION).setComponent(
                ComponentName("co.sarae.agent", "co.sarae.agent.VcamInjectionStatusReceiver")
            ).putExtra("event", event).putExtra("mode", mode)
            if (cameraId != null) intent.putExtra("camera_id", cameraId)
            if (detail != null) intent.putExtra("detail", detail)
            appContext.sendBroadcast(intent)
        } catch (t: Throwable) {
            YLog.error(tag = "VcamPro", msg = "Cannot report injection status $event/$mode: $t")
        }
    }

    override fun onInit() = YukiHookAPI.configs {
        isDebug = BuildConfig.DEBUG
    }

    override fun onHook() = YukiHookAPI.encase {
        if (this.packageName == "io.twinkle.unreal") {
            "io.twinkle.unreal.util.ModuleStatus".toClass().method {
                name = "isActivated"
                emptyParam()
                returnType = BooleanType
            }.hook {
                before {
                    this.resultTrue()
                }
            }
        }

        ActivityClass.method {
            name = "onCreate"
            param(BundleClass)
            returnType = UnitType
        }.hook {
            after {
                YLog.error(tag = "Unreal", msg = "当前Activity: ${this.instance}, Class: ${this.instanceClass?.name}")
            }
        }

        val enabled =
            File(Environment.getExternalStorageDirectory().path + "/Android/data/" + this.packageName + "/files/unreal/enabled")

        // The lab-only camera probe is bundled with the demo APK and has no external toggle UI.
        // Keep normal targets behind the existing per-app enable flag.
        if (!enabled.exists() && this.packageName != "test.codex.cameraprobe" &&
            this.packageName != SARAe_TIKTOK_PACKAGE) {
            YLog.error(tag = "Unreal", msg = "${this.packageName} 未启用")
            return@encase
        } else {
            if (this.packageName == SARAe_TIKTOK_PACKAGE) {
                // Replace microphone PCM returned by supported AudioRecord.read overloads.
                AudioRecord::class.java.declaredMethods
                    .filter {
                        it.name == "read" && it.parameterTypes.firstOrNull() in setOf(
                            ByteBuffer::class.java,
                            ByteArray::class.java,
                            ShortArray::class.java
                        )
                    }
                    .forEach { readMethod ->
                        try {
                            XposedBridge.hookMethod(readMethod, object : XC_MethodHook() {
                                override fun beforeHookedMethod(param: MethodHookParam) {
                                    val signature = readMethod.toGenericString()
                                    if (loggedAudioReadMethods.add(signature)) {
                                        YLog.debug(tag = "VcamAudio", msg = "Replacing TikTok mic PCM: $signature")
                                    }
                                    val feeder = synchronized(audioFeeders) { audioFeeders[param.thisObject] }
                                        ?: return
                                    try {
                                        val stats = synchronized(audioFeeders) {
                                            audioReadStats.getOrPut(param.thisObject) { AudioInjectionStats() }.apply { readCalls++ }
                                        }
                                        val size = param.args[1] as? Int ?: return
                                        val requestedBytes = when (param.args[0]) {
                                            is ByteArray -> (param.args.getOrNull(2) as? Int ?: 0).coerceAtLeast(0)
                                            is ShortArray -> ((param.args.getOrNull(2) as? Int ?: 0).coerceAtLeast(0) * 2)
                                            else -> size.coerceAtLeast(0)
                                        }
                                        param.result = when (val target = param.args[0]) {
                                            is ByteBuffer -> feeder.read(target, size)
                                            is ByteArray -> {
                                                val offset = (param.args.getOrNull(1) as? Int) ?: 0
                                                val byteCount = (param.args.getOrNull(2) as? Int) ?: return
                                                val destination = ByteBuffer.wrap(target, offset, byteCount)
                                                feeder.read(destination, byteCount)
                                            }
                                            is ShortArray -> {
                                                val offset = (param.args.getOrNull(1) as? Int) ?: 0
                                                val shortCount = (param.args.getOrNull(2) as? Int) ?: return
                                                val bytes = ByteBuffer.allocate(shortCount * 2).order(java.nio.ByteOrder.LITTLE_ENDIAN)
                                                val written = feeder.read(bytes, shortCount * 2) / 2
                                                for (i in 0 until written) target[offset + i] = bytes.getShort(i * 2)
                                                written
                                            }
                                            else -> return
                                        }
                                        val written = (param.result as? Int ?: 0).coerceAtLeast(0)
                                        synchronized(audioFeeders) {
                                            stats.pcmBytesFed += written
                                            if (written < requestedBytes) stats.underrunBuffers++
                                        }
                                        if (stats.readCalls == 1L || stats.readCalls % 100L == 0L) {
                                            val record = param.thisObject as AudioRecord
                                            val routed = runCatching { record.routedDevice?.type }.getOrNull()
                                            YLog.debug(tag = "VcamAudio", msg = "AudioRecord diagnostics registered=true callbacks=${stats.readCalls} pcmBytesFed=${stats.pcmBytesFed} underruns=${stats.underrunBuffers} dropped=${stats.droppedBuffers} format=${record.sampleRate}Hz/${record.channelCount}ch state=${record.recordingState} route=${routed ?: "inactive/unknown"}")
                                        }
                                    } catch (t: Throwable) {
                                        synchronized(audioFeeders) {
                                            audioReadStats[param.thisObject]?.droppedBuffers =
                                                (audioReadStats[param.thisObject]?.droppedBuffers ?: 0L) + 1
                                        }
                                        YLog.error(tag = "VcamAudio", msg = "PCM injection read failed: $t")
                                    }
                                }
                            })
                            YLog.debug(tag = "VcamAudio", msg = "AudioRecord.read hook registered=true signature=${readMethod.toGenericString()}; callback invocation and route state are tracked separately")
                        } catch (t: Throwable) {
                            YLog.error(tag = "VcamAudio", msg = "Cannot hook AudioRecord.read: $t")
                        }
                    }
                try {
                    XposedHelpers.findAndHookMethod(
                        AudioRecord::class.java,
                        "startRecording",
                        object : XC_MethodHook() {
                            override fun afterHookedMethod(param: MethodHookParam) {
                                val record = param.thisObject as AudioRecord
                                val files = (1..500).map { number ->
                                    File(video_path, "playlist/${number.toString().padStart(3, '0')}.mp4")
                                }.takeWhile { it.isFile }.map { it.absolutePath }
                                    .ifEmpty { listOf(File(video_path, "virtual.mp4").absolutePath) }
                                val activePath = activeVideoPath
                                val index = files.indexOf(activePath).takeIf { it >= 0 } ?: 0
                                val elapsed = if (activeVideoStartedAtMs > 0L) {
                                    (android.os.SystemClock.elapsedRealtime() - activeVideoStartedAtMs).coerceAtLeast(0)
                                } else 0L
                                try {
                                    val feeder = VirtualAudioFeeder(
                                        files = files,
                                        targetRate = record.sampleRate,
                                        targetChannels = record.channelCount.coerceIn(1, 2),
                                        startIndex = index,
                                        startPositionMs = elapsed
                                    )
                                    synchronized(audioFeeders) {
                                        audioFeeders.remove(record)?.close()
                                        audioFeeders[record] = feeder
                                    }
                                    val routed = runCatching { record.routedDevice?.type }.getOrNull()
                                    YLog.debug(
                                        tag = "VcamAudio",
                                        msg = "Audio route active=${record.recordingState == AudioRecord.RECORDSTATE_RECORDING} routedDevice=${routed ?: "inactive/unknown"}; hookRegistered=true callbackInvocations=pending. MP4 audio is decoded to AudioRecord PCM; speaker playback is a separate MediaPlayer route. source=${files[index]} format=${record.sampleRate}Hz/${record.channelCount}ch seek=${elapsed}ms"
                                    )
                                } catch (t: Throwable) {
                                    YLog.error(tag = "VcamAudio", msg = "Cannot initialize playlist audio: $t; keep microphone")
                                }
                            }
                        }
                    )
                    XposedHelpers.findAndHookMethod(
                        AudioRecord::class.java,
                        "stop",
                        object : XC_MethodHook() {
                            override fun beforeHookedMethod(param: MethodHookParam) {
                                synchronized(audioFeeders) { audioFeeders.remove(param.thisObject)?.close() }
                            }
                        }
                    )
                    XposedHelpers.findAndHookMethod(
                        AudioRecord::class.java,
                        "release",
                        object : XC_MethodHook() {
                            override fun beforeHookedMethod(param: MethodHookParam) {
                                synchronized(audioFeeders) { audioFeeders.remove(param.thisObject)?.close() }
                            }
                        }
                    )
                } catch (t: Throwable) {
                    YLog.error(tag = "VcamAudio", msg = "Cannot hook AudioRecord lifecycle: $t")
                }
                try {
                    val dataInterface = XposedHelpers.findClass(
                        "com.ss.android.ttve.nativePort.TEAudioDataInterface",
                        this@encase.appClassLoader
                    )
                    val receiveCallbacks = dataInterface.declaredMethods
                        .filter { it.name == "onReceive" && it.parameterTypes.size == 1 }
                    check(receiveCallbacks.isNotEmpty()) { "No one-argument onReceive method found in ${dataInterface.name}" }
                    receiveCallbacks
                        .forEach { callback ->
                            YLog.debug(
                                tag = "VcamAudio",
                                msg = "Hook registration signature=${callback.toGenericString()}, targetLoader=${callback.declaringClass.classLoader}, parameterLoader=${callback.parameterTypes[0].classLoader}"
                            )
                            XposedBridge.hookMethod(callback, object : XC_MethodHook() {
                                override fun beforeHookedMethod(param: MethodHookParam) {
                                    val callbackStartNs = android.os.SystemClock.elapsedRealtimeNanos()
                                    val callbackCount = synchronized(audioFeeders) {
                                        val next = (audioCallbackCounts[param.thisObject] ?: 0L) + 1L
                                        audioCallbackCounts[param.thisObject] = next
                                        next
                                    }
                                    if (callbackCount == 1L || callbackCount % 100L == 0L) {
                                        YLog.debug(tag = "VcamAudio", msg = "TEAudioDataInterface.onReceive invoked: callbacks=$callbackCount")
                                    }
                                    val sample = param.args.firstOrNull() ?: return
                                    try {
                                        val sampleTimestamp = runCatching {
                                            (XposedHelpers.callMethod(sample, "getTimeStamp") as? Number)?.toLong()
                                        }.getOrNull()
                                        val previousTimestamp = synchronized(audioFeeders) {
                                            val stats = audioReadStats.getOrPut(param.thisObject) { AudioInjectionStats() }
                                            val previous = stats.lastSampleTimestamp
                                            stats.lastSampleTimestamp = sampleTimestamp ?: previous
                                            previous
                                        }
                                        val timestampContinuity = when {
                                            sampleTimestamp == null || previousTimestamp == null -> "unknown"
                                            sampleTimestamp > previousTimestamp -> "monotonic"
                                            else -> "discontinuity"
                                        }
                                        val sampleBytes = (XposedHelpers.callMethod(sample, "getByteSize") as? Int)?.coerceAtLeast(0) ?: return
                                        val sampleBuffer = XposedHelpers.callMethod(sample, "getSampleBuffer")
                                        val storage = XposedHelpers.getObjectField(sampleBuffer, "LIZ")
                                        if (callbackCount == 1L) {
                                            YLog.debug(
                                                tag = "VcamAudio",
                                                msg = "Callback sample=${sample.javaClass.name} sampleLoader=${sample.javaClass.classLoader} sampleBuffer=${sampleBuffer?.javaClass?.name} storage=${storage?.javaClass?.name} byteSize=$sampleBytes"
                                            )
                                        }
                                        var feeder = synchronized(audioFeeders) { audioFeeders[param.thisObject] }
                                        if (feeder == null) {
                                            val files = (1..500).map { number ->
                                                File(video_path, "playlist/${number.toString().padStart(3, '0')}.mp4")
                                            }.takeWhile { it.isFile }.map { it.absolutePath }
                                                .ifEmpty { listOf(File(video_path, "virtual.mp4").absolutePath) }
                                            val activePath = activeVideoPath
                                            val index = files.indexOf(activePath).takeIf { it >= 0 } ?: 0
                                            val elapsed = if (activeVideoStartedAtMs > 0L) {
                                                (android.os.SystemClock.elapsedRealtime() - activeVideoStartedAtMs).coerceAtLeast(0)
                                            } else 0L
                                            val settings = XposedHelpers.getObjectField(param.thisObject, "mAudioCaptureSettings")
                                            val configuredRate = (XposedHelpers.callMethod(settings, "getSampleRate") as? Int) ?: 0
                                            val configuredChannels = (XposedHelpers.callMethod(settings, "getChannel") as? Int) ?: 0
                                            val rate = configuredRate.takeIf { it in 8000..192000 } ?: 44100
                                            val channels = if (configuredChannels <= 1) 1 else 2
                                            feeder = VirtualAudioFeeder(
                                                files = files,
                                                targetRate = rate,
                                                targetChannels = channels,
                                                startIndex = index,
                                                startPositionMs = elapsed,
                                                paceReads = false
                                            )
                                            synchronized(audioFeeders) { audioFeeders[param.thisObject] = feeder }
                                            YLog.debug(
                                                tag = "VcamAudio",
                                                msg = "onReceive feeder initialized: ${files[index]}, ${rate}Hz/${channels}ch, bytes=$sampleBytes, seek=${elapsed}ms"
                                            )
                                        }
                                        val written = when (storage) {
                                            is ByteArray -> {
                                                val count = sampleBytes.coerceAtMost(storage.size)
                                                feeder.read(ByteBuffer.wrap(storage, 0, count), count)
                                            }
                                            is ByteBuffer -> feeder.read(storage, sampleBytes)
                                            else -> 0
                                        }
                                        val callbackStats = synchronized(audioFeeders) {
                                            audioReadStats.getOrPut(param.thisObject) { AudioInjectionStats() }.apply {
                                                pcmBytesFed += written.coerceAtLeast(0)
                                                if (written < sampleBytes) underrunBuffers++
                                                if (written == 0) droppedBuffers++
                                            }
                                        }
                                        if (callbackCount == 1L || callbackCount % 100L == 0L) {
                                            val timestampDelta = if (sampleTimestamp != null && previousTimestamp != null) sampleTimestamp - previousTimestamp else null
                                            YLog.debug(tag = "VcamAudio", msg = "Audio callback stats registered=true invoked=$callbackCount format=${feeder.targetRate}Hz/${feeder.targetChannels}ch/s16 bytesFed=${callbackStats.pcmBytesFed} underrunBuffers=${callbackStats.underrunBuffers} droppedBuffers=${callbackStats.droppedBuffers} timestamp=$sampleTimestamp delta=$timestampDelta continuity=$timestampContinuity route=AudioRecord route status is logged separately")
                                        }
                                        if (written > 0) {
                                            // Do not let stale microphone samples remain after a short decode read.
                                            when (storage) {
                                                is ByteArray -> java.util.Arrays.fill(storage, written.coerceAtMost(storage.size), sampleBytes.coerceAtMost(storage.size), 0.toByte())
                                                is ByteBuffer -> {
                                                    val position = storage.position()
                                                    val end = (position + sampleBytes).coerceAtMost(storage.limit())
                                                    storage.position((position + written).coerceAtMost(end))
                                                    while (storage.position() < end) storage.put(0.toByte())
                                                    storage.position(position)
                                                }
                                            }
                                            if (callbackCount == 1L || callbackCount % 100L == 0L) {
                                                val metrics = synchronized(audioFeeders) {
                                                    audioCallbackMetrics.getOrPut(param.thisObject) { PcmMetrics(feeder.targetRate, feeder.targetChannels) }
                                                }
                                                val measured = when (storage) {
                                                    is ByteArray -> storage.copyOfRange(0, written.coerceAtMost(storage.size))
                                                    is ByteBuffer -> ByteArray(written).also { copy ->
                                                        val view = storage.duplicate()
                                                        view.position(view.position().coerceAtMost(view.limit()))
                                                        view.get(copy, 0, written.coerceAtMost(view.remaining()))
                                                    }
                                                    else -> byteArrayOf()
                                                }
                                                metrics.add(measured, measured.size)
                                                val processingMs = (android.os.SystemClock.elapsedRealtimeNanos() - callbackStartNs) / 1_000_000.0
                                                YLog.debug(tag = "VcamAudio", msg = "TEAudioDataInterface PCM: callbacks=$callbackCount bytes=$written/$sampleBytes format=${feeder.targetRate}Hz/${feeder.targetChannels}ch/s16 rms=${metrics.rms()} peak=${metrics.peak()} processingMs=$processingMs shortRead=${written < sampleBytes} timestamps=$timestampContinuity")
                                            }
                                        }
                                    } catch (t: Throwable) {
                                        YLog.error(tag = "VcamAudio", msg = "LIVE callback injection failed: $t")
                                    }
                                }
                            })
                        }
                    YLog.debug(
                        tag = "VcamAudio",
                        msg = "Hook registration complete: registered=${receiveCallbacks.size} class=${dataInterface.name} loader=${dataInterface.classLoader} signatures=${receiveCallbacks.joinToString { it.toGenericString() }}; callback invocation is separate"
                    )
                } catch (t: Throwable) {
                    YLog.error(tag = "VcamAudio", msg = "Cannot hook TikTok live audio callback: $t")
                }
            }
            // 第一条hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "setPreviewTexture",
                SurfaceTexture::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE) {
                            requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                            activeCameraSourceMode = requestedCameraSourceModeAtOpen
                            if (activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        }
                        val file = File(video_path + "virtual.mp4")
                        if (file.exists()) {
                            val control_file =
                                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                            if (control_file.exists()) {
                                return
                            }
                            if (is_hooked) {
                                is_hooked = false
                                return
                            }
                            if (param.args[0] == null) {
                                return
                            }
                            if (param.args[0] == c1_fake_texture) {
                                return
                            }
                            if (origin_preview_camera != null && origin_preview_camera == param.thisObject) {
                                param.args[0] = fake_SurfaceTexture
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "发现重复" + origin_preview_camera.toString()
                                )
                                return
                            } else {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "创建预览"
                                )
                            }
                            origin_preview_camera = param.thisObject as Camera
                            mSurfacetexture = param.args[0] as SurfaceTexture
                            if (fake_SurfaceTexture == null) {
                                fake_SurfaceTexture = SurfaceTexture(10)
                            } else {
                                fake_SurfaceTexture!!.release()
                                fake_SurfaceTexture = SurfaceTexture(10)
                            }
                            param.args[0] = fake_SurfaceTexture
                        } else {
                            val toast_control =
                                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                            needToShowToast = !toast_control.exists()
                            if (toastContent != null && needToShowToast) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                        }
                    }
                })

            XposedHelpers.findAndHookMethod(
                SurfaceTexture::class.java,
                "setDefaultBufferSize",
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                object : XC_MethodHook() {
                    override fun afterHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName != SARAe_TIKTOK_PACKAGE) return
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "TikTok SurfaceTexture buffer target=${param.args[0]}x${param.args[1]} activeMode=$activeCameraSourceMode"
                        )
                    }
                }
            )

            // 第二条Hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.camera2.CameraManager",
                this@encase.appClassLoader,
                "openCamera",
                String::class.java,
                CameraDevice.StateCallback::class.java,
                Handler::class.java,
                object : XC_MethodHook() {
                    @Throws(Throwable::class)
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (param.args[1] == null) {
                            return
                        }
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE) {
                            requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                        }
                        if (param.args[1] == c2_state_cb) {
                            return
                        }
                        c2_state_cb = param.args[1] as CameraDevice.StateCallback
                        c2_state_callback = param.args[1].javaClass
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE) {
                            requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                            YLog.debug(tag = "VcamPro", msg = "TikTok pre-LIVE camera mode requested=${requestedCameraSourceModeAtOpen}; session recreation required after later changes")
                            processCamera2Init(c2_state_callback)
                            return
                        }
                        requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        val file = File(video_path + "virtual.mp4")
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (!file.exists()) {
                            if (toastContent != null && needToShowToast) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                            return
                        }
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "1位参数初始化相机，类：" + c2_state_callback.toString()
                        )
                        is_first_hook_build = true
                        processCamera2Init(c2_state_callback)
                    }
                })

            // 第三条Hook
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                XposedHelpers.findAndHookMethod(
                    "android.hardware.camera2.CameraManager",
                    this@encase.appClassLoader,
                    "openCamera",
                    String::class.java,
                    Executor::class.java,
                    CameraDevice.StateCallback::class.java,
                    object : XC_MethodHook() {
                        @Throws(Throwable::class)
                        override fun afterHookedMethod(param: MethodHookParam) {
                            if (param.args[2] == null) {
                                return
                            }
                            if (this@encase.packageName == SARAe_TIKTOK_PACKAGE) {
                                requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                            }
                            if (param.args[2] == c2_state_cb) {
                                return
                            }
                            c2_state_cb = param.args[2] as CameraDevice.StateCallback
                            if (this@encase.packageName == SARAe_TIKTOK_PACKAGE) {
                                requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                                c2_state_callback = param.args[2].javaClass
                                YLog.debug(tag = "VcamPro", msg = "TikTok pre-LIVE camera mode requested=${requestedCameraSourceModeAtOpen}; session recreation required after later changes")
                                processCamera2Init(c2_state_callback)
                                return
                            }
                            requestedCameraSourceModeAtOpen = requestedCameraMode(this@encase.packageName)
                            val control_file =
                                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                            if (control_file.exists()) {
                                return
                            }
                            val file = File(video_path + "virtual.mp4")
                            val toast_control =
                                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                            needToShowToast = !toast_control.exists()
                            if (!file.exists()) {
                                if (toastContent != null && needToShowToast) {
                                    try {
                                        Toast.makeText(
                                            toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                        ).show()
                                    } catch (ee: Exception) {
                                        YLog.debug(
                                            tag = "VcamPro",
                                            msg = "[toast]$ee"
                                        )
                                    }
                                }
                                return
                            }
                            c2_state_callback = param.args[2].javaClass
                            YLog.debug(
                                tag = "VcamPro",
                                msg = "2位参数初始化相机，类：" + c2_state_callback.toString()
                            )
                            is_first_hook_build = true
                            processCamera2Init(c2_state_callback)
                        }
                    })
            }

            // 第四条Hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "setPreviewCallbackWithBuffer",
                PreviewCallback::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE && activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        if (param.args[0] != null) {
                            processCallback(param)
                        }
                    }
                })

            // 第五条Hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "addCallbackBuffer",
                ByteArray::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE && activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        if (param.args[0] != null) {
                            param.args[0] = ByteArray((param.args[0] as ByteArray).size)
                        }
                    }
                })

            // 第六条Hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "setPreviewCallback",
                PreviewCallback::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE && activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        if (param.args[0] != null) {
                            processCallback(param)
                        }
                    }
                })

            // 第七条Hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "setOneShotPreviewCallback",
                PreviewCallback::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (param.args[0] != null) {
                            processCallback(param)
                        }
                    }
                })

            // 第八条Hook
            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "takePicture",
                ShutterCallback::class.java,
                PictureCallback::class.java,
                PictureCallback::class.java,
                PictureCallback::class.java,
                object : XC_MethodHook() {
                    override fun afterHookedMethod(param: MethodHookParam) {
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "4参数拍照"
                        )
                        if (param.args[1] != null) {
                            processAShotYuv(param)
                        }
                        if (param.args[3] != null) {
                            processAShotJpeg(param, 3)
                        }
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.media.MediaRecorder",
                this@encase.appClassLoader,
                "setCamera",
                Camera::class.java,
                object : XC_MethodHook() {
                    @Throws(Throwable::class)
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        super.beforeHookedMethod(param)
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "[record]" + this@encase.packageName
                        )
                        if (toastContent != null && needToShowToast) {
                            try {
                                Toast.makeText(
                                    toastContent,
                                    "应用：" + this@encase.appInfo.name + "(" + this@encase.packageName + ")" + "触发了录像，但目前无法拦截",
                                    Toast.LENGTH_SHORT
                                ).show()
                            } catch (ee: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "[toast]" + ee.stackTrace.contentToString()
                                )
                            }
                        }
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.app.Instrumentation",
                this@encase.appClassLoader,
                "callApplicationOnCreate",
                Application::class.java,
                object : XC_MethodHook() {
                    @Throws(Throwable::class)
                    override fun afterHookedMethod(param: MethodHookParam) {
                        super.afterHookedMethod(param)
                        if (param.args[0] is Application) {
                            try {
                                toastContent = (param.args[0] as Application).applicationContext
                            } catch (ee: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "$ee"
                                )
                            }
                            val force_private =
                                File(Environment.getExternalStorageDirectory().absolutePath + "/DCIM/Camera1/private_dir.jpg")
                            if (toastContent != null) { //后半段用于强制私有目录
                                var auth_statue = 0
                                try {
                                    auth_statue += toastContent!!.checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) + 1
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[permission-check]$ee"
                                    )
                                }
                                try {
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                                        auth_statue += toastContent!!.checkSelfPermission(Manifest.permission.MANAGE_EXTERNAL_STORAGE) + 1
                                    }
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[permission-check]$ee"
                                    )
                                }

                                //权限判断完毕
                                if (auth_statue < 1 || force_private.exists()) {
                                    var shown_file = File(
                                        toastContent!!.getExternalFilesDir(null)!!.absolutePath + "/unreal/"
                                    )
                                    if (!shown_file.isDirectory && shown_file.exists()) {
                                        shown_file.delete()
                                    }
                                    if (!shown_file.exists()) {
                                        shown_file.mkdir()
                                    }
                                    shown_file =
                                        File(toastContent!!.getExternalFilesDir(null)!!.absolutePath + "/unreal/" + "has_shown")
                                    val toast_force_file =
                                        File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/force_show.jpg")
                                    if ((this@encase.packageName != BuildConfig.APPLICATION_ID && this@encase.packageName != "android") && (!shown_file.exists() || toast_force_file.exists())) {
                                        try {
                                            Toast.makeText(
                                                toastContent, """
     ${this@encase.packageName}未授予读取本地目录权限，请检查权限
     Camera1目前重定向为 
     """.trimIndent() + toastContent!!.getExternalFilesDir(null)!!
                                                    .absolutePath + "/unreal/", Toast.LENGTH_SHORT
                                            ).show()
                                            val fos = FileOutputStream(
                                                toastContent!!.getExternalFilesDir(null)!!.absolutePath + "/unreal/" + "has_shown"
                                            )
                                            val info = "shown"
                                            fos.write(info.toByteArray())
                                            fos.flush()
                                            fos.close()
                                        } catch (e: Exception) {
                                            YLog.debug(
                                                tag = "VcamPro",
                                                msg = "[switch-dir]$e"
                                            )
                                        }
                                    }
                                    video_path = toastContent!!.getExternalFilesDir(null)!!
                                        .absolutePath + "/unreal/"
                                } else {
                                    video_path =
                                        Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/"
                                }
                            } else {
                                video_path =
                                    Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/"
                                val uni_DCIM_path =
                                    File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/")
                                if (uni_DCIM_path.canWrite()) {
                                    val uni_Camera1_path = File(video_path)
                                    if (!uni_Camera1_path.exists()) {
                                        uni_Camera1_path.mkdir()
                                    }
                                }
                            }
                            // Current TikTok targets modern scoped storage. Use the shared test folder
                            // so it can read the source MP4 with READ_MEDIA_VIDEO, without root.
                            if (this@encase.packageName == "com.zhiliaoapp.musically" ||
                                this@encase.packageName == SARAe_TIKTOK_PACKAGE) {
                                video_path = Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/"
                                YLog.debug(tag = "VcamPro", msg = "TikTok shared source path: $video_path")
                            }
                        }
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "startPreview",
                object : XC_MethodHook() {
                    @Throws(Throwable::class)
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE && activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        val file = File(video_path + "virtual.mp4")
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (!file.exists()) {
                            if (toastContent != null && needToShowToast) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                            return
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        is_someone_playing = false
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "开始预览"
                        )
                        start_preview_camera = param.thisObject as Camera
                        if (ori_holder != null) {
                            if (mplayer1 == null) {
                                mplayer1 = MediaPlayer()
                            } else {
                                mplayer1!!.release()
                                mplayer1 = null
                                mplayer1 = MediaPlayer()
                            }
                            if (!ori_holder!!.surface.isValid || ori_holder == null) {
                                return
                            }
                            mplayer1!!.setSurface(ori_holder!!.surface)
                            val sfile =
                                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no-silent.jpg")
                            if (!(sfile.exists() && !is_someone_playing)) {
                                mplayer1!!.setVolume(0f, 0f)
                                is_someone_playing = false
                            } else {
                                is_someone_playing = true
                            }
                            startPlaylist(mplayer1!!, ori_holder!!.surface, video_path, !is_someone_playing)
                        }
                        if (mSurfacetexture != null) {
                            if (mSurface == null) {
                                mSurface = Surface(mSurfacetexture)
                            } else {
                                mSurface!!.release()
                                mSurface = Surface(mSurfacetexture)
                            }
                            if (mMediaPlayer == null) {
                                mMediaPlayer = MediaPlayer()
                            } else {
                                mMediaPlayer!!.release()
                                mMediaPlayer = MediaPlayer()
                            }
                            mMediaPlayer!!.setSurface(mSurface)
                            val sfile =
                                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no-silent.jpg")
                            if (!(sfile.exists() && !is_someone_playing)) {
                                mMediaPlayer!!.setVolume(0f, 0f)
                                is_someone_playing = false
                            } else {
                                is_someone_playing = true
                            }
                            startPlaylist(mMediaPlayer!!, mSurface!!, video_path, !is_someone_playing)
                        }
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.hardware.Camera",
                this@encase.appClassLoader,
                "setPreviewDisplay",
                SurfaceHolder::class.java,
                object : XC_MethodHook() {
                    @Throws(Throwable::class)
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (this@encase.packageName == SARAe_TIKTOK_PACKAGE && activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "添加Surfaceview预览"
                        )
                        val file = File(video_path + "virtual.mp4")
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (!file.exists()) {
                            if (toastContent != null && needToShowToast) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                            return
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        mcamera1 = param.thisObject as Camera
                        ori_holder = param.args[0] as SurfaceHolder
                        if (c1_fake_texture == null) {
                            c1_fake_texture = SurfaceTexture(11)
                        } else {
                            c1_fake_texture!!.release()
                            c1_fake_texture = null
                            c1_fake_texture = SurfaceTexture(11)
                        }
                        if (c1_fake_surface == null) {
                            c1_fake_surface = Surface(c1_fake_texture)
                        } else {
                            c1_fake_surface!!.release()
                            c1_fake_surface = null
                            c1_fake_surface = Surface(c1_fake_texture)
                        }
                        is_hooked = true
                        mcamera1!!.setPreviewTexture(c1_fake_texture)
                        param.setResult(null)
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.hardware.camera2.CaptureRequest.Builder",
                this@encase.appClassLoader,
                "addTarget",
                Surface::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        if (param.args[0] == null) {
                            return
                        }
                        if (param.thisObject == null) {
                            return
                        }
                        val file = File(video_path + "virtual.mp4")
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (!file.exists()) {
                            if (toastContent != null && needToShowToast) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                            return
                        }
                        if (param.args[0] == c2_virtual_surface) {
                            return
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        val surfaceInfo = param.args[0].toString()
                        if (surfaceInfo.contains("Surface(name=null)")) {
                            if (c2_reader_Surfcae == null) {
                                c2_reader_Surfcae = param.args[0] as Surface
                            } else {
                                if (c2_reader_Surfcae != param.args[0] && c2_reader_Surfcae_1 == null) {
                                    c2_reader_Surfcae_1 = param.args[0] as Surface
                                }
                            }
                        } else {
                            if (c2_preview_Surfcae == null) {
                                c2_preview_Surfcae = param.args[0] as Surface
                            } else {
                                if (c2_preview_Surfcae != param.args[0] && c2_preview_Surfcae_1 == null) {
                                    c2_preview_Surfcae_1 = param.args[0] as Surface
                                }
                            }
                        }
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "添加目标：" + param.args[0].toString()
                        )
                        param.args[0] = c2_virtual_surface
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.hardware.camera2.CaptureRequest.Builder",
                this@encase.appClassLoader,
                "removeTarget",
                Surface::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        if (param.args[0] == null) {
                            return
                        }
                        if (param.thisObject == null) {
                            return
                        }
                        val file = File(video_path + "virtual.mp4")
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (!file.exists()) {
                            if (toastContent != null && needToShowToast) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                            return
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        val rm_surf = param.args[0] as Surface
                        if (rm_surf == c2_preview_Surfcae) {
                            c2_preview_Surfcae = null
                        }
                        if (rm_surf == c2_preview_Surfcae_1) {
                            c2_preview_Surfcae_1 = null
                        }
                        if (rm_surf == c2_reader_Surfcae_1) {
                            c2_reader_Surfcae_1 = null
                        }
                        if (rm_surf == c2_reader_Surfcae) {
                            c2_reader_Surfcae = null
                        }
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "移除目标：" + param.args[0].toString()
                        )
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.hardware.camera2.CaptureRequest.Builder",
                this@encase.appClassLoader,
                "build",
                object : XC_MethodHook() {
                    @Throws(Throwable::class)
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        if (param.thisObject == null) {
                            return
                        }
                        if (param.thisObject == c2_builder) {
                            return
                        }
                        c2_builder = param.thisObject as CaptureRequest.Builder
                        if (activeCameraSourceMode != "VIRTUAL_VIDEO") return
                        val file = File(video_path + "virtual.mp4")
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (!file.exists() && needToShowToast) {
                            if (toastContent != null) {
                                try {
                                    Toast.makeText(
                                        toastContent, """
     不存在替换视频
     ${this@encase.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                    ).show()
                                } catch (ee: Exception) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "[toast]$ee"
                                    )
                                }
                            }
                            return
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "开始build请求"
                        )
                        processCamera2Play()
                    }
                })

            XposedHelpers.findAndHookMethod(
                "android.media.ImageReader",
                this@encase.appClassLoader,
                "newInstance",
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                Int::class.javaPrimitiveType,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "应用创建了渲染器：宽：" + param.args[0] + " 高：" + param.args[1] + "格式" + param.args[2]
                        )
                        c2OriWidth = param.args[0] as Int
                        c2OriHeight = param.args[1] as Int
                        imageReaderFormat = param.args[2] as Int
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (toastContent != null && needToShowToast) {
                            try {
                                Toast.makeText(
                                    toastContent, """
     应用创建了渲染器：
     宽：${param.args[0]}
     高：${param.args[1]}
     一般只需要宽高比与视频相同
     """.trimIndent(), Toast.LENGTH_SHORT
                                ).show()
                            } catch (e: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "[toast]$e"
                                )
                            }
                        }
                    }
                })
            XposedHelpers.findAndHookMethod("android.hardware.camera2.CameraCaptureSession.CaptureCallback",
                this@encase.appClassLoader,
                "onCaptureFailed",
                CameraCaptureSession::class.java,
                CaptureRequest::class.java,
                CaptureFailure::class.java,
                object : XC_MethodHook() {
                    override fun beforeHookedMethod(param: MethodHookParam) {
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "onCaptureFailed" + "原因：" + (param.args[2] as CaptureFailure).reason
                        )
                    }
                })
        } // if end
    }

    private fun processCamera2Play() {
        if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
            YLog.debug(tag = "VcamPro", msg = "VCAM playback not started: active mode=$activeCameraSourceMode")
            return
        }
        if (c2_reader_Surfcae != null) {
            if (c2_hw_decode_obj != null) {
                c2_hw_decode_obj!!.stopDecode()
                c2_hw_decode_obj = null
            }
            c2_hw_decode_obj = VideoToFrames()
            try {
                if (imageReaderFormat == 256) {
                    c2_hw_decode_obj!!.setSaveFrames("null", OutputImageFormat.JPEG)
                } else {
                    c2_hw_decode_obj!!.setSaveFrames("null", OutputImageFormat.NV21)
                }
                c2_hw_decode_obj!!.set_surfcae(c2_reader_Surfcae)
                c2_hw_decode_obj!!.decode(video_path + "virtual.mp4")
            } catch (throwable: Throwable) {
                YLog.debug(
                    tag = "VcamPro",
                    msg = "$throwable"
                )
            }
        }
        if (c2_reader_Surfcae_1 != null) {
            if (c2_hw_decode_obj_1 != null) {
                c2_hw_decode_obj_1!!.stopDecode()
                c2_hw_decode_obj_1 = null
            }
            c2_hw_decode_obj_1 = VideoToFrames()
            try {
                if (imageReaderFormat == 256) {
                    c2_hw_decode_obj_1!!.setSaveFrames("null", OutputImageFormat.JPEG)
                } else {
                    c2_hw_decode_obj_1!!.setSaveFrames("null", OutputImageFormat.NV21)
                }
                c2_hw_decode_obj_1!!.set_surfcae(c2_reader_Surfcae_1)
                c2_hw_decode_obj_1!!.decode(video_path + "virtual.mp4")
            } catch (throwable: Throwable) {
                YLog.debug(
                    tag = "VcamPro",
                    msg = "$throwable"
                )
            }
        }
        if (c2_preview_Surfcae != null) {
            if (c2_player == null) {
                c2_player = MediaPlayer()
            } else {
                c2_player!!.release()
                c2_player = MediaPlayer()
            }
            c2_player!!.setSurface(c2_preview_Surfcae)
            val sfile =
                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no-silent.jpg")
            if (!sfile.exists()) {
                c2_player!!.setVolume(0f, 0f)
            }
            startPlaylist(c2_player!!, c2_preview_Surfcae!!, video_path, !sfile.exists())
        }
        if (c2_preview_Surfcae_1 != null) {
            if (c2_player_1 == null) {
                c2_player_1 = MediaPlayer()
            } else {
                c2_player_1!!.release()
                c2_player_1 = MediaPlayer()
            }
            c2_player_1!!.setSurface(c2_preview_Surfcae_1)
            val sfile =
                File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no-silent.jpg")
            if (!sfile.exists()) {
                c2_player_1!!.setVolume(0f, 0f)
            }
            startPlaylist(c2_player_1!!, c2_preview_Surfcae_1!!, video_path, !sfile.exists())
        }
        YLog.debug(
            tag = "VcamPro",
            msg = "Camera2处理过程完全执行"
        )
    }

    private fun startPlaylist(player: MediaPlayer, surface: Surface, directory: String, muted: Boolean) {
        val playlist = ArrayList<String>()
        for (number in 1..500) {
            val candidate = File(directory, "playlist/${number.toString().padStart(3, '0')}.mp4")
            if (!candidate.isFile) break
            playlist.add(candidate.absolutePath)
        }
        val sources = playlist.ifEmpty { listOf(File(directory, "virtual.mp4").absolutePath) }
        YLog.debug(tag = "VcamPro", msg = "playlist loaded: ${sources.size} files: ${sources.joinToString()}")
        player.setVideoScalingMode(MediaPlayer.VIDEO_SCALING_MODE_SCALE_TO_FIT)
        player.setOnVideoSizeChangedListener { _, width, height ->
            YLog.debug(tag = "VcamPro", msg = "decoded frame size=${width}x${height}; renderer=scale-to-fit; no injected rotate/mirror/crop")
        }
        player.setOnInfoListener { _, what, extra ->
            if (what == MediaPlayer.MEDIA_INFO_VIDEO_RENDERING_START) {
                YLog.debug(tag = "VcamPro", msg = "virtual video rendering started on TikTok target Surface")
                reportInjectionStatus("VIRTUAL_VIDEO_RENDERING", activeCameraSourceMode, detail = "MediaPlayer rendering callback")
            } else if (what == MediaPlayer.MEDIA_INFO_VIDEO_TRACK_LAGGING) {
                YLog.error(tag = "VcamPro", msg = "virtual video renderer reports track lag extra=$extra")
            }
            false
        }
        var index = 0
        fun playCurrent() {
            try {
                player.reset()
                player.setSurface(surface)
                player.setVolume(if (muted) 0f else 1f, if (muted) 0f else 1f)
                player.setOnPreparedListener {
                    activeVideoPath = sources[index]
                    activeVideoStartedAtMs = android.os.SystemClock.elapsedRealtime()
                    logVideoMetadata(sources[index])
                    YLog.debug(tag = "VcamPro", msg = "playlist start ${index + 1}/${sources.size}: ${sources[index]}")
                    it.start()
                }
                if (sources.size > 1) {
                    player.isLooping = false
                    player.setOnCompletionListener {
                        index = (index + 1) % sources.size
                        YLog.debug(tag = "VcamPro", msg = "playlist completed; advance to ${index + 1}/${sources.size}")
                        playCurrent()
                    }
                    player.setOnErrorListener { _, what, extra ->
                        YLog.debug(tag = "VcamPro", msg = "playlist error on ${index + 1}/${sources.size}: $what/$extra")
                        index = (index + 1) % sources.size
                        playCurrent()
                        true
                    }
                } else {
                    player.isLooping = true
                    player.setOnCompletionListener(null)
                    player.setOnErrorListener(null)
                }
                player.setDataSource(sources[index])
                player.prepareAsync()
            } catch (e: Exception) {
                YLog.debug(tag = "VcamPro", msg = "playlist playback failed: $e")
            }
        }
        playCurrent()
    }

    private fun logVideoMetadata(path: String) {
        val retriever = MediaMetadataRetriever()
        try {
            retriever.setDataSource(path)
            val width = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)
            val height = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)
            val rotation = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)
            val rotationDegrees = rotation?.toIntOrNull() ?: 0
            val displaySize = if (rotationDegrees % 180 == 90) "${height}x${width}" else "${width}x${height}"
            YLog.debug(tag = "VcamPro", msg = "source metadata path=$path encoded=${width}x${height} rotation=${rotationDegrees}deg display=$displaySize; SurfaceTexture transform unused")
        } catch (t: Throwable) {
            YLog.error(tag = "VcamPro", msg = "Cannot read source rotation/dimensions for $path: $t")
        } finally {
            runCatching { retriever.release() }
        }
    }

    private fun createVirtualSurface(): Surface? {
        if (needRecreate) {
            if (c2_virtual_surfaceTexture != null) {
                c2_virtual_surfaceTexture!!.release()
                c2_virtual_surfaceTexture = null
            }
            if (c2_virtual_surface != null) {
                c2_virtual_surface!!.release()
                c2_virtual_surface = null
            }
            c2_virtual_surfaceTexture = SurfaceTexture(15)
            c2_virtual_surface = Surface(c2_virtual_surfaceTexture)
            needRecreate = false
        } else {
            if (c2_virtual_surface == null) {
                needRecreate = true
                c2_virtual_surface = createVirtualSurface()
            }
        }
        YLog.debug(
            tag = "VcamPro",
            msg = "【重建垃圾场】" + c2_virtual_surface.toString()
        )
        return c2_virtual_surface
    }

    private fun processCamera2Init(hooked_class: Class<*>?) {
        XposedHelpers.findAndHookMethod(
            hooked_class,
            "onOpened",
            CameraDevice::class.java,
            object : XC_MethodHook() {
                @Throws(Throwable::class)
                override fun beforeHookedMethod(param: MethodHookParam) {
                    val camera = param.args.firstOrNull() as? CameraDevice
                    val cameraId = camera?.id
                    val requested = requestedCameraSourceModeAtOpen
                    val sourceExists = File(video_path + "virtual.mp4").isFile
                    activeCameraSourceMode = CameraSourceModePolicy.activeMode(requested, sourceExists)
                    if (requested == "VIRTUAL_VIDEO" && !sourceExists) {
                        YLog.error(tag = "VcamPro", msg = "Requested VIRTUAL_VIDEO but source MP4 is missing; fail-safe REAL_CAMERA")
                    }
                    reportInjectionStatus("CAMERA_DEVICE_OPENED", activeCameraSourceMode, cameraId, "requested=$requested sourceExists=$sourceExists")
                    if (camera != null && toastContent != null) {
                        runCatching {
                            val manager = toastContent!!.getSystemService(Context.CAMERA_SERVICE) as CameraManager
                            val characteristics = manager.getCameraCharacteristics(camera.id)
                            val orientation = characteristics.get(CameraCharacteristics.SENSOR_ORIENTATION)
                            val facing = characteristics.get(CameraCharacteristics.LENS_FACING)
                            YLog.debug(tag = "VcamPro", msg = "Camera2 id=${camera.id} sensorOrientation=${orientation}deg lensFacing=$facing; injection applies no sensor/front-camera mirror transform")
                        }.onFailure { YLog.error(tag = "VcamPro", msg = "Could not read camera characteristics: $it") }
                    }
                    needRecreate = true
                    if (c2_hw_decode_obj_1 != null) {
                        c2_hw_decode_obj_1!!.stopDecode()
                        c2_hw_decode_obj_1 = null
                    }
                    if (c2_hw_decode_obj != null) {
                        c2_hw_decode_obj!!.stopDecode()
                        c2_hw_decode_obj = null
                    }
                    if (c2_player != null) {
                        runCatching { c2_player!!.release() }
                        c2_player = null
                    }
                    if (c2_player_1 != null) {
                        runCatching { c2_player_1!!.release() }
                        c2_player_1 = null
                    }
                    c2_preview_Surfcae_1 = null
                    c2_reader_Surfcae_1 = null
                    c2_reader_Surfcae = null
                    c2_preview_Surfcae = null
                    is_first_hook_build = true
                    YLog.debug(
                        tag = "VcamPro",
                        msg = "Camera2 opened; active source mode=$activeCameraSourceMode"
                    )
                    val file = File(video_path + "virtual.mp4")
                    if (activeCameraSourceMode == "VIRTUAL_VIDEO") createVirtualSurface()
                    val toast_control =
                        File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                    needToShowToast = !toast_control.exists()
                    if (!file.exists() && this@HookMain.toastContent?.packageName != SARAe_TIKTOK_PACKAGE) {
                        if (toastContent != null && needToShowToast) {
                            try {
                                Toast.makeText(
                                    toastContent, """
     不存在替换视频
     ${toastContent!!.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                                ).show()
                            } catch (ee: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "[toast]$ee"
                                )
                            }
                        }
                        return
                    }
                    XposedHelpers.findAndHookMethod(
                        param.args[0].javaClass,
                        "createCaptureSession",
                        MutableList::class.java,
                        CameraCaptureSession.StateCallback::class.java,
                        Handler::class.java,
                        object : XC_MethodHook() {
                            @Throws(Throwable::class)
                            override fun beforeHookedMethod(paramd: MethodHookParam) {
                                (paramd.args.getOrNull(1) as? CameraCaptureSession.StateCallback)?.let(::processCamera2sessionCallback)
                                if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
                                    YLog.debug(tag = "VcamPro", msg = "REAL_CAMERA: preserve original Camera2 session outputs=${paramd.args[0]}")
                                    return
                                }
                                if (paramd.args[0] != null) {
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "createCaptureSession创捷捕获，原始:" + paramd.args[0].toString() + "虚拟：" + c2_virtual_surface.toString()
                                    )
                                    paramd.args[0] = Arrays.asList(c2_virtual_surface)
                                    if (paramd.args[1] != null) {
                                        processCamera2sessionCallback(paramd.args[1] as CameraCaptureSession.StateCallback)
                                    }
                                }
                            }
                        })

                    XposedHelpers.findAndHookMethod(
                        param.args[0].javaClass,
                        "createCaptureSessionByOutputConfigurations",
                        MutableList::class.java,
                        CameraCaptureSession.StateCallback::class.java,
                        Handler::class.java,
                        object : XC_MethodHook() {
                            @Throws(Throwable::class)
                            override fun beforeHookedMethod(param: MethodHookParam) {
                                super.beforeHookedMethod(param)
                                (param.args.getOrNull(1) as? CameraCaptureSession.StateCallback)?.let(::processCamera2sessionCallback)
                                if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
                                    YLog.debug(tag = "VcamPro", msg = "REAL_CAMERA: preserve original output configurations=${param.args[0]}")
                                    return
                                }
                                if (param.args[0] != null) {
                                    outputConfiguration = OutputConfiguration(
                                        c2_virtual_surface!!
                                    )
                                    param.args[0] = Arrays.asList(outputConfiguration)
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "执行了createCaptureSessionByOutputConfigurations-144777"
                                    )
                                    if (param.args[1] != null) {
                                        processCamera2sessionCallback(param.args[1] as CameraCaptureSession.StateCallback)
                                    }
                                }
                            }
                        })
                    XposedHelpers.findAndHookMethod(
                        param.args[0].javaClass,
                        "createConstrainedHighSpeedCaptureSession",
                        MutableList::class.java,
                        CameraCaptureSession.StateCallback::class.java,
                        Handler::class.java,
                        object : XC_MethodHook() {
                            @Throws(Throwable::class)
                            override fun beforeHookedMethod(param: MethodHookParam) {
                                super.beforeHookedMethod(param)
                                (param.args.getOrNull(1) as? CameraCaptureSession.StateCallback)?.let(::processCamera2sessionCallback)
                                if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
                                    YLog.debug(tag = "VcamPro", msg = "REAL_CAMERA: preserve original high-speed outputs=${param.args[0]}")
                                    return
                                }
                                if (param.args[0] != null) {
                                    param.args[0] = Arrays.asList(c2_virtual_surface)
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "执行了 createConstrainedHighSpeedCaptureSession -5484987"
                                    )
                                    if (param.args[1] != null) {
                                        processCamera2sessionCallback(param.args[1] as CameraCaptureSession.StateCallback)
                                    }
                                }
                            }
                        })
                    XposedHelpers.findAndHookMethod(
                        param.args[0].javaClass,
                        "createReprocessableCaptureSession",
                        InputConfiguration::class.java,
                        MutableList::class.java,
                        CameraCaptureSession.StateCallback::class.java,
                        Handler::class.java,
                        object : XC_MethodHook() {
                            @Throws(Throwable::class)
                            override fun beforeHookedMethod(param: MethodHookParam) {
                                super.beforeHookedMethod(param)
                                (param.args.getOrNull(2) as? CameraCaptureSession.StateCallback)?.let(::processCamera2sessionCallback)
                                if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
                                    YLog.debug(tag = "VcamPro", msg = "REAL_CAMERA: preserve original reprocessable outputs=${param.args[1]}")
                                    return
                                }
                                if (param.args[1] != null) {
                                    param.args[1] = Arrays.asList(c2_virtual_surface)
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "执行了 createReprocessableCaptureSession "
                                    )
                                    if (param.args[2] != null) {
                                        processCamera2sessionCallback(param.args[2] as CameraCaptureSession.StateCallback)
                                    }
                                }
                            }
                        })
                    XposedHelpers.findAndHookMethod(
                        param.args[0].javaClass,
                        "createReprocessableCaptureSessionByConfigurations",
                        InputConfiguration::class.java,
                        MutableList::class.java,
                        CameraCaptureSession.StateCallback::class.java,
                        Handler::class.java,
                        object : XC_MethodHook() {
                            @Throws(Throwable::class)
                            override fun beforeHookedMethod(param: MethodHookParam) {
                                super.beforeHookedMethod(param)
                                (param.args.getOrNull(2) as? CameraCaptureSession.StateCallback)?.let(::processCamera2sessionCallback)
                                if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
                                    YLog.debug(tag = "VcamPro", msg = "REAL_CAMERA: preserve original reprocessable configurations=${param.args[0]}")
                                    return
                                }
                                if (param.args[1] != null) {
                                    outputConfiguration = OutputConfiguration(
                                        c2_virtual_surface!!
                                    )
                                    param.args[0] = Arrays.asList(outputConfiguration)
                                    YLog.debug(
                                        tag = "VcamPro",
                                        msg = "执行了 createReprocessableCaptureSessionByConfigurations"
                                    )
                                    if (param.args[2] != null) {
                                        processCamera2sessionCallback(param.args[2] as CameraCaptureSession.StateCallback)
                                    }
                                }
                            }
                        })
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                        XposedHelpers.findAndHookMethod(
                            param.args[0].javaClass,
                            "createCaptureSession",
                            SessionConfiguration::class.java,
                            object : XC_MethodHook() {
                                @Throws(Throwable::class)
                                override fun beforeHookedMethod(param: MethodHookParam) {
                                    super.beforeHookedMethod(param)
                                    if (param.args[0] != null) {
                                        sessionConfiguration = param.args[0] as SessionConfiguration
                                        processCamera2sessionCallback(sessionConfiguration!!.stateCallback)
                                        if (activeCameraSourceMode != "VIRTUAL_VIDEO") {
                                            YLog.debug(tag = "VcamPro", msg = "REAL_CAMERA: preserve original SessionConfiguration outputs=${sessionConfiguration!!.outputConfigurations}")
                                            return
                                        }
                                        YLog.debug(
                                            tag = "VcamPro",
                                            msg = "执行了 createCaptureSession -5484987"
                                        )
                                        outputConfiguration = OutputConfiguration(
                                            c2_virtual_surface!!
                                        )
                                        fake_sessionConfiguration = SessionConfiguration(
                                            sessionConfiguration!!.sessionType,
                                            Arrays.asList(outputConfiguration),
                                            sessionConfiguration!!.executor,
                                            sessionConfiguration!!.stateCallback
                                        )
                                        param.args[0] = fake_sessionConfiguration
                                        processCamera2sessionCallback(sessionConfiguration!!.stateCallback)
                                    }
                                }
                            })
                    }
                }
            })
        XposedHelpers.findAndHookMethod(
            hooked_class,
            "onError",
            CameraDevice::class.java,
            Int::class.javaPrimitiveType,
            object : XC_MethodHook() {
                @Throws(Throwable::class)
                override fun beforeHookedMethod(param: MethodHookParam) {
                    YLog.debug(
                        tag = "VcamPro",
                        msg = "相机错误onerror：" + param.args[1] as Int
                    )
                    reportInjectionStatus("CAMERA_DEVICE_ERROR", activeCameraSourceMode, (param.args.firstOrNull() as? CameraDevice)?.id, "code=${param.args[1]}")
                }
            })
        XposedHelpers.findAndHookMethod(
            hooked_class,
            "onDisconnected",
            CameraDevice::class.java,
            object : XC_MethodHook() {
                @Throws(Throwable::class)
                override fun beforeHookedMethod(param: MethodHookParam) {
                    YLog.debug(
                        tag = "VcamPro",
                        msg = "相机断开onDisconnected ："
                    )
                    reportInjectionStatus("CAMERA_DEVICE_DISCONNECTED", activeCameraSourceMode, (param.args.firstOrNull() as? CameraDevice)?.id)
                }
            })
    }

    private fun processAShotJpeg(param: MethodHookParam, index: Int) {
        try {
            YLog.debug(
                tag = "VcamPro",
                msg = "第二个jpeg:" + param.args[index].toString()
            )
        } catch (eee: Exception) {
            YLog.debug(
                tag = "VcamPro",
                msg = "$eee"
            )
        }
        val callback: Class<*> = param.args[index].javaClass
        XposedHelpers.findAndHookMethod(
            callback,
            "onPictureTaken",
            ByteArray::class.java,
            Camera::class.java,
            object : XC_MethodHook() {
                @Throws(Throwable::class)
                override fun beforeHookedMethod(paramd: MethodHookParam) {
                    try {
                        val loaclcam = paramd.args[1] as Camera
                        onemwidth = loaclcam.parameters.previewSize.width
                        onemhight = loaclcam.parameters.previewSize.height
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "JPEG拍照回调初始化：宽：" + onemwidth + "高：" + onemhight + "对应的类：" + loaclcam.toString()
                        )
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (toastContent != null && needToShowToast) {
                            try {
                                Toast.makeText(
                                    toastContent, """
     发现拍照
     宽：$onemwidth
     高：$onemhight
     格式：JPEG
     """.trimIndent(), Toast.LENGTH_SHORT
                                ).show()
                            } catch (e: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "[toast]$e"
                                )
                            }
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        val pict = getBMP(video_path + "1000.bmp")
                        val temp_array = ByteArrayOutputStream()
                        pict.compress(Bitmap.CompressFormat.JPEG, 100, temp_array)
                        val jpeg_data = temp_array.toByteArray()
                        paramd.args[0] = jpeg_data
                    } catch (ee: Exception) {
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "$ee"
                        )
                    }
                }
            })
    }

    private fun processAShotYuv(param: MethodHookParam) {
        try {
            YLog.debug(
                tag = "VcamPro",
                msg = "发现拍照YUV:" + param.args[1].toString()
            )
        } catch (eee: Exception) {
            YLog.debug(
                tag = "VcamPro",
                msg = "$eee"
            )
        }
        val callback: Class<*> = param.args[1].javaClass
        XposedHelpers.findAndHookMethod(
            callback,
            "onPictureTaken",
            ByteArray::class.java,
            Camera::class.java,
            object : XC_MethodHook() {
                @Throws(Throwable::class)
                override fun beforeHookedMethod(paramd: MethodHookParam) {
                    try {
                        val loaclcam = paramd.args[1] as Camera
                        onemwidth = loaclcam.parameters.previewSize.width
                        onemhight = loaclcam.parameters.previewSize.height
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "YUV拍照回调初始化：宽：" + onemwidth + "高：" + onemhight + "对应的类：" + loaclcam.toString()
                        )
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (toastContent != null && needToShowToast) {
                            try {
                                Toast.makeText(
                                    toastContent, """
     发现拍照
     宽：$onemwidth
     高：$onemhight
     格式：YUV_420_888
     """.trimIndent(), Toast.LENGTH_SHORT
                                ).show()
                            } catch (ee: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "[toast]$ee"
                                )
                            }
                        }
                        val control_file =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
                        if (control_file.exists()) {
                            return
                        }
                        input = getYUVByBitmap(getBMP(video_path + "1000.bmp"))
                        paramd.args[0] = input
                    } catch (ee: Exception) {
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "$ee"
                        )
                    }
                }
            })
    }

    private fun processCallback(param: MethodHookParam) {
        val preview_cb_class: Class<*> = param.args[0].javaClass
        var need_stop = 0
        val control_file =
            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "disable.jpg")
        if (control_file.exists()) {
            need_stop = 1
        }
        val file = File(video_path + "virtual.mp4")
        val toast_control =
            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
        needToShowToast = !toast_control.exists()
        if (!file.exists()) {
            if (toastContent != null && needToShowToast) {
                try {
                    Toast.makeText(
                        toastContent, """
     不存在替换视频
     ${toastContent!!.packageName}当前路径：$video_path
     """.trimIndent(), Toast.LENGTH_SHORT
                    ).show()
                } catch (ee: Exception) {
                    YLog.debug(
                        tag = "VcamPro",
                        msg = "[toast]$ee"
                    )
                }
            }
            need_stop = 1
        }
        val finalNeed_stop = need_stop
        XposedHelpers.findAndHookMethod(
            preview_cb_class,
            "onPreviewFrame",
            ByteArray::class.java,
            Camera::class.java,
            object : XC_MethodHook() {
                @Throws(Throwable::class)
                override fun beforeHookedMethod(paramd: MethodHookParam) {
                    val localcam = paramd.args[1] as Camera
                    if (localcam == camera_onPreviewFrame) {
                        while (data_buffer == null) {
                        }
                        System.arraycopy(
                            data_buffer, 0, paramd.args[0], 0, min(
                                data_buffer!!.size.toDouble(),
                                (paramd.args[0] as ByteArray).size.toDouble()
                            )
                                .toInt()
                        )
                    } else {
                        camera_callback_calss = preview_cb_class
                        camera_onPreviewFrame = paramd.args[1] as Camera
                        mwidth = camera_onPreviewFrame!!.parameters.previewSize.width
                        mhight = camera_onPreviewFrame!!.parameters.previewSize.height
                        val frame_Rate = camera_onPreviewFrame!!.parameters.previewFrameRate
                        YLog.debug(
                            tag = "VcamPro",
                            msg = "帧预览回调初始化：宽：" + mwidth + " 高：" + mhight + " 帧率：" + frame_Rate
                        )
                        val toast_control =
                            File(Environment.getExternalStorageDirectory().path + "/DCIM/Camera1/" + "no_toast.jpg")
                        needToShowToast = !toast_control.exists()
                        if (toastContent != null && needToShowToast) {
                            try {
                                Toast.makeText(
                                    toastContent, """
     发现预览
     宽：$mwidth
     高：$mhight
     需要视频分辨率与其完全相同
     """.trimIndent(), Toast.LENGTH_SHORT
                                ).show()
                            } catch (ee: Exception) {
                                YLog.debug(
                                    tag = "VcamPro",
                                    msg = "[toast]$ee"
                                )
                            }
                        }
                        if (finalNeed_stop == 1) {
                            return
                        }
                        if (hw_decode_obj != null) {
                            hw_decode_obj!!.stopDecode()
                        }
                        hw_decode_obj = VideoToFrames()
                        hw_decode_obj!!.setSaveFrames("", OutputImageFormat.NV21)
                        hw_decode_obj!!.decode(video_path + "virtual.mp4")
                        while (data_buffer == null) {
                        }
                        System.arraycopy(
                            data_buffer, 0, paramd.args[0], 0, min(
                                data_buffer!!.size.toDouble(),
                                (paramd.args[0] as ByteArray).size.toDouble()
                            )
                                .toInt()
                        )
                    }
                }
            })
    }

    private fun processCamera2sessionCallback(callback_calss: CameraCaptureSession.StateCallback?) {
        if (callback_calss == null) {
            return
        }
        hookCamera2Callback(callback_calss.javaClass, "onConfigureFailed", "onConfigureFailed")
        hookCamera2Callback(callback_calss.javaClass, "onConfigured", "onConfigured")
        hookCamera2Callback(callback_calss.javaClass, "onClosed", "onClosed")
    }

    private fun hookCamera2Callback(callbackClass: Class<*>, methodName: String, logName: String) {
        val method = resolveCamera2CallbackMethod(callbackClass, methodName)
        if (!hookedCamera2CallbackMethods.add(method)) return
        XposedBridge.hookMethod(method, object : XC_MethodHook() {
            override fun beforeHookedMethod(param: MethodHookParam) {
                YLog.debug(tag = "VcamPro", msg = "$logName ：${param.args[0]}")
                val session = param.args.firstOrNull() as? CameraCaptureSession
                val cameraId = runCatching { session?.device?.id }.getOrNull()
                val sessionMode = synchronized(sessionSourceModes) {
                    if (methodName == "onConfigured" && session != null) {
                        sessionSourceModes[session] = activeCameraSourceMode
                    }
                    session?.let { sessionSourceModes[it] } ?: activeCameraSourceMode
                }
                val event = when (methodName) {
                    "onConfigured" -> if (sessionMode == "REAL_CAMERA") "REAL_CAMERA_ACTIVE" else "VIRTUAL_SESSION_CONFIGURED"
                    "onConfigureFailed" -> "CAMERA_SESSION_CONFIGURE_FAILED"
                    "onClosed" -> "CAMERA_SESSION_CLOSED"
                    else -> "CAMERA_SESSION_$methodName"
                }
                reportInjectionStatus(event, sessionMode, cameraId, "callback=$methodName")
                if (methodName == "onClosed" && session != null) synchronized(sessionSourceModes) { sessionSourceModes.remove(session) }
            }
        })
    }

    //以下代码来源：https://blog.csdn.net/jacke121/article/details/73888732
    @Throws(Throwable::class)
    private fun getBMP(file: String): Bitmap {
        return BitmapFactory.decodeFile(file)
    }

    companion object {
        var mSurface: Surface? = null
        var mSurfacetexture: SurfaceTexture? = null
        var mMediaPlayer: MediaPlayer? = null
        var fake_SurfaceTexture: SurfaceTexture? = null
        var origin_preview_camera: Camera? = null
        var camera_onPreviewFrame: Camera? = null
        var start_preview_camera: Camera? = null

        @JvmField
        @Volatile
        var data_buffer: ByteArray? = byteArrayOf(0)
        var input: ByteArray? = null
        var mhight = 0
        var mwidth = 0
        var is_someone_playing = false
        var is_hooked = false
        var hw_decode_obj: VideoToFrames? = null
        var c2_hw_decode_obj: VideoToFrames? = null
        var c2_hw_decode_obj_1: VideoToFrames? = null
        var c1_fake_texture: SurfaceTexture? = null
        var c1_fake_surface: Surface? = null
        var ori_holder: SurfaceHolder? = null
        var mplayer1: MediaPlayer? = null
        var mcamera1: Camera? = null
        var is_first_hook_build = true
        var onemhight = 0
        var onemwidth = 0
        var camera_callback_calss: Class<*>? = null
        var video_path = "/storage/emulated/0/DCIM/Camera1/"
        var c2_preview_Surfcae: Surface? = null
        var c2_preview_Surfcae_1: Surface? = null
        var c2_reader_Surfcae: Surface? = null
        var c2_reader_Surfcae_1: Surface? = null
        var c2_player: MediaPlayer? = null
        var c2_player_1: MediaPlayer? = null
        var c2_virtual_surface: Surface? = null
        var c2_virtual_surfaceTexture: SurfaceTexture? = null
        var c2_state_cb: CameraDevice.StateCallback? = null
        var c2_builder: CaptureRequest.Builder? = null
        var fake_sessionConfiguration: SessionConfiguration? = null
        var sessionConfiguration: SessionConfiguration? = null
        var outputConfiguration: OutputConfiguration? = null
        var c2_state_callback: Class<*>? = null
        var c2_is_front_camera = false // 是否为前置
        val loggedAudioReadMethods = java.util.concurrent.ConcurrentHashMap.newKeySet<String>()
        @Volatile var requestedCameraSourceModeAtOpen: String = "REAL_CAMERA"
        @Volatile var activeCameraSourceMode: String = "REAL_CAMERA"
        private val audioFeeders = WeakHashMap<Any, VirtualAudioFeeder>()
        private val audioReadStats = WeakHashMap<Any, AudioInjectionStats>()
        private val sessionSourceModes = WeakHashMap<CameraCaptureSession, String>()
        private val audioCallbackCounts = WeakHashMap<Any, Long>()
        private val audioCallbackMetrics = WeakHashMap<Any, PcmMetrics>()
        @Volatile var activeVideoPath: String = "/storage/emulated/0/DCIM/Camera1/playlist/001.mp4"
        @Volatile var activeVideoStartedAtMs: Long = 0L
        private fun rgb2YCbCr420(pixels: IntArray, width: Int, height: Int): ByteArray {
            val len = width * height
            // yuv格式数组大小，y亮度占len长度，u,v各占len/4长度。
            val yuv = ByteArray(len * 3 / 2)
            var y: Int
            var u: Int
            var v: Int
            for (i in 0 until height) {
                for (j in 0 until width) {
                    val rgb = pixels[i * width + j] and 0x00FFFFFF
                    val r = rgb and 0xFF
                    val g = rgb shr 8 and 0xFF
                    val b = rgb shr 16 and 0xFF
                    // 套用公式
                    y = (66 * r + 129 * g + 25 * b + 128 shr 8) + 16
                    u = (-38 * r - 74 * g + 112 * b + 128 shr 8) + 128
                    v = (112 * r - 94 * g - 18 * b + 128 shr 8) + 128
                    y = if (y < 16) 16 else min(y.toDouble(), 255.0).toInt()
                    u = if (u < 0) 0 else min(u.toDouble(), 255.0).toInt()
                    v = if (v < 0) 0 else min(v.toDouble(), 255.0).toInt()
                    // 赋值
                    yuv[i * width + j] = y.toByte()
                    yuv[len + (i shr 1) * width + (j and 1.inv())] = u.toByte()
                    yuv[len + +(i shr 1) * width + (j and 1.inv()) + 1] = v.toByte()
                }
            }
            return yuv
        }

        private fun getYUVByBitmap(bitmap: Bitmap?): ByteArray? {
            if (bitmap == null) {
                return null
            }
            val width = bitmap.width
            val height = bitmap.height
            val size = width * height
            val pixels = IntArray(size)
            bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
            return rgb2YCbCr420(pixels, width, height)
        }
    }
}
