package io.twinkle.unreal

import android.hardware.camera2.CameraCaptureSession
import org.junit.Assert.assertEquals
import org.junit.Test

class Camera2CallbackResolutionTest {
    private open class InheritedCloseCallback : CameraCaptureSession.StateCallback() {
        override fun onConfigured(session: CameraCaptureSession) = Unit
        override fun onConfigureFailed(session: CameraCaptureSession) = Unit
    }

    private class OverriddenCloseCallback : InheritedCloseCallback() {
        override fun onClosed(session: CameraCaptureSession) = Unit
    }

    @Test
    fun resolvesInheritedOnClosedOnFrameworkBaseClass() {
        val method = resolveCamera2CallbackMethod(InheritedCloseCallback::class.java, "onClosed")
        assertEquals(CameraCaptureSession.StateCallback::class.java, method.declaringClass)
    }

    @Test
    fun prefersConcreteCallbackOverride() {
        val method = resolveCamera2CallbackMethod(OverriddenCloseCallback::class.java, "onClosed")
        assertEquals(OverriddenCloseCallback::class.java, method.declaringClass)
    }
}
