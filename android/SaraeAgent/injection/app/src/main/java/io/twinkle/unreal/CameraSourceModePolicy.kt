package io.twinkle.unreal

/** Fail-safe policy shared by the camera open path and its unit tests. */
internal object CameraSourceModePolicy {
    const val REAL_CAMERA = "REAL_CAMERA"
    const val VIRTUAL_VIDEO = "VIRTUAL_VIDEO"

    fun activeMode(requestedMode: String, sourceExists: Boolean): String =
        if (requestedMode == VIRTUAL_VIDEO && sourceExists) VIRTUAL_VIDEO else REAL_CAMERA
}
