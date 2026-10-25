package co.sarae.camera.audio

/** Video source PTS and wall clock are separate. Duplicate output frames do not reset audio. */
class VideoAudioClock {
    data class Anchor(val key: String,val mediaNs: Long,val elapsedNs: Long,val loop: Long)
    @Volatile var anchor: Anchor?=null;private set
    @Synchronized fun update(key: String,mediaNs: Long,elapsedNs: Long) {
        val old=anchor
        if(old?.key==key && old.mediaNs==mediaNs) return
        anchor=Anchor(key,mediaNs,elapsedNs,if(old?.key==key) old.loop+if(mediaNs<old.mediaNs) 1 else 0 else 0)
    }
    @Synchronized fun clear() { anchor=null }
    fun mediaTime(nowNs: Long): Long?=anchor?.let { it.mediaNs+maxOf(0L,nowNs-it.elapsedNs) }
}
class AudioCursor {
    var generation=""
    var nextMediaNs=0L
    var framesRead=0L
}
