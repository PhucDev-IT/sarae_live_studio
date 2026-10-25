package co.sarae.camera.observation

/** Loaded only by the owned-harness module. No target-native integration. */
object NativeHookValidation {
    external fun control(command:Int):LongArray
    external fun poll():LongArray
    external fun path20(kind:Int):String
    external fun resolve20(kind:Int,rva:Long,buildId:String,fingerprint:ByteArray):LongArray
    external fun reject20(code:Int):LongArray
    external fun poll20():LongArray
    external fun code20():ByteArray
    external fun pinError20():String
    external fun ownerPin20(handle:Long)
}
