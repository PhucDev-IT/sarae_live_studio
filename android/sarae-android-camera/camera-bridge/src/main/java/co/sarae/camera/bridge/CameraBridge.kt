package co.sarae.camera.bridge

import android.view.Surface

/** Camera2 test consumer. Session generation changes only when Camera2 is recreated. */
interface CameraBridge : AutoCloseable {
    val sessionGeneration: Int
    fun start(output: Surface, events: (String, String) -> Unit)
    fun stop()
}
