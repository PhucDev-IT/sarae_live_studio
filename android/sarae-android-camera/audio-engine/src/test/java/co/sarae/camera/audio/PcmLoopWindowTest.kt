package co.sarae.camera.audio

import org.junit.Assert.*
import org.junit.Test

class PcmLoopWindowTest {
    @Test fun preparingHeadKeepsCurrentTailReadable() {
        val windows=PcmLoopWindow(PcmFormat(8000,1))
        windows.bank(0)!!.write(1_000_000,floatArrayOf(.7f,.8f))
        windows.prepareNext(1).write(0,floatArrayOf(.1f,.2f))
        assertArrayEquals(floatArrayOf(.7f,.8f),windows.bank(0)!!.read(1_000_000_000,PcmFormat(8000,1),2).samples,0f)
        assertArrayEquals(floatArrayOf(.1f,.2f),windows.bank(1)!!.read(0,PcmFormat(8000,1),2).samples,0f)
    }
    @Test fun loopPromotionReusesPreparedHeadWithoutClearingIt() {
        val windows=PcmLoopWindow(PcmFormat(8000,1));val prepared=windows.prepareNext(1)
        prepared.write(0,floatArrayOf(.2f,.3f));assertTrue(windows.promote(1));assertSame(prepared,windows.bank(1));assertNull(windows.bank(0))
        windows.prepareNext(2).write(0,floatArrayOf(.4f));assertTrue(windows.promote(2));assertNull(windows.bank(1))
        assertEquals(.4f,windows.bank(2)!!.read(0,PcmFormat(8000,1),1).samples[0],0f)
    }
    @Test fun previewProgressFreesRingWithoutCaptureReads() {
        val ring=PcmRing(PcmFormat(8000,1));ring.write(0,FloatArray(3200){.1f})
        ring.discardBefore(400_000_000);assertEquals(2400L to 3200L,ring.bounds())
        ring.write(400_000,FloatArray(2000){.2f});assertEquals(0,ring.overruns)
        assertEquals(0,ring.read(400_000_000,PcmFormat(8000,1),2000).missingFrames)
    }
    @Test fun absentLoopCannotReadPreviousSourceTail() {
        val windows=PcmLoopWindow(PcmFormat(8000,1));windows.bank(0)!!.write(0,floatArrayOf(.9f))
        assertNull(windows.bank(1));assertFalse(windows.promote(1))
        windows.reset(3);assertNull(windows.bank(0));assertEquals(1,windows.bank(3)!!.read(0,PcmFormat(8000,1),1).missingFrames)
    }
}
