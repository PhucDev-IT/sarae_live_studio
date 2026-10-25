package co.sarae.camera.audio

import android.content.Context
import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import java.nio.ByteOrder
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.abs

/** Audio only. A maximum of two bounded decks follows the existing video renderer clock. */
class MediaAudioEngine(private val context: Context,private val log: (String,Map<String,Any?>)->Unit): AutoCloseable {
    private data class Position(val mediaNs:Long,val epoch:Long)
    private val controlThread=HandlerThread("sarae-audio-control").apply { start() }
    private val control=Handler(controlThread.looper)
    private val decks=ConcurrentHashMap<String,Deck>()
    val clock=VideoAudioClock()
    @Volatile private var session=0L
    private fun key(token: Long,id: Long,uri: String)="$token:$id:$uri"
    fun beginVideo(token: Long) {
        session=token;clock.clear()
        control.post { decks.values.toList().forEach { it.close() };decks.clear() }
    }
    fun prepare(token: Long,id: Long,uri: String) { control.post {
        if(token!=session) return@post
        val k=key(token,id,uri)
        if(!decks.containsKey(k)) decks[k]=Deck(k,Uri.parse(uri))
        val activeKey=clock.anchor?.key
        decks.keys.filter { it!=k && it!=activeKey }.forEach { decks.remove(it)?.close() }
    } }
    /** Hold the request outside the video engine until the candidate's first PCM is available. */
    fun prepareBeforeSwitch(token:Long,id:Long,uri:String,ready:()->Unit) {
        prepare(token,id,uri)
        val deadline=SystemClock.elapsedRealtimeNanos()+750_000_000L
        control.post(object:Runnable {
            override fun run() {
                if(token!=session)return
                val pcm=decks[key(token,id,uri)]?.windows?.bank(0)
                val range=pcm?.bounds()
                val available=pcm!=null && range!=null && range.first<=0 && range.second>=pcm.format.rate/10
                if(!available && SystemClock.elapsedRealtimeNanos()<deadline) {control.postDelayed(this,5);return}
                log(if(available) "audio_switch_prepared" else "audio_switch_prepare_timeout",mapOf("key" to key(token,id,uri),"ready" to available))
                ready()
            }
        })
    }
    fun videoFrame(token: Long,id: Long,uri: String,ptsNs: Long,elapsedNs: Long) {
        if(token!=session) return
        val k=key(token,id,uri);val previous=clock.anchor
        clock.update(k,ptsNs,elapsedNs)
        val anchor=requireNotNull(clock.anchor)
        // Follow video before capture starts; a first AudioRecord read must not trigger catch-up.
        decks[k]?.request(anchor.mediaNs,anchor.loop)
        if(previous?.key!=k) log("audio_source_commit",mapOf("key" to k,"videoMediaNs" to ptsNs,"videoElapsedNs" to elapsedNs))
        else if(previous.loop!=anchor.loop) log("audio_video_loop",mapOf("key" to k,"epoch" to anchor.loop,"videoMediaNs" to ptsNs,"videoElapsedNs" to elapsedNs))
    }
    fun endVideo(token: Long) {
        if(token!=session) return
        clock.clear();control.post { decks.values.toList().forEach { it.close() };decks.clear() }
    }
    data class Result(val pcm: FloatArray,val missingFrames: Int,val driftNs: Long,val key: String?,val mediaNs:Long?=null,val epoch:Long?=null)
    fun read(format: PcmFormat,frames: Int,cursor: AudioCursor,nowNs: Long=SystemClock.elapsedRealtimeNanos()):Result {
        require(frames in 0..format.rate)
        val anchor=clock.anchor ?: return Result(FloatArray(frames*format.channels),frames,0,null)
        val generation="${anchor.key}:${anchor.loop}"
        val blockNs=frames*1_000_000_000L/format.rate
        val desired=maxOf(0L,anchor.mediaNs+maxOf(0L,nowNs-anchor.elapsedNs)-blockNs)
        var drift=cursor.nextMediaNs-desired
        if(cursor.generation!=generation || abs(drift)>40_000_000L) {
            log("audio_clock_resync",mapOf("from" to cursor.nextMediaNs,"to" to desired,"driftNs" to drift,"generation" to generation))
            cursor.generation=generation;cursor.nextMediaNs=desired;drift=0
        }
        val deck=decks[anchor.key]
        val mediaNs=cursor.nextMediaNs
        val result=deck?.windows?.bank(anchor.loop)?.read(mediaNs,format,frames) ?: PcmRead(FloatArray(frames*format.channels),frames)
        cursor.nextMediaNs+=blockNs;cursor.framesRead+=frames
        return Result(result.samples,result.missingFrames,drift,anchor.key,mediaNs,anchor.loop)
    }
    private inner class Deck(val key: String,val uri: Uri):AutoCloseable {
        private val thread=HandlerThread("sarae-audio-decoder").apply { start() }
        private val handler=Handler(thread.looper)
        private var extractor:MediaExtractor?=null
        private var codec:MediaCodec?=null
        private var inputEos=false
        private var outputEos=false
        @Volatile private var closed=false
        @Volatile private var target=Position(0,0)
        private var decodingEpoch=0L
        @Volatile var windows:PcmLoopWindow?=null;private set
        private var readyEpoch=-1L
        private var encoding=AudioFormat.ENCODING_PCM_16BIT
        private var outputRate=0
        private var outputChannels=0
        private val info=MediaCodec.BufferInfo()
        private var producedFrames=0L
        private fun open() { try {
            val media=MediaExtractor();extractor=media
            context.contentResolver.openFileDescriptor(uri,"r").use { fd -> media.setDataSource(requireNotNull(fd).fileDescriptor) }
            val track=(0 until media.trackCount).firstOrNull { media.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/")==true }
                ?: error("No audio track: $uri")
            media.selectTrack(track);val format=media.getTrackFormat(track)
            val decoder=MediaCodec.createDecoderByType(requireNotNull(format.getString(MediaFormat.KEY_MIME)))
            codec=decoder;decoder.configure(format,null,null,0);decoder.start()
            log("audio_decoder_created",mapOf("key" to key,"codec" to decoder.name,"format" to format.toString()))
            handler.post(tick)
        } catch(t:Throwable) { log("audio_decoder_error",mapOf("key" to key,"error" to t.stackTraceToString()));release() } }
        fun request(mediaNs:Long,epoch:Long) {target=Position(mediaNs,epoch)}
        private val tick=object:Runnable {
            override fun run() {
                if(closed) return
                try {
                    val decoder=codec ?: return;val media=extractor ?: return
                    val position=target;val storage=windows
                    if(storage!=null && position.epoch>decodingEpoch) {
                        // Late/missed preparation is explicit. Never serve the previous loop bank.
                        if(!storage.promote(position.epoch)) storage.reset(position.epoch)
                        media.seekTo(position.mediaNs/1000,MediaExtractor.SEEK_TO_PREVIOUS_SYNC);decoder.flush()
                        decodingEpoch=position.epoch;inputEos=false;outputEos=false
                        log("audio_loop_not_ready",mapOf("key" to key,"epoch" to decodingEpoch,"mediaNs" to position.mediaNs))
                    } else if(storage!=null && position.epoch==decodingEpoch) storage.promote(position.epoch)
                    if(outputEos && storage!=null && decodingEpoch==position.epoch) {
                        // Preserve current tail while the same decoder prepares a separate next head.
                        val nextEpoch=decodingEpoch+1
                        storage.prepareNext(nextEpoch)
                        media.seekTo(0,MediaExtractor.SEEK_TO_PREVIOUS_SYNC);decoder.flush()
                        decodingEpoch=nextEpoch;inputEos=false;outputEos=false
                        log("audio_next_loop_prepare",mapOf("key" to key,"epoch" to nextEpoch,"videoMediaNs" to position.mediaNs))
                    }
                    val current=windows?.bank(decodingEpoch)
                    val targetNs=if(decodingEpoch==position.epoch) position.mediaNs else 0L
                    if(current!=null) {
                        current.discardBefore(targetNs)
                        val range=current.bounds();val targetFrame=targetNs*current.format.rate/1_000_000_000L
                        // Decode preroll normally. Seek only for a genuine large forward discontinuity.
                        if(range.second>0 && targetFrame>range.second+current.format.rate/2) {
                            media.seekTo(targetNs/1000,MediaExtractor.SEEK_TO_PREVIOUS_SYNC);decoder.flush();current.clear();inputEos=false;outputEos=false
                            log("audio_seek",mapOf("key" to key,"mediaNs" to targetNs,"epoch" to decodingEpoch))
                        }
                        if(current.freeFrames()<minOf(2048,current.capacityFrames/2) || (current.bounds().second*1_000_000_000L/current.format.rate)>targetNs+250_000_000L) {
                            handler.postDelayed(this,4);return
                        }
                    }
                    if(!inputEos && !outputEos) {
                        val index=decoder.dequeueInputBuffer(0)
                        if(index>=0) {
                            val buffer=requireNotNull(decoder.getInputBuffer(index));buffer.clear()
                            val size=media.readSampleData(buffer,0)
                            if(size<0) { decoder.queueInputBuffer(index,0,0,0,MediaCodec.BUFFER_FLAG_END_OF_STREAM);inputEos=true }
                            else { decoder.queueInputBuffer(index,0,size,media.sampleTime,0);media.advance() }
                        }
                    }
                    if(!outputEos) {
                        val index=decoder.dequeueOutputBuffer(info,0)
                        if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                            val format=decoder.outputFormat
                            outputRate=format.getInteger(MediaFormat.KEY_SAMPLE_RATE);outputChannels=format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                            encoding=if(format.containsKey(MediaFormat.KEY_PCM_ENCODING)) format.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                            require(encoding==AudioFormat.ENCODING_PCM_16BIT || encoding==AudioFormat.ENCODING_PCM_FLOAT) { "Unsupported decoder PCM $encoding" }
                            val pcmFormat=PcmFormat(outputRate,outputChannels)
                            if(windows==null)windows=PcmLoopWindow(pcmFormat)
                            require(windows!!.format==pcmFormat) {"PCM format changed mid-source"}
                            log("audio_output_format",mapOf("key" to key,"rate" to outputRate,"channels" to outputChannels,"encoding" to encoding,"capacityFramesPerBank" to outputRate/2,"maximumBanks" to 2))
                        } else if(index>=0) {
                            try {
                                if(info.size>0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG==0) {
                                    val buffer=requireNotNull(decoder.getOutputBuffer(index)).duplicate().order(ByteOrder.LITTLE_ENDIAN)
                                    buffer.position(info.offset);buffer.limit(info.offset+info.size)
                                    val samples=FloatArray(info.size/if(encoding==AudioFormat.ENCODING_PCM_FLOAT) 4 else 2)
                                    for(i in samples.indices) samples[i]=if(encoding==AudioFormat.ENCODING_PCM_FLOAT) buffer.float.coerceIn(-1f,1f) else buffer.short/32768f
                                    val pcm=requireNotNull(windows?.bank(decodingEpoch));pcm.write(info.presentationTimeUs,samples);producedFrames+=samples.size/outputChannels
                                    val bounds=pcm.bounds()
                                    log("pcm_write",mapOf("key" to key,"epoch" to decodingEpoch,"ptsUs" to info.presentationTimeUs,"frames" to samples.size/outputChannels,"bufferedFrames" to (bounds.second-bounds.first),"overruns" to pcm.overruns))
                                    if(readyEpoch!=decodingEpoch && bounds.first<=targetNs*outputRate/1_000_000_000L && bounds.second>=(targetNs+100_000_000L)*outputRate/1_000_000_000L) {
                                        readyEpoch=decodingEpoch
                                        log("audio_pcm_ready",mapOf("key" to key,"epoch" to decodingEpoch,"targetNs" to targetNs,"firstFrame" to bounds.first,"endFrame" to bounds.second))
                                    }
                                }
                                if(info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0) {outputEos=true;log("audio_eos",mapOf("key" to key))}
                            } finally { decoder.releaseOutputBuffer(index,false) }
                        }
                    }
                    handler.postDelayed(this,if(outputEos) 10 else 2)
                } catch(t:Throwable) {log("audio_decoder_error",mapOf("key" to key,"error" to t.stackTraceToString()));release()}
            }
        }
        init { handler.post { open() } }
        private fun release() {
            closed=true;handler.removeCallbacksAndMessages(null)
            try{codec?.stop()}catch(_:Exception){};try{codec?.release()}catch(_:Exception){};codec=null
            try{extractor?.release()}catch(_:Exception){};extractor=null
            log("audio_decoder_released",mapOf("key" to key,"frames" to producedFrames));thread.quitSafely()
        }
        override fun close() {handler.post {release()} }
    }
    override fun close() {control.post {decks.values.forEach {it.close()};decks.clear();clock.clear();controlThread.quitSafely()} }
}
