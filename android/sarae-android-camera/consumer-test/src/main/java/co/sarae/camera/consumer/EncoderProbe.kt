package co.sarae.camera.consumer

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import java.io.File

/** Diagnostic sink only: encodes the existing bridge's Surface output, no decoder or renderer. */
internal class EncoderProbe(file:File, private val report:(String,String)->Unit) {
    private val codec=MediaCodec.createEncoderByType("video/avc")
    private val muxer=MediaMuxer(file.absolutePath,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
    @Volatile private var stopping=false
    private var muxerStarted=false
    val surface:android.view.Surface
    private val worker:Thread
    init {
        codec.configure(MediaFormat.createVideoFormat("video/avc",640,480).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE,1_500_000)
            setInteger(MediaFormat.KEY_FRAME_RATE,30)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL,1)
        },null,null,MediaCodec.CONFIGURE_FLAG_ENCODE)
        surface=codec.createInputSurface();codec.start()
        report("encoder_probe_started","codec=${codec.name} file=${file.name} size=640x480")
        worker=Thread({drain()},"sarae-encoder-probe").apply {start()}
    }
    private fun drain() {
        var track=-1;var frames=0;var previous=-1L;var regressions=0;var maxGap=0L
        val info=MediaCodec.BufferInfo()
        try {
            while(true) {
                val index=codec.dequeueOutputBuffer(info,20_000)
                if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                    track=muxer.addTrack(codec.outputFormat);muxer.start();muxerStarted=true
                } else if(index>=0) {
                    if(info.size>0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG==0) {
                        val data=requireNotNull(codec.getOutputBuffer(index))
                        data.position(info.offset);data.limit(info.offset+info.size)
                        muxer.writeSampleData(track,data,info)
                        if(previous>=0) {if(info.presentationTimeUs<=previous)regressions++;maxGap=maxOf(maxGap,info.presentationTimeUs-previous)}
                        previous=info.presentationTimeUs;frames++
                        if(frames==1 || frames%30==0)report("encoded_packet","frames=$frames ptsUs=$previous emittedNs=${android.os.SystemClock.elapsedRealtimeNanos()}")
                    }
                    val eos=info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM!=0
                    codec.releaseOutputBuffer(index,false)
                    if(eos)break
                }
                if(stopping && index==MediaCodec.INFO_TRY_AGAIN_LATER)break
            }
        } catch(t:Throwable) {report("encoder_probe_error",t.toString())}
        finally {
            report("encoder_probe_summary","frames=$frames ptsRegressions=$regressions maxPacketGapUs=$maxGap")
            codec.stop();codec.release()
            if(muxerStarted)muxer.stop()
            muxer.release()
        }
    }
    fun close() {
        if(stopping)return
        stopping=true
        codec.signalEndOfInputStream()
        worker.join(1500)
        report("encoder_probe_closed","workerStopped=${!worker.isAlive}")
    }
}
