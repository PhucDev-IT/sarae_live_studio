package co.sarae.camera.observation

/** Explicitly loaded by the lab Xposed module. No static automatic loading. */
object NativeObservation {
    external fun apiAddress(): Long
    external fun diagnostics(): LongArray
}
