package co.sarae.camera.consumer

import android.annotation.SuppressLint
import android.app.Activity
import android.graphics.Color
import android.graphics.SurfaceTexture
import android.hardware.camera2.*
import android.hardware.camera2.params.OutputConfiguration
import android.hardware.camera2.params.SessionConfiguration
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Surface
import android.view.TextureView
import java.io.File
import java.io.BufferedWriter
import org.json.JSONObject

/** No dependency on engine, controller or bridge. Only Camera2 and pixel observations. */
class ConsumerActivity : Activity(), TextureView.SurfaceTextureListener {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var view: TextureView
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var surface: Surface? = null
    private var generation = 0
    private var frames = 0L
    private var previousNs = 0L
    private var lastSampleNs = 0L
    private var maxGapNs = 0L
    private var gaps = 0
    private var foreground=false
    private var opening=false
    private var retiredDevice:CameraDevice?=null
    private var retiredOnce=false
    private var encoderProbe:EncoderProbe?=null
    private lateinit var jsonLog: BufferedWriter

    private fun event(kind: String, detail: String) {
        val ns=SystemClock.elapsedRealtimeNanos()
        jsonLog.write(JSONObject().put("kind",kind).put("ns",ns).put("generation",generation).put("frames",frames).put("detail",detail).toString())
        jsonLog.newLine()
        if (frames%30L==0L || kind != "frame") jsonLog.flush()
        if (kind != "frame" || frames%300L==0L) Log.i("SaraeConsumer","ns=$ns event=$kind generation=$generation frames=$frames $detail")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val dir=File(getExternalFilesDir(null),"phase3").apply { mkdirs() }
        jsonLog=File(dir,"consumer-${android.os.Process.myPid()}.jsonl").bufferedWriter()
        view = TextureView(this).also { it.surfaceTextureListener = this }
        setContentView(view)
        event("create", "pid=${android.os.Process.myPid()}")
    }

    @SuppressLint("MissingPermission")
    override fun onSurfaceTextureAvailable(texture: SurfaceTexture, width: Int, height: Int) {
        openCamera(texture)
    }

    @SuppressLint("MissingPermission")
    private fun openCamera(texture: SurfaceTexture) {
        if (!foreground || opening || device != null) return
        opening=true
        val width=intent.getIntExtra("testWidth",640)
        val height=intent.getIntExtra("testHeight",480)
        require(width in 1..1920 && height in 1..1920)
        texture.setDefaultBufferSize(width, height)
        event("consumer_output","size=${width}x$height")
        surface = if(intent.getBooleanExtra("testEncodeCapture",false)) {
            if(encoderProbe==null)encoderProbe=EncoderProbe(File(getExternalFilesDir(null),"phase13-encoded.mp4")) {kind,detail->handler.post {event(kind,detail)}}
            encoderProbe!!.surface
        } else Surface(texture)
        getSystemService(CameraManager::class.java).openCamera("0", object : CameraDevice.StateCallback() {
            override fun onOpened(camera: CameraDevice) {
                opening=false
                if (!foreground) { camera.close(); return }
                if(intent.getBooleanExtra("testRetiredClose",false) && !retiredOnce) {
                    retiredOnce=true;retiredDevice=camera
                    camera.close()
                    event("retired_device_closed","identity=${System.identityHashCode(camera)}")
                    handler.postDelayed({if(foreground)openCamera(texture)},700)
                    return
                }
                device = camera
                val method=intent.getStringExtra("testSessionMethod") ?: "legacy"
                val prebuilt=if(intent.getBooleanExtra("testPrebuiltRequest",false))
                    camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                        addTarget(surface!!);setTag("phase13-prebuilt")
                    }.build().also {event("request_prebuilt","surface=${surface!!.hashCode()} tag=${it.tag}")} else null
                val callback=object : CameraCaptureSession.StateCallback() {
                    override fun onConfigured(configured: CameraCaptureSession) {
                        session = configured; generation++
                        val request = prebuilt ?: camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply { addTarget(surface!!) }.build()
                        val capture=if(intent.getBooleanExtra("testCaptureCallbacks",false)) object:CameraCaptureSession.CaptureCallback() {
                            private var completed=0
                            override fun onCaptureCompleted(s:CameraCaptureSession,r:CaptureRequest,result:TotalCaptureResult) {
                                completed++
                                if(completed==1 || completed%30==0)event("capture_completed","count=$completed frame=${result.frameNumber} sensorNs=${result.get(CaptureResult.SENSOR_TIMESTAMP)} requestSame=${r===request} tag=${r.tag}")
                            }
                            override fun onCaptureFailed(s:CameraCaptureSession,r:CaptureRequest,failure:CaptureFailure) {event("capture_failed","frame=${failure.frameNumber} reason=${failure.reason}")}
                            override fun onCaptureBufferLost(s:CameraCaptureSession,r:CaptureRequest,target:Surface,frameNumber:Long) {event("capture_buffer_lost","target=${target.hashCode()} frame=$frameNumber")}
                        } else null
                        try {
                            configured.setRepeatingRequest(request, capture, handler)
                        } catch(t:Exception) {
                            event("request_submit_error","${t.javaClass.name}: ${t.message}")
                            if(prebuilt==null)throw t
                            // Negative mechanism test: keep the Surface alive to show that EGL
                            // pixels alone can continue while Camera2 has no capture results.
                            return
                        }
                        event("session_started", "method=$method surface=${surface!!.hashCode()} thread=${Thread.currentThread().name}")
                        if(intent.getBooleanExtra("testRetiredClose",false))handler.postDelayed({
                            event("retired_device_close_again","identity=${retiredDevice?.let{System.identityHashCode(it)}} active=${System.identityHashCode(camera)}")
                            retiredDevice?.close()
                        },2500)
                        if(intent.getBooleanExtra("testEncodeCapture",false))handler.postDelayed({
                            stopCamera();finish()
                        },8000)
                    }
                    override fun onConfigureFailed(session: CameraCaptureSession) { event("error", "configure_failed") }
                }
                event("session_request", "method=$method surface=${surface!!.hashCode()}")
                if (method == "modern" || method == "shared") {
                    val output=OutputConfiguration(surface!!)
                    // Negative case verifies that unsupported metadata is left untouched.
                    if (method == "shared") output.enableSurfaceSharing()
                    val config=SessionConfiguration(SessionConfiguration.SESSION_REGULAR,
                        listOf(output), mainExecutor, callback)
                    config.sessionParameters=camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).build()
                    camera.createCaptureSession(config)
                } else {
                    require(method == "legacy")
                    camera.createCaptureSession(listOf(surface!!), callback, handler)
                }
            }
            override fun onDisconnected(camera: CameraDevice) { opening=false; event("error", "disconnected"); camera.close() }
            override fun onError(camera: CameraDevice, error: Int) { opening=false; event("error", "camera=$error"); camera.close() }
        }, handler)
    }

    override fun onSurfaceTextureUpdated(texture: SurfaceTexture) {
        val now = SystemClock.elapsedRealtimeNanos()
        frames++
        val gap = if (previousNs == 0L) 0L else now - previousNs
        previousNs = now
        maxGapNs = maxOf(maxGapNs, gap)
        if (gap > 100_000_000L) gaps++
        run {
            lastSampleNs = now
            val bitmap = view.getBitmap(16, 16) ?: return
            var red = 0; var blue = 0; var green = 0
            for (y in 0 until 16) for (x in 0 until 16) {
                val color = bitmap.getPixel(x, y)
                red += Color.red(color); green += Color.green(color); blue += Color.blue(color)
            }
            bitmap.recycle()
            red /= 256; green /= 256; blue /= 256
            val pattern = if (red > 200 && blue < 40 && green < 40) "A" else if (blue > 200 && red < 40 && green < 40) "B" else "PHYSICAL_OR_UNKNOWN"
            event("frame", "timestampNs=${texture.timestamp} gapNs=$gap pattern=$pattern black=${red<10 && green<10 && blue<10} rgb=$red,$green,$blue maxGapNs=$maxGapNs gapsOver100ms=$gaps")
        }
    }
    override fun onSurfaceTextureSizeChanged(texture: SurfaceTexture, width: Int, height: Int) {}
    override fun onSurfaceTextureDestroyed(texture: SurfaceTexture): Boolean {
        stopCamera()
        event("stop", "maxGapNs=$maxGapNs gapsOver100ms=$gaps")
        return true
    }

    private fun stopCamera() {
        session?.close(); session=null
        device?.close(); device=null
        encoderProbe?.close();encoderProbe=null
        surface?.release(); surface=null
    }
    override fun onStart() {
        super.onStart(); foreground=true
        if (view.isAvailable) view.surfaceTexture?.let { openCamera(it) }
    }
    override fun onStop() {
        foreground=false; stopCamera()
        event("background","camera closed; consumer Surface may be retained by TextureView")
        super.onStop()
    }
}
