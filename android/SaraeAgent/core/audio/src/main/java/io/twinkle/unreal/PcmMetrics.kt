package io.twinkle.unreal

import java.io.Closeable
import java.io.File
import java.io.RandomAccessFile
import kotlin.math.sqrt

/** Shared PCM diagnostics and WAV output used by both the hook module and offline harness. */
class PcmMetrics(private val sampleRate: Int, private val channels: Int) {
    private var sumSquares = 0.0
    private var sampleCount = 0L
    private var absolutePeak = 0

    @Synchronized
    fun add(pcm: ByteArray, byteCount: Int) {
        val count = byteCount.coerceAtMost(pcm.size) and -2
        var i = 0
        while (i < count) {
            val value = ((pcm[i].toInt() and 0xff) or (pcm[i + 1].toInt() shl 8)).toShort().toInt()
            val magnitude = kotlin.math.abs(value)
            if (magnitude > absolutePeak) absolutePeak = magnitude
            sumSquares += value.toDouble() * value
            sampleCount++
            i += 2
        }
    }

    @Synchronized fun rms(): Double = if (sampleCount == 0L) 0.0 else sqrt(sumSquares / sampleCount) / 32768.0
    @Synchronized fun peak(): Int = absolutePeak
    fun durationMs(byteCount: Long): Long = byteCount * 1000L / (sampleRate.toLong() * channels * 2L)
}

class PcmWavWriter(file: File, private val sampleRate: Int, private val channels: Int) : Closeable {
    private val output = RandomAccessFile(file, "rw")
    private var bytesWritten = 0L

    init { require(sampleRate > 0 && channels in 1..2); output.setLength(0); writeHeader(0) }

    @Synchronized
    fun write(pcm: ByteArray, byteCount: Int) {
        val aligned = byteCount.coerceAtMost(pcm.size) / (channels * 2) * (channels * 2)
        if (aligned <= 0) return
        output.seek(44 + bytesWritten)
        output.write(pcm, 0, aligned)
        bytesWritten += aligned
    }

    @Synchronized
    override fun close() {
        output.seek(0)
        writeHeader(bytesWritten)
        output.fd.sync()
        output.close()
    }

    private fun writeHeader(dataBytes: Long) {
        val byteRate = sampleRate * channels * 2
        output.writeBytes("RIFF")
        writeInt(36L + dataBytes)
        output.writeBytes("WAVEfmt ")
        writeInt(16)
        writeShort(1)
        writeShort(channels)
        writeInt(sampleRate.toLong())
        writeInt(byteRate.toLong())
        writeShort(channels * 2)
        writeShort(16)
        output.writeBytes("data")
        writeInt(dataBytes)
    }

    private fun writeShort(value: Int) { output.write(value and 0xff); output.write((value ushr 8) and 0xff) }
    private fun writeInt(value: Long) { repeat(4) { shift -> output.write(((value ushr (shift * 8)) and 0xff).toInt()) } }
}
