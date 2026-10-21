package io.twinkle.unreal

import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.os.SystemClock
import java.io.Closeable
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.ArrayDeque
import kotlin.math.max
import kotlin.math.roundToInt

/** Supplies decoded playlist audio as PCM to TikTok's AudioRecord.read(ByteBuffer, ...). */
class VirtualAudioFeeder(
    private val files: List<String>,
    val targetRate: Int,
    val targetChannels: Int,
    startIndex: Int,
    startPositionMs: Long,
    private val paceReads: Boolean = true
) : Closeable {
    private val pcm = ArrayDeque<Short>()
    private var currentIndex = startIndex.coerceIn(0, (files.size - 1).coerceAtLeast(0))
    private var extractor: MediaExtractor? = null
    private var decoder: MediaCodec? = null
    private var inputEnded = false
    private var outputEnded = false
    @Volatile var sourceRate = 48000
        private set
    @Volatile var sourceChannels = 2
        private set
    @Volatile var sourceEncoding = AudioFormat.ENCODING_PCM_16BIT
        private set
    private var nextReadNs = SystemClock.elapsedRealtimeNanos()
    @Volatile private var closed = false

    init {
        require(files.isNotEmpty()) { "No playlist files" }
        require(targetRate > 0 && targetChannels > 0) { "Invalid AudioRecord format" }
        check(openAudioAt(currentIndex, startPositionMs)) { "No playlist clip contains a decodable audio track" }
    }

    @Synchronized
    fun read(buffer: ByteBuffer, requestedBytes: Int): Int {
        if (closed || requestedBytes <= 0) return 0
        val byteCount = requestedBytes.coerceAtMost(buffer.remaining()).coerceAtMost(1 shl 20)
        val evenBytes = byteCount and -2
        if (evenBytes <= 0) return 0
        val savedPosition = buffer.position()
        var written = 0
        while (written < evenBytes && !closed) {
            if (pcm.isEmpty() && !decodeMore()) break
            while (written < evenBytes && pcm.isNotEmpty()) {
                val sample = pcm.removeFirst().toInt()
                buffer.put((sample and 0xff).toByte())
                buffer.put(((sample ushr 8) and 0xff).toByte())
                written += 2
            }
        }
        buffer.position(savedPosition)
        if (written == 0) return 0

        // AudioRecord.read() normally blocks for roughly the duration of the requested PCM.
        // Keep the replacement stream in real time instead of draining the decoder immediately.
        if (paceReads) {
            val durationNs = written.toLong() * 1_000_000_000L / (targetRate.toLong() * targetChannels * 2L)
            val now = SystemClock.elapsedRealtimeNanos()
            nextReadNs = max(nextReadNs, now) + durationNs
            val waitMs = ((nextReadNs - SystemClock.elapsedRealtimeNanos()) / 1_000_000L).coerceAtLeast(0)
            if (waitMs > 0) SystemClock.sleep(waitMs)
        }
        return written
    }

    private fun decodeMore(): Boolean {
        repeat(80) {
            if (closed) return false
            val codec = decoder ?: return false
            if (!inputEnded) {
                val inputIndex = codec.dequeueInputBuffer(10_000)
                if (inputIndex >= 0) {
                    val input = codec.getInputBuffer(inputIndex) ?: return@repeat
                    val size = extractor?.readSampleData(input, 0) ?: -1
                    if (size < 0) {
                        codec.queueInputBuffer(inputIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputEnded = true
                    } else {
                        codec.queueInputBuffer(inputIndex, 0, size, extractor?.sampleTime ?: 0, 0)
                        extractor?.advance()
                    }
                }
            }

            val info = MediaCodec.BufferInfo()
            val outputIndex = codec.dequeueOutputBuffer(info, 10_000)
            when {
                outputIndex >= 0 -> {
                    if (info.size > 0) {
                        val output = codec.getOutputBuffer(outputIndex)
                        if (output != null) {
                            output.position(info.offset)
                            output.limit(info.offset + info.size)
                            appendPcm(output.slice().order(ByteOrder.LITTLE_ENDIAN))
                        }
                    }
                    outputEnded = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    codec.releaseOutputBuffer(outputIndex, false)
                    if (pcm.isNotEmpty()) return true
                    if (outputEnded) {
                        if (!advanceSource()) return false
                        return true
                    }
                }
                outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    val format = codec.outputFormat
                    sourceRate = format.intOr(MediaFormat.KEY_SAMPLE_RATE, sourceRate)
                    sourceChannels = format.intOr(MediaFormat.KEY_CHANNEL_COUNT, sourceChannels)
                    sourceEncoding = format.intOr(MediaFormat.KEY_PCM_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
                }
            }
        }
        return pcm.isNotEmpty()
    }

    private fun appendPcm(data: ByteBuffer) {
        val bytesPerSample = if (sourceEncoding == AudioFormat.ENCODING_PCM_FLOAT) 4 else 2
        val frameBytes = sourceChannels * bytesPerSample
        val frames = data.remaining() / frameBytes
        if (frames <= 0) return
        val outFrames = max(1, (frames.toDouble() * targetRate / sourceRate).roundToInt())
        fun sourceSample(frame: Int, channel: Int): Float {
            val sourceChannel = when {
                sourceChannels == 1 -> 0
                targetChannels == 1 -> -1
                else -> channel.coerceAtMost(sourceChannels - 1)
            }
            fun one(ch: Int): Float {
                val p = (frame * sourceChannels + ch) * bytesPerSample
                return if (sourceEncoding == AudioFormat.ENCODING_PCM_FLOAT) {
                    data.getFloat(p).coerceIn(-1f, 1f)
                } else {
                    data.getShort(p).toFloat() / 32768f
                }
            }
            return if (sourceChannel >= 0) one(sourceChannel) else {
                var total = 0f
                for (ch in 0 until sourceChannels) total += one(ch)
                total / sourceChannels
            }
        }
        for (outFrame in 0 until outFrames) {
            val src = outFrame.toDouble() * sourceRate / targetRate
            val left = src.toInt().coerceIn(0, frames - 1)
            val right = (left + 1).coerceAtMost(frames - 1)
            val fraction = (src - left).toFloat().coerceIn(0f, 1f)
            for (channel in 0 until targetChannels) {
                val a = sourceSample(left, channel)
                val b = sourceSample(right, channel)
                val value = ((a + (b - a) * fraction) * 32767f).roundToInt().coerceIn(-32768, 32767)
                pcm.addLast(value.toShort())
            }
        }
    }

    private fun advanceSource(): Boolean {
        val next = (currentIndex + 1) % files.size
        closeCurrent()
        return openAudioAt(next, 0L)
    }

    private fun openAudioAt(requestedIndex: Int, seekMs: Long): Boolean {
        var index = requestedIndex
        repeat(files.size) {
            try {
                val newExtractor = MediaExtractor()
                newExtractor.setDataSource(files[index])
                var audioTrack = -1
                var audioFormat: MediaFormat? = null
                for (track in 0 until newExtractor.trackCount) {
                    val format = newExtractor.getTrackFormat(track)
                    if (format.getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true) {
                        audioTrack = track
                        audioFormat = format
                        break
                    }
                }
                if (audioTrack >= 0 && audioFormat != null) {
                    newExtractor.selectTrack(audioTrack)
                    if (seekMs > 0) newExtractor.seekTo(seekMs * 1000L, MediaExtractor.SEEK_TO_CLOSEST_SYNC)
                    val mime = audioFormat.getString(MediaFormat.KEY_MIME) ?: error("Audio MIME missing")
                    val newDecoder = MediaCodec.createDecoderByType(mime)
                    newDecoder.configure(audioFormat, null, null, 0)
                    newDecoder.start()
                    extractor = newExtractor
                    decoder = newDecoder
                    currentIndex = index
                    sourceRate = audioFormat.intOr(MediaFormat.KEY_SAMPLE_RATE, 48000)
                    sourceChannels = audioFormat.intOr(MediaFormat.KEY_CHANNEL_COUNT, 2)
                    sourceEncoding = AudioFormat.ENCODING_PCM_16BIT
                    inputEnded = false
                    outputEnded = false
                    return true
                }
                newExtractor.release()
            } catch (_: Throwable) {
                closeCurrent()
            }
            index = (index + 1) % files.size
        }
        return false
    }

    private fun closeCurrent() {
        try { decoder?.stop() } catch (_: Throwable) {}
        try { decoder?.release() } catch (_: Throwable) {}
        try { extractor?.release() } catch (_: Throwable) {}
        decoder = null
        extractor = null
        inputEnded = false
        outputEnded = false
    }

    override fun close() {
        synchronized(this) {
            closed = true
            pcm.clear()
            closeCurrent()
        }
    }

    private fun MediaFormat.intOr(key: String, fallback: Int): Int =
        if (containsKey(key)) getInteger(key) else fallback
}

