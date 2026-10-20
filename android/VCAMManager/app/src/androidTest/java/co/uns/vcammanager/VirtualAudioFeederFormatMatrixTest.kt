package co.uns.vcammanager

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.AudioTimestamp
import android.os.SystemClock
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.twinkle.unreal.PcmMetrics
import io.twinkle.unreal.VirtualAudioFeeder
import java.io.File
import java.nio.ByteBuffer
import java.util.Locale
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VirtualAudioFeederFormatMatrixTest {
    private data class Profile(val rate: Int, val channels: Int) {
        override fun toString() = "${rate}Hz/${channels}ch"
    }

    @Test
    fun decodesResamplesAndPlaysMonoStereoAt44100And48000() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val sources = listOf(
            "VCAM-harness-av-44k1-mono-4s.mp4" to (44100 to 1),
            "VCAM-harness-av2-6s.mp4" to (48000 to 2)
        )
        val profiles = listOf(Profile(44100, 1), Profile(44100, 2), Profile(48000, 1), Profile(48000, 2))
        val sourceFiles = sources.associate { (asset, expected) ->
            val output = File(context.cacheDir, asset)
            instrumentation.context.assets.open(asset).use { input -> output.outputStream().use(input::copyTo) }
            output.absolutePath to expected
        }

        sourceFiles.forEach { (path, sourceFormat) ->
            profiles.forEach { target ->
                val label = "${File(path).name} ${target}"
                val feeder = VirtualAudioFeeder(listOf(path), target.rate, target.channels, 0, 0, paceReads = false)
                val channelMask = if (target.channels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
                val minBuffer = AudioTrack.getMinBufferSize(target.rate, channelMask, AudioFormat.ENCODING_PCM_16BIT)
                assertTrue("$label AudioTrack buffer: $minBuffer", minBuffer > 0)
                val track = AudioTrack.Builder()
                    .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(target.rate).setChannelMask(channelMask).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                    .setBufferSizeInBytes(minBuffer * 2)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
                try {
                    assertEquals("$label source rate", sourceFormat.first, feeder.sourceRate)
                    assertEquals("$label source channels", sourceFormat.second, feeder.sourceChannels)
                    track.setVolume(0f)
                    val pcm = ByteArray(8192)
                    val buffer = ByteBuffer.wrap(pcm)
                    val metrics = PcmMetrics(target.rate, target.channels)
                    val targetBytes = target.rate * target.channels * 2
                    var totalBytes = 0
                    var readMaxNs = 0L
                    var readTotalNs = 0L
                    var writeMaxNs = 0L
                    var writeTotalNs = 0L
                    var writeCalls = 0L
                    var trackStarted = false
                    var blocks = 0
                    while (totalBytes < targetBytes) {
                        buffer.clear()
                        val readStart = SystemClock.elapsedRealtimeNanos()
                        val count = feeder.read(buffer, pcm.size)
                        val readNs = SystemClock.elapsedRealtimeNanos() - readStart
                        readMaxNs = maxOf(readMaxNs, readNs)
                        readTotalNs += readNs
                        assertTrue("$label decoder returned $count bytes", count > 0)
                        assertEquals("$label frame alignment", 0, count % (target.channels * 2))
                        metrics.add(pcm, count)
                        var offset = 0
                        while (offset < count) {
                            val writeStart = SystemClock.elapsedRealtimeNanos()
                            val written = track.write(pcm, offset, count - offset, AudioTrack.WRITE_BLOCKING)
                            val writeNs = SystemClock.elapsedRealtimeNanos() - writeStart
                            writeMaxNs = maxOf(writeMaxNs, writeNs)
                            writeTotalNs += writeNs
                            writeCalls++
                            assertTrue("$label AudioTrack.write returned $written", written > 0)
                            offset += written
                        }
                        if (!trackStarted) {
                            track.play()
                            trackStarted = true
                        }
                        totalBytes += count
                        blocks++
                    }
                    val streamUnderruns = track.underrunCount
                    SystemClock.sleep(150)
                    val firstTimestamp = AudioTimestamp()
                    val hasFirstTimestamp = track.getTimestamp(firstTimestamp)
                    SystemClock.sleep(50)
                    val secondTimestamp = AudioTimestamp()
                    val timestampDrift = if (hasFirstTimestamp && track.getTimestamp(secondTimestamp)) {
                        val frameDeltaMs = (secondTimestamp.framePosition - firstTimestamp.framePosition) * 1000.0 / target.rate
                        val clockDeltaMs = (secondTimestamp.nanoTime - firstTimestamp.nanoTime) / 1_000_000.0
                        "${"%.1f".format(Locale.US, frameDeltaMs - clockDeltaMs)}ms"
                    } else "unavailable"
                    val underruns = streamUnderruns
                    val rms = metrics.rms()
                    val peak = metrics.peak()
                    val result = "PASS $label source=${sourceFormat.first}Hz/${sourceFormat.second}ch bytes=$totalBytes blocks=$blocks rms=$rms peak=$peak decodeAvgMs=${readTotalNs / blocks / 1_000_000.0} decodeMaxMs=${readMaxNs / 1_000_000.0} writeAvgMs=${writeTotalNs / maxOf(writeCalls, 1L) / 1_000_000.0} writeMaxMs=${writeMaxNs / 1_000_000.0} underruns=$underruns audioTimestampDrift=$timestampDrift"
                    Log.i("VcamAudioMatrix", result)
                    assertTrue("$label PCM must not be silent", rms > 0.001)
                    assertTrue("$label peak must be non-zero", peak > 0)
                    assertTrue("$label output duration=${metrics.durationMs(totalBytes.toLong())}ms", kotlin.math.abs(metrics.durationMs(totalBytes.toLong()) - 1000L) <= 100L)
                    assertEquals("$label must not underrun while PCM is being supplied", 0, streamUnderruns)
                } finally {
                    runCatching { track.stop() }
                    track.release()
                    feeder.close()
                }
            }
        }
    }
}
