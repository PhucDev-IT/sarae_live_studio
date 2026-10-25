package co.sarae.camera.engine

import co.sarae.camera.bridge.RenderConfiguration
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.max
import kotlin.math.sin

/** Pure geometry: preserve pixel aspect, apply source metadata then consumer calibration. */
internal object RenderGeometry {
    fun positions(videoWidth: Int, videoHeight: Int, mediaClockwise: Int,
                  outputWidth: Int, outputHeight: Int, configuration: RenderConfiguration): FloatArray {
        require(videoWidth > 0 && videoHeight > 0 && outputWidth > 0 && outputHeight > 0)
        val angle = Math.toRadians((configuration.outputRotationDegrees - mediaClockwise).toDouble())
        val c = cos(angle).toFloat(); val s = sin(angle).toFloat()
        val rotatedWidth = abs(c) * videoWidth + abs(s) * videoHeight
        val rotatedHeight = abs(s) * videoWidth + abs(c) * videoHeight
        val scaleX = outputWidth / rotatedWidth; val scaleY = outputHeight / rotatedHeight
        val scale = if (configuration.scaleMode == RenderConfiguration.ScaleMode.FIT) min(scaleX, scaleY) else max(scaleX, scaleY)
        return floatArrayOf(-1f,-1f, 1f,-1f, -1f,1f, 1f,1f).also { points ->
            for (i in points.indices step 2) {
                val x=points[i]*videoWidth/2f; val y=points[i+1]*videoHeight/2f
                val rotatedX=(c*x-s*y)*scale*2f/outputWidth
                points[i]=if(configuration.outputMirrorX) -rotatedX else rotatedX
                points[i+1]=(s*x+c*y)*scale*2f/outputHeight
            }
        }
    }
}
