package co.sarae.camera.engine

import android.content.Context
import android.media.MediaCodec
import android.media.MediaCodecList
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.os.Trace
import android.view.Surface
import co.sarae.camera.bridge.CameraFrameProvider
import co.sarae.camera.bridge.FrameEvent

/** Video-only MP4 decoder. All codec and extractor calls run on one worker thread. */
class LoopingMp4Provider(private val context: Context, private val preloadFirstFrame: Boolean = false,
                         private val rendererOwnsRotation: Boolean = false) : CameraFrameProvider {
    private val thread = HandlerThread("sarae-mp4-decoder").apply { start() }
    private val handler = Handler(thread.looper)
    private var codec: MediaCodec? = null
    private var extractor: MediaExtractor? = null
    private var descriptor: ParcelFileDescriptor? = null
    private var output: Surface? = null
    private var source: Uri? = null
    private var events: (FrameEvent) -> Unit = {}
    private var inputEos = false
    private var pendingOutput = -1
    private val outputInfo = MediaCodec.BufferInfo()
    private var loopStartNs = 0L
    private var firstPtsUs = -1L
    private var frameIndex = 0L
    private var loopIndex = 0L
    private var fpsWindowNs = 0L
    private var fpsFrames = 0
    private var fps = 0.0
    private var running = false
    private var closed = false
    private var paused = false
    private var lastPtsUs = 0L

    private val tick = object : Runnable {
        override fun run() {
            if (!running) return
            val begin=SystemClock.elapsedRealtimeNanos()
            val beforeFrame=frameIndex
            try {
                Trace.beginSection("Sarae.decodeTick")
                decodeTick()
                Trace.endSection()
                val duration=SystemClock.elapsedRealtimeNanos()-begin
                if(frameIndex != beforeFrame || duration>=5_000_000L)
                    events(FrameEvent("decoder_tick","worker polling",SystemClock.elapsedRealtimeNanos(),source?.toString(),frameIndex,
                        timingsNs=mapOf("workerStartNs" to begin,"workerDurationNs" to duration)))
                if (running && !paused) handler.postDelayed(this, 2)
            } catch (t: Throwable) {
                emit("decoder_error", "${t.javaClass.simpleName}: ${t.message}")
                stopOnWorker()
            }
        }
    }

    override fun start(output: Surface, source: Uri, events: (FrameEvent) -> Unit) {
        handler.post {
            if (closed) return@post
            stopOnWorker()
            this.output = output
            this.events = events
            openOnWorker(source)
        }
    }

    override fun switchSource(source: Uri) {
        handler.post {
            if (!running || output == null) {
                emit("switch_rejected", "provider not running")
                return@post
            }
            emit("switch_requested", "next=$source")
            handler.removeCallbacks(tick)
            releaseMedia()
            openOnWorker(source)
        }
    }

    private fun openOnWorker(uri: Uri) {
        try {
            val surface = requireNotNull(output)
            require(surface.isValid) { "output surface is invalid" }
            source = uri
            descriptor = requireNotNull(context.contentResolver.openFileDescriptor(uri, "r"))
            val retriever=MediaMetadataRetriever()
            try {
                retriever.setDataSource(descriptor!!.fileDescriptor)
                emit("retriever_metadata","width=${retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)} height=${retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)} rotation=${retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)}")
            } finally { retriever.release() }
            emit("source_opened", "scheme=${uri.scheme} fd=${descriptor!!.fd}")
            val mediaExtractor = MediaExtractor().also { it.setDataSource(descriptor!!.fileDescriptor) }
            extractor = mediaExtractor
            emit("extractor_opened", "tracks=${mediaExtractor.trackCount}")
            val track = (0 until mediaExtractor.trackCount).firstOrNull {
                mediaExtractor.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("video/") == true
            } ?: error("MP4 has no video track")
            mediaExtractor.selectTrack(track)
            val format = mediaExtractor.getTrackFormat(track)
            val mime = requireNotNull(format.getString(MediaFormat.KEY_MIME))
            val rotation=if(format.containsKey(MediaFormat.KEY_ROTATION)) format.getInteger(MediaFormat.KEY_ROTATION) else 0
            events(FrameEvent("video_format","rotationAppliedByRenderer=$rendererOwnsRotation format=$format",SystemClock.elapsedRealtimeNanos(),uri.toString(),
                videoWidth=format.getInteger(MediaFormat.KEY_WIDTH),videoHeight=format.getInteger(MediaFormat.KEY_HEIGHT),mediaRotationDegrees=rotation))
            // Own the metadata rotation in one place; SurfaceTexture retains codec crop/flip.
            if(rendererOwnsRotation) format.setInteger(MediaFormat.KEY_ROTATION,0)
            val decoder = openDecoder(mime, format, surface)
            codec = decoder
            inputEos = false
            paused = false
            pendingOutput = -1
            firstPtsUs = -1
            frameIndex = 0
            loopIndex = 0
            fpsFrames = 0
            fps = 0.0
            fpsWindowNs = SystemClock.elapsedRealtimeNanos()
            loopStartNs = fpsWindowNs
            running = true
            emit("decoder_started", "codec=${decoder.name} mime=$mime track=$track format=$format surface=${surface.hashCode()}")
            handler.post(tick)
        } catch (t: Throwable) {
            emit("decoder_error", "open $uri: ${t.stackTraceToString()}")
            stopOnWorker()
        }
    }

    private fun openDecoder(mime: String, format: MediaFormat, surface: Surface): MediaCodec {
        val candidates = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
            .filter { !it.isEncoder && it.supportedTypes.any { type -> type.equals(mime, true) } }
        var lastFailure: Throwable? = null
        for (candidate in candidates) {
            val decoder = try { MediaCodec.createByCodecName(candidate.name) }
            catch (t: Exception) {
                emit("codec_rejected", "codec=${candidate.name} create=${t.javaClass.simpleName}")
                lastFailure = t
                continue
            }
            try {
                decoder.configure(format, surface, null, 0)
                decoder.start()
                emit("codec_started", "codec=${decoder.name} mime=$mime")
                return decoder
            } catch (t: Exception) {
                emit("codec_rejected", "codec=${candidate.name} configure=${t.javaClass.simpleName}: ${t.message}")
                lastFailure = t
                try { decoder.release() } catch (_: Exception) {}
            }
        }
        throw IllegalStateException("No decoder configured for $format", lastFailure)
    }

    private fun decodeTick() {
        val decoder = codec ?: return
        val mediaExtractor = extractor ?: return
        if (!inputEos) {
            val input = decoder.dequeueInputBuffer(0)
            if (input >= 0) {
                val buffer = requireNotNull(decoder.getInputBuffer(input))
                buffer.clear()
                val size = mediaExtractor.readSampleData(buffer, 0)
                if (size < 0) {
                    decoder.queueInputBuffer(input, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                    inputEos = true
                    emit("input_eos", "loop=$loopIndex")
                } else {
                    val pts = mediaExtractor.sampleTime
                    decoder.queueInputBuffer(input, 0, size, pts, 0)
                    mediaExtractor.advance()
                }
            }
        }
        val index = if (pendingOutput >= 0) pendingOutput else decoder.dequeueOutputBuffer(outputInfo, 0)
        when (index) {
            MediaCodec.INFO_TRY_AGAIN_LATER -> return
            MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                emit("output_format", decoder.outputFormat.toString()); return
            }
        }
        if (index < 0) return
        if (outputInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) {
            decoder.releaseOutputBuffer(index, false)
            pendingOutput = -1
            loopIndex++
            emit("loop", "count=$loopIndex frames=$frameIndex")
            mediaExtractor.seekTo(0, MediaExtractor.SEEK_TO_CLOSEST_SYNC)
            decoder.flush()
            inputEos = false
            firstPtsUs = -1
            loopStartNs = SystemClock.elapsedRealtimeNanos()
            return
        }
        if (firstPtsUs < 0) firstPtsUs = outputInfo.presentationTimeUs
        val targetNs = loopStartNs + (outputInfo.presentationTimeUs - firstPtsUs) * 1_000L
        val nowNs = SystemClock.elapsedRealtimeNanos()
        if (targetNs > nowNs + 2_000_000L) {
            pendingOutput = index
            return
        }
        pendingOutput = -1
        decoder.releaseOutputBuffer(index, true)
        frameIndex++
        fpsFrames++
        val elapsed = nowNs - fpsWindowNs
        if (elapsed >= 1_000_000_000L) {
            fps = fpsFrames * 1_000_000_000.0 / elapsed
            fpsFrames = 0
            fpsWindowNs = nowNs
            emit("fps", "fps=%.2f frames=$frameIndex loop=$loopIndex".format(fps))
        }
        emit("frame_rendered", "loop=$loopIndex lateMs=${(nowNs-targetNs)/1_000_000}", frameIndex, outputInfo.presentationTimeUs)
        lastPtsUs = outputInfo.presentationTimeUs
        if (preloadFirstFrame && frameIndex == 1L) paused = true
    }

    fun resumePlayback() { handler.post {
        if (paused && running) {
            paused = false
            loopStartNs = SystemClock.elapsedRealtimeNanos() - (lastPtsUs - firstPtsUs) * 1_000L
            handler.post(tick)
        }
    } }

    private fun emit(kind: String, detail: String, frame: Long? = null, pts: Long? = null) {
        events(FrameEvent(kind, detail, SystemClock.elapsedRealtimeNanos(), source?.toString(), frame, pts, fps))
    }

    private fun releaseMedia() {
        if (pendingOutput >= 0) {
            try { codec?.releaseOutputBuffer(pendingOutput, false) } catch (_: Exception) {}
            pendingOutput = -1
        }
        try { codec?.stop() } catch (_: Exception) {}
        try { codec?.release() } catch (_: Exception) {}
        codec = null
        emit("decoder_released", "source=$source")
        try { extractor?.release() } catch (_: Exception) {}
        extractor = null
        try { descriptor?.close() } catch (_: Exception) {}
        descriptor = null
    }

    private fun stopOnWorker() {
        handler.removeCallbacks(tick)
        if (running || codec != null) emit("decoder_stop", "frames=$frameIndex loops=$loopIndex")
        running = false
        releaseMedia()
        output = null
    }

    override fun stop() { handler.post { stopOnWorker() } }

    override fun close() = closeAsync {}

    fun closeAsync(onClosed: () -> Unit) {
        handler.post {
            stopOnWorker()
            closed = true
            thread.quitSafely()
            onClosed()
        }
    }
}
