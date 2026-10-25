package co.sarae.camera.audio

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.roundToInt

enum class PcmEncoding(val bytes: Int) { U8(1),S16(2),FLOAT(4) }
object PcmBufferWriter {
    /** Validate first; mutate only the successful AudioRecord read region. */
    fun write(target: Any,offset: Int,pcm: FloatArray,encoding:PcmEncoding,returnedCount:Int) {
        val byteTarget=target is ByteArray || target is ByteBuffer
        require(returnedCount==pcm.size*(if(byteTarget) encoding.bytes else 1))
        when(target) {
            is ByteBuffer->require(target.isDirect && returnedCount<=target.capacity() && offset==0)
            is ByteArray->require(offset in 0..target.size-returnedCount)
            is ShortArray->require(encoding==PcmEncoding.S16 && offset in 0..target.size-returnedCount)
            is FloatArray->require(encoding==PcmEncoding.FLOAT && offset in 0..target.size-returnedCount)
            else->error("Unsupported PCM target")
        }
        fun sample(i:Int)=if(pcm[i].isFinite()) pcm[i].coerceIn(-1f,1f) else 0f
        fun s16(i:Int)=(sample(i)*32767).roundToInt().toShort()
        val bytes=when(target){is ByteBuffer->target.duplicate().apply {clear()};is ByteArray->ByteBuffer.wrap(target);else->null}?.order(ByteOrder.nativeOrder())
        for(i in pcm.indices) when(target) {
            is ShortArray->target[offset+i]=s16(i)
            is FloatArray->target[offset+i]=sample(i)
            else->when(encoding) {
                PcmEncoding.U8->bytes!!.put(offset+i,(sample(i)*127+128).roundToInt().coerceIn(0,255).toByte())
                PcmEncoding.S16->bytes!!.putShort(offset+i*2,s16(i))
                PcmEncoding.FLOAT->bytes!!.putFloat(offset+i*4,sample(i))
            }
        }
    }
}
