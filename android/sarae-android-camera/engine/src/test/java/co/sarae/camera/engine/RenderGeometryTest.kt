package co.sarae.camera.engine

import co.sarae.camera.bridge.RenderConfiguration
import org.junit.Assert.*
import org.junit.Test
import kotlin.math.abs

class RenderGeometryTest {
    private fun geometry(rotation: Int=0, mode: RenderConfiguration.ScaleMode=RenderConfiguration.ScaleMode.FIT,
                         mirror: Boolean=false, media: Int=0) = RenderGeometry.positions(576,1024,media,1920,1080,
        RenderConfiguration(rotation,mode,mirror))
    @Test fun portraitFitHasBarsWithoutStretching() {
        val p=geometry()
        assertEquals(0.31640625f,abs(p[0]),0.00001f)
        assertEquals(1f,abs(p[1]),0.00001f)
    }
    @Test fun calibratedQuarterTurnFitsLandscapeExactly() {
        val p=geometry(90)
        assertArrayEquals(floatArrayOf(1f,-1f,1f,1f,-1f,-1f,-1f,1f),p,0.00001f)
    }
    @Test fun sourceMetadataAppliedOnceInClockwiseDirection() {
        assertArrayEquals(geometry(270),geometry(media=90),0.00001f)
        assertArrayEquals(geometry(),geometry(90,media=90),0.00001f)
    }
    @Test fun mirrorIsExplicitAndOnlyChangesHorizontalOutputAxis() {
        val base=geometry(90); val mirrored=geometry(90,mirror=true)
        for(i in 0..3) { assertEquals(-base[i*2],mirrored[i*2],0f); assertEquals(base[i*2+1],mirrored[i*2+1],0f) }
    }
    @Test fun fillCropsUsingUniformPixelScale() {
        val p=geometry(mode=RenderConfiguration.ScaleMode.FILL)
        assertEquals(1f,abs(p[0]),0.00001f)
        assertEquals(3.1604939f,abs(p[1]),0.00001f)
    }
    @Test fun allQuarterTurnsPreserveHandednessWithoutMirror() {
        for(r in listOf(0,90,180,270)) {
            val p=geometry(r)
            val cross=(p[2]-p[0])*(p[5]-p[1])-(p[3]-p[1])*(p[4]-p[0])
            assertTrue("rotation=$r",cross>0)
        }
    }
    @Test fun automaticCorrectionUsesFacingSensorAndDisplay() {
        val front=RenderConfiguration(autoCameraOrientation=true,sensorOrientation=270,frontFacing=true)
        val back=RenderConfiguration(autoCameraOrientation=true,sensorOrientation=90,frontFacing=false)
        assertEquals(90,front.outputRotationDegrees)
        assertEquals(90,back.outputRotationDegrees)
        assertEquals(180,front.copy(displayRotationDegrees=90).outputRotationDegrees)
        assertEquals(0,front.copy(autoCameraOrientation=false).outputRotationDegrees)
        assertEquals(270,front.copy(rotationOverride=270).outputRotationDegrees)
        assertNull(front.rotationOverride)
    }
    @Test fun landscapeSourceAndPortraitTargetPreserveAspect() {
        val p=RenderGeometry.positions(1920,1080,0,1080,1920,RenderConfiguration())
        assertEquals(1f,abs(p[0]),0.00001f)
        assertEquals(0.31640625f,abs(p[1]),0.00001f)
    }
    @Test fun inverseIncludesConfirmedFrontPreviewReflection() {
        val config=RenderConfiguration(autoCameraOrientation=true,sensorOrientation=270,frontFacing=true,consumerMirrorsFrontPreview=true)
        assertTrue(config.outputMirrorX)
        assertNull(config.rotationOverride)
        val raw=RenderGeometry.positions(576,1024,0,1920,1080,config)
        // Measured TikTok front transform: R(270 CCW) * horizontal reflection.
        // Engine is reflection * R(90 CCW), so composition restores source axes.
        val composed=FloatArray(8)
        for(i in 0..3) { val x=-raw[i*2]; val y=raw[i*2+1]; composed[i*2]=y; composed[i*2+1]=-x }
        assertArrayEquals(floatArrayOf(-1f,-1f,1f,-1f,-1f,1f,1f,1f),composed,0.00001f)
        assertFalse(config.copy(autoCameraOrientation=false).outputMirrorX)
        assertFalse(config.copy(frontFacing=false).outputMirrorX)
    }
}
