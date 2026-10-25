package co.sarae.camera.nativeaudio

import java.nio.ByteBuffer

/** Owned recorder only. No symbol interception, target lookup, or global hooks. */
object NativeRecorder {
    init { System.loadLibrary("sarae_native_audio") }
    const val RATE = 48000
    const val CHANNELS = 2
    const val FRAMES = 480
    const val PCM_BYTES = FRAMES * CHANNELS * 2
    const val HEADER_BYTES = 48
    const val PACKET_BYTES = HEADER_BYTES + PCM_BYTES * 2
    // mode: 0 MICROPHONE, 1 VIDEO, 2 MUTED. Immutable per recording.
    external fun create(mode: Int): Long
    external fun createObserved(mode: Int, api: Long): Long
    external fun observerToken(handle: Long): Long
    external fun observerStats(api: Long, token: Long): LongArray
    external fun observerLibraryStats(api: Long): LongArray
    external fun pollObservation(api: Long, token: Long, packet: ByteBuffer): Boolean
    external fun releaseObservation(api: Long, token: Long)
    external fun observerShutdown(api: Long)
    external fun probeEnable(enabled:Boolean)
    external fun probeSelfTest():LongArray
    external fun probeStats():LongArray
    external fun probe20(command:Int):LongArray
    external fun pin20(acquire:Boolean):Long
    external fun start(handle: Long)
    external fun stop(handle: Long)
    external fun destroy(handle: Long)
    external fun push(handle: Long, pcm: ShortArray): Boolean
    external fun inputDepth(handle: Long): Int
    external fun poll(handle: Long, packet: ByteBuffer): Boolean
    // callbacks, outputDrops, missingFrames, enqueueFailures, callbacksInFlight,
    // buffer0, buffer1, recorder, bufferQueue, state, startNs, stopNs
    external fun stats(handle: Long): LongArray
}
