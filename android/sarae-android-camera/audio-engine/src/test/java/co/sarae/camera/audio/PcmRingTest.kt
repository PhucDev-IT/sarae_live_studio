package co.sarae.camera.audio

import org.junit.Assert.*
import org.junit.Test

class PcmRingTest {
    @Test fun millisecondQuantizedAacPtsDoNotDiscardBufferedSoundtrack() {
        val ring=PcmRing(PcmFormat(48000,1))
        // Reproduces the observed MP4 demux precision: -16/-32 sample jitter.
        for(i in 0 until 18) ring.write((i*1024L/48L)*1000L,FloatArray(1024){(i+1)/20f})
        assertEquals(0L to 18L*1024L,ring.bounds())
        val read=ring.read(0,PcmFormat(48000,1),18*1024)
        assertEquals(0,read.missingFrames)
        for(i in 0 until 18) assertEquals((i+1)/20f,read.samples[i*1024],0f)
    }
    @Test fun actualTimestampGapStillResetsWindow() {
        val ring=PcmRing(PcmFormat(48000,1))
        ring.write(0,FloatArray(1024){.1f})
        ring.write(30_000,FloatArray(1024){.8f}) // >1 ms, not demux rounding.
        assertEquals(1440L to 2464L,ring.bounds())
        assertEquals(480,ring.read(0,PcmFormat(48000,1),480).missingFrames)
    }
    @Test fun contiguousDecoderBlocksRemainAvailableTogether() {
        val ring=PcmRing(PcmFormat(8000,1),8)
        ring.write(0,floatArrayOf(.1f,.2f));ring.write(250,floatArrayOf(.3f,.4f))
        assertEquals(0L to 4L,ring.bounds())
        val read=ring.read(0,PcmFormat(8000,1),4)
        assertEquals(0,read.missingFrames)
        assertArrayEquals(floatArrayOf(.1f,.2f,.3f,.4f),read.samples,0f)
    }
    @Test fun boundedOverflowKeepsNewestFrames() {
        val ring=PcmRing(PcmFormat(8000,1),4)
        ring.write(0,floatArrayOf(0f,1f,2f,3f,4f,5f))
        assertEquals(2L to 6L,ring.bounds());assertEquals(2,ring.overruns)
        val data=ring.read(250000,PcmFormat(8000,1),4)
        assertArrayEquals(floatArrayOf(2f,3f,4f,5f),data.samples,0f)
    }
    @Test fun downsampleAndMonoToStereo() {
        val ring=PcmRing(PcmFormat(48000,1),32)
        ring.write(0,FloatArray(32){it.toFloat()})
        val data=ring.read(0,PcmFormat(16000,2),4)
        assertArrayEquals(floatArrayOf(0f,0f,3f,3f,6f,6f,9f,9f),data.samples,0.0001f)
        assertEquals(0,data.missingFrames)
    }
    @Test fun stereoToMonoAveragesChannels() {
        val ring=PcmRing(PcmFormat(48000,2),8)
        ring.write(0,floatArrayOf(1f,-1f,0.6f,0.2f))
        assertArrayEquals(floatArrayOf(0f,0.4f),ring.read(0,PcmFormat(48000,1),2).samples,0.00001f)
    }
    @Test fun upsamplingInterpolatesAcrossFrames() {
        val ring=PcmRing(PcmFormat(8000,1),8)
        ring.write(0,floatArrayOf(0f,1f,0f))
        assertArrayEquals(floatArrayOf(0f,.5f,1f,.5f),ring.read(0,PcmFormat(16000,1),4).samples,0.00001f)
    }
    @Test fun underrunReturnsSilenceNotOldMicrophone() {
        val ring=PcmRing(PcmFormat(48000,1),8)
        val read=ring.read(0,PcmFormat(48000,2),5)
        assertEquals(5,read.missingFrames);assertArrayEquals(FloatArray(10),read.samples,0f)
    }
    @Test fun nonContiguousSeekDiscardsOldSource() {
        val ring=PcmRing(PcmFormat(8000,1),8)
        ring.write(0,floatArrayOf(1f,1f));ring.write(1_000_000,floatArrayOf(.2f,.3f))
        assertEquals(2,ring.read(0,PcmFormat(8000,1),2).missingFrames)
        assertEquals(8000L to 8002L,ring.bounds())
    }
    @Test fun negativePrerollPtsNeverUsesNegativeArrayOffset() {
        val ring=PcmRing(PcmFormat(8000,1),8)
        ring.write(-125,floatArrayOf(.1f,.2f,.3f))
        assertEquals(.2f,ring.read(0,PcmFormat(8000,1),1).samples[0],0.00001f)
    }
    @Test fun repeatedReadsRetainBoundedHistory() {
        val ring=PcmRing(PcmFormat(48000,1),1000)
        ring.write(0,FloatArray(1000){.5f})
        assertEquals(0,ring.read(0,PcmFormat(48000,1),100).missingFrames)
        assertEquals(0,ring.read(0,PcmFormat(48000,1),100).missingFrames)
    }
    @Test fun mediaClockDoesNotResetOnRepeatedVideoFrame() {
        val clock=VideoAudioClock();clock.update("a",100,1000);clock.update("a",100,2000)
        assertEquals(1100L,clock.mediaTime(2000));assertEquals(1000L,clock.anchor!!.elapsedNs)
    }
    @Test fun loopAndSourceSwitchHaveIndependentEpochs() {
        val clock=VideoAudioClock();clock.update("a",100,1000);clock.update("a",0,2000)
        assertEquals(1,clock.anchor!!.loop);clock.update("b",0,3000);assertEquals(0,clock.anchor!!.loop)
    }
}
