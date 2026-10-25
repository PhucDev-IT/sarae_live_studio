package co.sarae.camera.audio

/** Two fixed-size PCM banks: the audible loop tail and the prepared next-loop head. */
class PcmLoopWindow(val format:PcmFormat) {
    data class Bank(val epoch:Long,val pcm:PcmRing)
    private var active=Bank(0,PcmRing(format))
    private var next:Bank?=null
    @Synchronized fun bank(epoch:Long):PcmRing?=when(epoch) {
        active.epoch->active.pcm
        next?.epoch->next?.pcm
        else->null
    }
    @Synchronized fun prepareNext(epoch:Long):PcmRing {
        require(epoch==active.epoch+1)
        if(next?.epoch!=epoch) next=Bank(epoch,PcmRing(format))
        return requireNotNull(next).pcm
    }
    @Synchronized fun promote(epoch:Long):Boolean {
        if(epoch==active.epoch)return true
        val prepared=next ?: return false
        if(prepared.epoch!=epoch)return false
        active=prepared;next=null;return true
    }
    @Synchronized fun reset(epoch:Long):PcmRing {
        active=Bank(epoch,PcmRing(format));next=null;return active.pcm
    }
}
