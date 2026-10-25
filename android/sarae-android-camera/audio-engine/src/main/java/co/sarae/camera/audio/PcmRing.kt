package co.sarae.camera.audio

import kotlin.math.floor
import kotlin.math.abs

enum class AudioMode { VIDEO, MICROPHONE, MUTED, MIXED }
data class PcmFormat(val rate: Int,val channels: Int) {
    init { require(rate in 8000..192000 && channels in 1..2) }
}
data class PcmRead(val samples: FloatArray,val missingFrames: Int)

/** Fixed memory, media-PTS indexed PCM. One writer; reads never wait for decoded audio. */
class PcmRing(val format: PcmFormat,val capacityFrames: Int= format.rate/2) {
    init { require(capacityFrames>=2 && capacityFrames<=format.rate*2) }
    private val data=FloatArray(capacityFrames*format.channels)
    private var first=0L
    private var end=0L
    var overruns=0L;private set
    var underruns=0L;private set
    @Synchronized fun clear() { first=0;end=0 }
    @Synchronized fun bounds(): Pair<Long,Long> = first to end
    @Synchronized fun freeFrames(): Int=capacityFrames-(end-first).toInt()
    /** Let a running preview advance bounded storage even before AudioRecord starts reading. */
    @Synchronized fun discardBefore(mediaNs: Long) {
        first=minOf(end,maxOf(first,mediaNs*format.rate/1_000_000_000L-format.rate/10))
    }
    @Synchronized fun write(ptsUs: Long,pcm: FloatArray): Int {
        require(pcm.size%format.channels==0)
        val frames=pcm.size/format.channels
        var at=ptsUs*format.rate/1_000_000L
        // Some MP4 packet timelines quantize AAC PTS to millisecond precision.
        // Keep already decoded contiguous PCM instead of discarding the entire
        // window for that bounded jitter. Larger discontinuities still reset it.
        if(end>first && abs(at-end)<=maxOf(2L,format.rate/1000L)) at=end
        if(end==first || at!=end) { first=at;end=at }
        val skip=maxOf(0,frames-capacityFrames)
        val newEnd=at+frames
        val lost=maxOf(0L,newEnd-first-capacityFrames)
        if(lost>0){overruns+=lost;first+=lost}
        for(f in skip until frames) for(c in 0 until format.channels)
            data[(Math.floorMod(at+f,capacityFrames.toLong())*format.channels+c).toInt()]=pcm[f*format.channels+c]
        end=newEnd
        return frames-skip
    }
    @Synchronized fun read(mediaNs: Long,target: PcmFormat,frames: Int): PcmRead {
        require(frames in 0..target.rate) // At most one second per caller request.
        val result=FloatArray(frames*target.channels)
        val begin=mediaNs*format.rate/1e9
        val step=format.rate.toDouble()/target.rate
        var missing=0
        for(i in 0 until frames) {
            val pos=begin+i*step;val a=floor(pos).toLong();val fraction=(pos-a).toFloat()
            val b=if(fraction<0.00001f) a else a+1
            if(a<first || b>=end){missing++;continue}
            fun channel(frame: Long,c: Int):Float=data[(Math.floorMod(frame,capacityFrames.toLong())*format.channels+c).toInt()]
            for(c in 0 until target.channels) {
                fun mapped(f: Long):Float=if(target.channels==1 && format.channels==2) (channel(f,0)+channel(f,1))/2f
                    else channel(f,if(format.channels==1) 0 else c)
                val x=mapped(a);result[i*target.channels+c]=x+(mapped(b)-x)*fraction
            }
        }
        underruns+=missing
        // Keep 100 ms history for overlapping capture reads; memory remains fixed.
        first=minOf(end,maxOf(first,floor(begin).toLong()-format.rate/10))
        return PcmRead(result,missing)
    }
}
