package co.sarae.camera.bridge

import android.net.Uri
import android.view.Surface

/** A source renders frames to the caller-owned Surface. It never owns the consumer lifecycle. */
interface CameraFrameProvider : AutoCloseable {
    fun configureRender(configuration: RenderConfiguration) {}
    fun start(output: Surface, source: Uri, events: (FrameEvent) -> Unit)
    fun switchSource(source: Uri)
    fun switchNow(source: Uri) = switchSource(source)
    fun switchAfterCurrent(source: Uri) = switchSource(source)
    fun stop()
}

data class FrameEvent(
    val kind: String,
    val detail: String,
    val elapsedRealtimeNs: Long,
    val source: String? = null,
    val frameIndex: Long? = null,
    val presentationTimeUs: Long? = null,
    val fps: Double? = null,
    val outputPresentationTimeNs: Long? = null,
    val surfaceTextureTimestampNs: Long? = null,
    val switchId: Long? = null,
    val receivedRealtimeNs: Long? = null,
    val timingsNs: Map<String, Long> = emptyMap(),
    val videoWidth: Int? = null,
    val videoHeight: Int? = null,
    val mediaRotationDegrees: Int? = null,
)

/** Explicit consumer calibration. Rotation is counterclockwise in the output buffer. */
data class RenderConfiguration(
    val rotationOverride: Int? = null,
    val scaleMode: ScaleMode = ScaleMode.FIT,
    val mirrorX: Boolean = false,
    val expectedWidth: Int = 0,
    val expectedHeight: Int = 0,
    val autoCameraOrientation: Boolean = false,
    val sensorOrientation: Int? = null,
    val frontFacing: Boolean = false,
    val displayRotationDegrees: Int = 0,
    val diagnosticCaptureLabel: String? = null,
    val consumerMirrorsFrontPreview: Boolean = false,
) {
    init {
        require(rotationOverride == null || rotationOverride in listOf(0, 90, 180, 270))
        require(sensorOrientation == null || sensorOrientation in listOf(0, 90, 180, 270))
        require(displayRotationDegrees in listOf(0,90,180,270))
        require(diagnosticCaptureLabel == null || diagnosticCaptureLabel.matches(Regex("[a-z0-9-]{1,32}")))
        require(expectedWidth >= 0 && expectedHeight >= 0)
    }
    val outputRotationDegrees: Int get() {
        rotationOverride?.let { return it }
        if(!autoCameraOrientation || sensorOrientation == null) return 0
        val sign=if(frontFacing) 1 else -1
        val relative=(sensorOrientation-displayRotationDegrees*sign+360)%360
        // Camera preview convention: front CCW, back CW. Inject the inverse.
        val consumerCcw=if(frontFacing) relative else (360-relative)%360
        return (360-consumerCcw)%360
    }
    // Invert the confirmed consumer reflection, rather than mirroring the final MP4.
    val outputMirrorX: Boolean get() = mirrorX.xor(autoCameraOrientation && frontFacing && consumerMirrorsFrontPreview)
    enum class ScaleMode { FIT, FILL }
}
