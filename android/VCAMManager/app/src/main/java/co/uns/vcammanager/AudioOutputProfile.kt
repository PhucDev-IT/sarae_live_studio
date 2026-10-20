package co.uns.vcammanager

data class AudioOutputProfile(val sampleRate: Int, val channels: Int) {
    val label: String
        get() = "${sampleRate / 1000.0} kHz · ${if (channels == 1) "mono" else "stereo"}"
}
