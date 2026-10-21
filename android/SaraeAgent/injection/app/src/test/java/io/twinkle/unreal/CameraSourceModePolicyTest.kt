package io.twinkle.unreal

import org.junit.Assert.assertEquals
import org.junit.Test

class CameraSourceModePolicyTest {
    @Test
    fun explicitRealCameraAlwaysKeepsOriginalCameraOutput() {
        assertEquals(
            CameraSourceModePolicy.REAL_CAMERA,
            CameraSourceModePolicy.activeMode(CameraSourceModePolicy.REAL_CAMERA, sourceExists = true)
        )
    }

    @Test
    fun virtualVideoRequiresAnExistingSourceAndOtherwiseFallsBackToRealCamera() {
        assertEquals(
            CameraSourceModePolicy.VIRTUAL_VIDEO,
            CameraSourceModePolicy.activeMode(CameraSourceModePolicy.VIRTUAL_VIDEO, sourceExists = true)
        )
        assertEquals(
            CameraSourceModePolicy.REAL_CAMERA,
            CameraSourceModePolicy.activeMode(CameraSourceModePolicy.VIRTUAL_VIDEO, sourceExists = false)
        )
    }

    @Test
    fun unknownModeFailsClosedToRealCamera() {
        assertEquals(CameraSourceModePolicy.REAL_CAMERA, CameraSourceModePolicy.activeMode("invalid", true))
    }
}
