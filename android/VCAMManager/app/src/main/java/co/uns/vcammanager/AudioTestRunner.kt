package co.uns.vcammanager

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.media.AudioTimestamp
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import io.twinkle.unreal.PcmMetrics
import io.twinkle.unreal.PcmWavWriter
import io.twinkle.unreal.VirtualAudioFeeder
import java.io.File
import java.nio.ByteBuffer
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/** Offline runner: decoder/format conversion is exactly the feeder used by the VCAM hook. */
class AudioTestRunner(private val context: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val running = AtomicBoolean(false)
    @Volatile private var worker: Thread? = null
    @Volatile private var generation = 0L
    @Volatile private var feeder: VirtualAudioFeeder? = null
    @Volatile private var track: AudioTrack? = null
    @Volatile private var wav: PcmWavWriter? = null
    @Volatile var lastWavPath: String? = null
        private set
    private val log = StringBuilder()

    @Synchronized
    fun start(uri: Uri, targetRate: Int = 44100, targetChannels: Int = 2, muted: Boolean = false, onUpdate: (String, String, String?) -> Unit) {
        require(targetRate in setOf(44100, 48000) && targetChannels in 1..2)
        stopCurrent(invalidate = true)
        val runId = ++generation
        running.set(true)
        log.appendLine("START ${System.currentTimeMillis()} source=$uri rate=$targetRate channels=$targetChannels pcm_s16le")
        worker = Thread {
            var localFeeder: VirtualAudioFeeder? = null
            var localTrack: AudioTrack? = null
            var localWav: PcmWavWriter? = null
            try {
                val source = File(context.cacheDir, "vcam-audio-source.mp4")
                context.contentResolver.openInputStream(uri)?.use { input -> source.outputStream().use(input::copyTo) }
                    ?: error("Không mở được video đã chọn")
                val sourceFormat = MediaExtractor().run {
                    try {
                        setDataSource(source.absolutePath)
                        (0 until trackCount).firstNotNullOfOrNull { index ->
                            val format = getTrackFormat(index)
                            if (format.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true) {
                                "${format.getString(MediaFormat.KEY_MIME)} ${format.intOrNull(MediaFormat.KEY_SAMPLE_RATE)}Hz/${format.intOrNull(MediaFormat.KEY_CHANNEL_COUNT)}ch"
                            } else null
                        } ?: error("MP4 không có audio track")
                    } finally { release() }
                }
                val outputDir = File(context.getExternalFilesDir(null) ?: context.filesDir, "audio-tests").apply { mkdirs() }
                val out = File(outputDir, "vcam-audio-${System.currentTimeMillis()}.wav")
                localFeeder = VirtualAudioFeeder(listOf(source.absolutePath), targetRate, targetChannels, 0, 0, paceReads = false)
                feeder = localFeeder
                localWav = PcmWavWriter(out, targetRate, targetChannels)
                wav = localWav
                val channelMask = if (targetChannels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
                val min = AudioTrack.getMinBufferSize(targetRate, channelMask, AudioFormat.ENCODING_PCM_16BIT)
                check(min > 0) { "AudioTrack buffer configuration failed: $min" }
                localTrack = AudioTrack.Builder()
                    .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build())
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(targetRate).setChannelMask(channelMask).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                    .setBufferSizeInBytes(min * 2)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
                track = localTrack
                localTrack.setVolume(if (muted) 0f else 1f)
                val metrics = PcmMetrics(targetRate, targetChannels)
                val buffer = ByteArray(8192)
                val frame = ByteBuffer.wrap(buffer)
                var blocks = 0L
                var feederReads = 0L
                var bytes = 0L
                var drops = 0L
                var latencyTotalNs = 0L
                var latencyMaxNs = 0L
                var writeLatencyTotalNs = 0L
                var writeLatencyMaxNs = 0L
                var trackStarted = false
                var previousAudioFramePosition: Long? = null
                var previousAudioTimestampNs: Long? = null
                var lastUi = 0L
                log.appendLine("SOURCE $sourceFormat; OUTPUT decoder PCM_${localFeeder.sourceEncoding} ${localFeeder.sourceRate}Hz/${localFeeder.sourceChannels}ch -> ${targetRate}Hz/${targetChannels}ch/PCM_S16LE AudioTrackBuffer=$min muted=$muted")
                while (running.get() && generation == runId) {
                    frame.clear()
                    val begin = SystemClock.elapsedRealtimeNanos()
                    val count = localFeeder.read(frame, buffer.size)
                    val latencyNs = SystemClock.elapsedRealtimeNanos() - begin
                    feederReads++
                    latencyTotalNs += latencyNs
                    latencyMaxNs = maxOf(latencyMaxNs, latencyNs)
                    if (count <= 0) { drops++; continue }
                    localWav.write(buffer, count)
                    metrics.add(buffer, count)
                    blocks++
                    bytes += count
                    val writeBeginNs = SystemClock.elapsedRealtimeNanos()
                    val written = localTrack.write(buffer, 0, count, AudioTrack.WRITE_BLOCKING)
                    val writeLatencyNs = SystemClock.elapsedRealtimeNanos() - writeBeginNs
                    writeLatencyTotalNs += writeLatencyNs
                    writeLatencyMaxNs = maxOf(writeLatencyMaxNs, writeLatencyNs)
                    if (written < count && running.get()) drops++
                    if (!trackStarted) {
                        localTrack.play()
                        trackStarted = true
                    }
                    val now = SystemClock.elapsedRealtime()
                    if (now - lastUi >= 1000) {
                        lastUi = now
                        val audioTimestamp = AudioTimestamp()
                        val timestampDrift = if (localTrack.getTimestamp(audioTimestamp)) {
                            val oldFrame = previousAudioFramePosition
                            val oldTime = previousAudioTimestampNs
                            previousAudioFramePosition = audioTimestamp.framePosition
                            previousAudioTimestampNs = audioTimestamp.nanoTime
                            if (oldFrame != null && oldTime != null) {
                                val frameDeltaMs = (audioTimestamp.framePosition - oldFrame) * 1000.0 / targetRate
                                val clockDeltaMs = (audioTimestamp.nanoTime - oldTime) / 1_000_000.0
                                "${"%.1f".format(Locale.US, frameDeltaMs - clockDeltaMs)}ms"
                            } else "baseline"
                        } else "unavailable"
                        val snapshot = "reads=$feederReads blocks=$blocks bytes=$bytes RMS=${"%.4f".format(Locale.US, metrics.rms())} peak=${metrics.peak()} decodeAvg=${"%.2f".format(Locale.US, latencyTotalNs / feederReads / 1_000_000.0)}ms decodeMax=${"%.2f".format(Locale.US, latencyMaxNs / 1_000_000.0)}ms writeAvg=${"%.2f".format(Locale.US, writeLatencyTotalNs / maxOf(blocks, 1) / 1_000_000.0)}ms writeMax=${"%.2f".format(Locale.US, writeLatencyMaxNs / 1_000_000.0)}ms dropped=$drops underruns=${localTrack.underrunCount} audioTimestampDrift=$timestampDrift"
                        log.appendLine("${System.currentTimeMillis()} $snapshot")
                        main.post { onUpdate("Đang phát — ${out.name}", snapshot, out.absolutePath) }
                    }
                }
                log.appendLine("STOP blocks=$blocks bytes=$bytes drops=$drops underruns=${localTrack.underrunCount} wav=${out.absolutePath}")
                if (generation == runId) lastWavPath = out.absolutePath
                if (generation == runId) main.post { onUpdate("Đã dừng. WAV đã ghi: ${out.name}", "blocks=$blocks bytes=$bytes RMS=${"%.4f".format(metrics.rms())} peak=${metrics.peak()} dropped=$drops", out.absolutePath) }
            } catch (t: Throwable) {
                log.appendLine("ERROR ${t.javaClass.name}: ${t.message}")
                if (generation == runId) main.post { onUpdate("Audio test lỗi: ${t.message}", log.toString().takeLast(2000), lastWavPath) }
            } finally {
                if (generation == runId) running.set(false)
                runCatching { localTrack?.pause() }
                runCatching { localTrack?.flush() }
                runCatching { localTrack?.release() }
                runCatching { localWav?.close() }
                runCatching { localFeeder?.close() }
                if (track === localTrack) track = null
                if (wav === localWav) wav = null
                if (feeder === localFeeder) feeder = null
            }
        }.apply { name = "VCAM-audio-test"; start() }
    }

    @Synchronized
    fun stop() {
        stopCurrent(invalidate = false)
    }

    private fun stopCurrent(invalidate: Boolean) {
        if (invalidate) generation++
        running.set(false)
        runCatching { track?.pause() }
        runCatching { track?.flush() }
        runCatching { feeder?.close() }
    }

    @Synchronized
    fun exportLog(): String = log.toString()
}

private fun MediaFormat.intOrNull(key: String): Int? = if (containsKey(key)) getInteger(key) else null
