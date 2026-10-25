package co.sarae.camera.poc

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.SurfaceTexture
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Surface
import android.view.TextureView
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import co.sarae.camera.bridge.Camera2PreviewBridge
import co.sarae.camera.bridge.FrameEvent
import co.sarae.camera.engine.LoopingMp4Provider

/** Own Camera2 consumer beside a stable MP4 output Surface; no third-party target is involved. */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var log: RunLog
    private lateinit var camera: Camera2PreviewBridge
    private lateinit var provider: LoopingMp4Provider
    private lateinit var cameraView: TextureView
    private lateinit var videoView: TextureView
    private lateinit var status: TextView
    private val clips = arrayOfNulls<Uri>(2)
    private var cameraSurface: Surface? = null
    private var videoSurface: Surface? = null
    private var providerStarted = false
    private var cameraStarted = false
    private var autoSwitch = false
    private var nextSlot = 0
    private var switchNumber = 0
    private var passedSwitches = 0
    private data class PendingSwitch(
        val number: Int,
        val source: Uri,
        val cameraGeneration: Int,
        val videoSurfaceHash: Int?,
        val requestedNs: Long,
    )
    @Volatile private var pendingSwitch: PendingSwitch? = null
    private var lastFrameNs = 0L
    private var videoFrames = 0L
    private var cameraFrames = 0L
    private var lastCameraFrameNs = 0L
    private var cameraFpsWindowNs = 0L
    private var cameraFpsFrames = 0
    private var cameraFps = 0.0
    private var previousExceptionHandler: Thread.UncaughtExceptionHandler? = null

    private val autoTick = object : Runnable {
        override fun run() {
            if (!autoSwitch) return
            val slot = nextSlot
            nextSlot = 1 - nextSlot
            switchTo(slot)
            ui.postDelayed(this, 3_000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        log = RunLog(this)
        camera = Camera2PreviewBridge(this)
        provider = LoopingMp4Provider(this)
        previousExceptionHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try { log.record("crash", "thread=${thread.name} stack=${throwable.stackTraceToString()}") }
            catch (_: Throwable) {}
            previousExceptionHandler?.uncaughtException(thread, throwable)
        }
        buildUi()
        log.record("lifecycle", "onCreate sdk=${android.os.Build.VERSION.SDK_INT} model=${android.os.Build.MODEL} abi=${android.os.Build.SUPPORTED_ABIS.joinToString()}")
        log.record("log_file", log.file.absolutePath)
        loadDebugClips()
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(16, 16, 16, 16) }
        status = TextView(this).apply { textSize = 14f; text = "Choose MP4 A and B. Camera permission is required." }
        root.addView(status)
        root.addView(TextView(this).apply { text = "Camera2 test consumer (real camera)" })
        cameraView = TextureView(this)
        root.addView(cameraView, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        root.addView(TextView(this).apply { text = "MediaCodec MP4 output (same Surface across switches)" })
        videoView = TextureView(this)
        root.addView(videoView, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        val controls = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        controls.addView(button("Choose MP4 A") { choose(0) })
        controls.addView(button("Choose MP4 B") { choose(1) })
        controls.addView(button("Switch to A") { switchTo(0) })
        controls.addView(button("Switch to B") { switchTo(1) })
        controls.addView(button("Auto switch every 3 seconds") {
            autoSwitch = !autoSwitch
            log.record("auto_switch", "enabled=$autoSwitch")
            ui.removeCallbacks(autoTick)
            if (autoSwitch) ui.post(autoTick)
            updateStatus()
        })
        val scroll = ScrollView(this).apply { addView(controls) }
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)
        cameraView.surfaceTextureListener = textureListener(false)
        videoView.surfaceTextureListener = textureListener(true)
    }

    private fun button(label: String, click: () -> Unit) = Button(this).apply {
        text = label
        setOnClickListener { click() }
    }

    private fun textureListener(video: Boolean) = object : TextureView.SurfaceTextureListener {
        override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
            texture.setDefaultBufferSize(1280, 720)
            val surface = Surface(texture)
            if (video) {
                videoSurface = surface
                log.record("surface_available", "video surface=${surface.hashCode()} size=${width}x$height")
                clips.firstOrNull { it != null }?.let { startVideo(it) }
            } else {
                cameraSurface = surface
                cameraFpsWindowNs = SystemClock.elapsedRealtimeNanos()
                log.record("surface_available", "camera2 surface=${surface.hashCode()} size=${width}x$height")
                startCameraIfPermitted()
            }
        }
        override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {
            log.record("surface_resized", "video=$video size=${width}x$height")
        }
        override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
            log.record("surface_destroyed", "video=$video")
            if (video) { provider.stop(); providerStarted = false; videoSurface?.release(); videoSurface = null }
            else { camera.stop(); cameraStarted = false; cameraSurface?.release(); cameraSurface = null }
            return true
        }
        override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {
            val now = SystemClock.elapsedRealtimeNanos()
            if (video) {
                videoFrames++
                if (videoFrames % 30L == 0L) log.record("surface_frame", "video count=$videoFrames timestampNs=${texture.timestamp}")
            } else {
                cameraFrames++
                lastCameraFrameNs = now
                cameraFpsFrames++
                val elapsed = now - cameraFpsWindowNs
                if (elapsed >= 1_000_000_000L) {
                    cameraFps = cameraFpsFrames * 1_000_000_000.0 / elapsed
                    cameraFpsFrames = 0; cameraFpsWindowNs = now
                    log.record("camera2_fps", "fps=$cameraFps frames=$cameraFrames generation=${camera.sessionGeneration} timestampNs=${texture.timestamp}")
                    updateStatus()
                }
            }
        }
    }

    private fun choose(slot: Int) {
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "video/mp4"
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION)
        }
        startActivityForResult(intent, 100 + slot)
    }

    /** ADB smoke-run input is limited to this debug app's own external files directory. */
    private fun loadDebugClips() {
        if (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return
        val ownDir = requireNotNull(getExternalFilesDir(null)).canonicalFile
        for (slot in 0..1) {
            val path = intent.getStringExtra("clip${if (slot == 0) "A" else "B"}") ?: continue
            try {
                val file = java.io.File(path).canonicalFile
                require(file.parentFile == ownDir && file.isFile) { "clip must be a file in app external files directory" }
                clips[slot] = Uri.fromFile(file)
                log.record("clip_selected", "debug slot=$slot file=${file.name} bytes=${file.length()}")
            } catch (t: Exception) { log.record("clip_error", "slot=$slot ${t.message}") }
        }
        if (intent.getBooleanExtra("autoSwitch", false) && clips.all { it != null }) {
            autoSwitch = true
            ui.postDelayed(autoTick, 5_000)
            log.record("auto_switch", "enabled=true via debug intent intervalMs=3000")
        }
    }

    @Deprecated("Activity result API keeps this dependency-free PoC small")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (resultCode != RESULT_OK || requestCode !in 100..101) return
        val uri = data?.data ?: return
        try { contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION) }
        catch (t: Exception) { log.record("uri_permission_error", t.toString()) }
        clips[requestCode - 100] = uri
        log.record("clip_selected", "slot=${requestCode - 100} uri=$uri")
        if (!providerStarted) startVideo(uri)
        updateStatus()
    }

    private fun startVideo(uri: Uri) {
        val surface = videoSurface ?: return
        providerStarted = true
        provider.start(surface, uri, ::onFrameEvent)
        log.record("provider_start", "surface=${surface.hashCode()} source=$uri")
    }

    private fun switchTo(slot: Int) {
        val uri = clips[slot] ?: run { log.record("switch_rejected", "slot=$slot missing clip"); return }
        if (!providerStarted) { startVideo(uri); return }
        switchNumber++
        pendingSwitch = PendingSwitch(switchNumber, uri, camera.sessionGeneration,
            videoSurface?.hashCode(), SystemClock.elapsedRealtimeNanos())
        log.record("switch_requested_ui", "number=$switchNumber slot=$slot cameraGeneration=${camera.sessionGeneration} cameraFrames=$cameraFrames videoSurface=${videoSurface?.hashCode()}")
        provider.switchSource(uri)
        updateStatus()
    }

    private fun onFrameEvent(event: FrameEvent) {
        log.record(event.kind, event.detail, mapOf(
            "source" to event.source, "frameIndex" to event.frameIndex,
            "presentationTimeUs" to event.presentationTimeUs, "fps" to event.fps,
            "decoderElapsedRealtimeNs" to event.elapsedRealtimeNs,
        ))
        if (event.kind == "frame_rendered") {
            lastFrameNs = event.elapsedRealtimeNs
            val pending = pendingSwitch
            if (pending != null && event.source == pending.source.toString() &&
                event.elapsedRealtimeNs >= pending.requestedNs) {
                pendingSwitch = null
                ui.postDelayed({
                    val cameraAdvanced = lastCameraFrameNs > pending.requestedNs
                    val same = pending.cameraGeneration > 0 && camera.sessionGeneration == pending.cameraGeneration
                    val surfaceStable = videoSurface?.hashCode() == pending.videoSurfaceHash
                    if (same && cameraAdvanced && surfaceStable) passedSwitches++
                    log.record("switch_continuity", "number=${pending.number} result=${if (same && cameraAdvanced && surfaceStable) "PASS" else "FAIL"} generationBefore=${pending.cameraGeneration} generationAfter=${camera.sessionGeneration} cameraAdvanced=$cameraAdvanced surfaceStable=$surfaceStable videoFrame=${event.frameIndex} source=${event.source}")
                    updateStatus()
                }, 150)
            }
        }
        if (event.kind == "decoder_error") ui.post {
            providerStarted = false
            pendingSwitch = null
            updateStatus("Decoder error: ${event.detail}")
        }
    }

    private fun startCameraIfPermitted() {
        val surface = cameraSurface ?: return
        if (cameraStarted) return
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), 7); return
        }
        cameraStarted = true
        camera.start(surface) { kind, detail ->
            log.record(kind, detail)
            ui.post { updateStatus() }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        log.record("camera_permission", "granted=${grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED}")
        if (requestCode == 7 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startCameraIfPermitted()
    }

    private fun updateStatus(message: String? = null) {
        status.text = message ?: "Camera2 session=${camera.sessionGeneration}, frames=$cameraFrames, fps=${"%.1f".format(cameraFps)}\n" +
            "MP4 surface frames=$videoFrames, switches=$switchNumber, continuity PASS=$passedSwitches\n" +
            "Auto switch=$autoSwitch\nLog: ${log.file.absolutePath}"
    }

    override fun onStop() {
        super.onStop()
        autoSwitch = false; ui.removeCallbacks(autoTick)
        provider.stop(); providerStarted = false
        camera.stop(); cameraStarted = false
        log.record("lifecycle", "onStop")
    }

    override fun onStart() {
        super.onStart()
        if (::log.isInitialized) {
            log.record("lifecycle", "onStart")
            startCameraIfPermitted()
            clips.firstOrNull { it != null }?.let { if (!providerStarted) startVideo(it) }
        }
    }

    override fun onDestroy() {
        log.record("lifecycle", "onDestroy switches=$switchNumber pass=$passedSwitches")
        provider.close(); camera.close()
        Thread.setDefaultUncaughtExceptionHandler(previousExceptionHandler)
        log.close()
        super.onDestroy()
    }
}
