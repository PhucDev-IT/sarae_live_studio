package co.sarae.camera.audio

import java.nio.ByteBuffer
import org.junit.Assert.*
import org.junit.Test

class PcmBufferWriterTest {
    @Test fun byteArrayOffsetDoesNotOverwriteOtherBytes() {
        val output=ByteArray(8){99};PcmBufferWriter.write(output,2,floatArrayOf(0f,0f),PcmEncoding.S16,4)
        assertArrayEquals(byteArrayOf(99,99,0,0,0,0,99,99),output)
    }
    @Test fun directBufferCursorAndLimitStayUnchanged() {
        val buffer=ByteBuffer.allocateDirect(12);buffer.position(7);buffer.limit(9)
        PcmBufferWriter.write(buffer,0,floatArrayOf(0f,0f),PcmEncoding.S16,4)
        assertEquals(7,buffer.position());assertEquals(9,buffer.limit())
    }
    @Test fun shortArrayOffsetPreserved() {
        val output=shortArrayOf(8,8,8,8)
        PcmBufferWriter.write(output,1,floatArrayOf(0f,1f),PcmEncoding.S16,2)
        assertArrayEquals(shortArrayOf(8,0,32767,8),output)
    }
    @Test fun unsigned8MuteIsMidpoint() {
        val output=ByteArray(2);PcmBufferWriter.write(output,0,FloatArray(2),PcmEncoding.U8,2)
        assertArrayEquals(byteArrayOf(128.toByte(),128.toByte()),output)
    }
    @Test fun floatOutputClipsAndDoesNotWriteOutsideRegion() {
        val output=floatArrayOf(8f,8f,8f,8f)
        PcmBufferWriter.write(output,1,floatArrayOf(2f,Float.NaN),PcmEncoding.FLOAT,2)
        assertArrayEquals(floatArrayOf(8f,1f,0f,8f),output,0f)
    }
    @Test fun invalidContractFailsBeforeMutation() {
        val output=ByteArray(2){55}
        try {PcmBufferWriter.write(output,0,FloatArray(2),PcmEncoding.S16,4);fail()}catch(_:IllegalArgumentException){}
        assertArrayEquals(byteArrayOf(55,55),output)
    }
}
