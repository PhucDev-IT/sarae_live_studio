package co.sarae.camera.bridge

import android.annotation.SuppressLint
import android.content.Context
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.os.Handler
import android.os.HandlerThread
import android.view.Surface

/** Only opens this app's real Camera2 preview; it performs no injection or hooking. */
class Camera2PreviewBridge(context: Context) : CameraBridge {
    private val manager = context.getSystemService(CameraManager::class.java)
    private val thread = HandlerThread("sarae-camera2").apply { start() }
    private val handler = Handler(thread.looper)
    private var device: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var output: Surface? = null
    private var events: (String, String) -> Unit = { _, _ -> }
    @Volatile override var sessionGeneration: Int = 0
        private set

    @SuppressLint("MissingPermission") // caller requests CAMERA permission before start
    override fun start(output: Surface, events: (String, String) -> Unit) {
        handler.post {
            if (device != null || session != null) {
                events("camera2_already_started", "generation=$sessionGeneration")
                return@post
            }
            this.output = output
            this.events = events
            try {
                val id = manager.cameraIdList.firstOrNull {
                    manager.getCameraCharacteristics(it).get(CameraCharacteristics.LENS_FACING) ==
                        CameraCharacteristics.LENS_FACING_BACK
                } ?: manager.cameraIdList.first()
                events("camera2_open_request", "id=$id surface=${output.hashCode()}")
                manager.openCamera(id, object : CameraDevice.StateCallback() {
                    override fun onOpened(camera: CameraDevice) {
                        device = camera
                        val preview = this@Camera2PreviewBridge.output
                        if (preview == null) { camera.close(); return }
                        try {
                            camera.createCaptureSession(listOf(preview), object : CameraCaptureSession.StateCallback() {
                                override fun onConfigured(configured: CameraCaptureSession) {
                                    session = configured
                                    sessionGeneration++
                                    try {
                                        val request = camera.createCaptureRequest(CameraDevice.TEMPLATE_PREVIEW).apply {
                                            addTarget(preview)
                                            set(CaptureRequest.CONTROL_AF_MODE, CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE)
                                        }.build()
                                        configured.setRepeatingRequest(request, null, handler)
                                        events("camera2_session_started", "generation=$sessionGeneration surface=${preview.hashCode()}")
                                    } catch (t: Exception) { events("camera2_error", t.toString()) }
                                }
                                override fun onConfigureFailed(failed: CameraCaptureSession) {
                                    events("camera2_error", "configure_failed")
                                    failed.close()
                                }
                            }, handler)
                        } catch (t: Exception) { events("camera2_error", t.toString()) }
                    }
                    override fun onDisconnected(camera: CameraDevice) {
                        events("camera2_disconnected", "generation=$sessionGeneration")
                        camera.close(); device = null
                    }
                    override fun onError(camera: CameraDevice, error: Int) {
                        events("camera2_error", "code=$error generation=$sessionGeneration")
                        camera.close(); device = null
                    }
                }, handler)
            } catch (t: Exception) { events("camera2_error", t.toString()) }
        }
    }

    override fun stop() {
        handler.post {
            events("camera2_stop", "generation=$sessionGeneration")
            session?.close(); session = null
            device?.close(); device = null
            output = null
        }
    }

    override fun close() {
        stop()
        handler.post { thread.quitSafely() }
    }
}
