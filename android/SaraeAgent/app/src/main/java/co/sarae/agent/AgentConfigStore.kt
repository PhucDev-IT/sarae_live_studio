package co.sarae.agent

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class AgentConfig(val apiUrl: String, val agentId: String, val credential: String, val hostname: String)

class AgentConfigStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("agent-v1", Context.MODE_PRIVATE)
    private val alias = "sarae.android.agent.v1"

    fun load(): AgentConfig? = runCatching {
        val encrypted = prefs.getString("credential", null) ?: return null
        val bytes = Base64.decode(encrypted, Base64.NO_WRAP)
        val iv = bytes.copyOfRange(0, 12)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        val credential = cipher.doFinal(bytes.copyOfRange(12, bytes.size)).decodeToString()
        AgentConfig(prefs.getString("api_url", "") ?: "", prefs.getString("agent_id", "") ?: "",
            credential, prefs.getString("hostname", "Android") ?: "Android")
    }.getOrNull()

    fun save(config: AgentConfig) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.iv + cipher.doFinal(config.credential.toByteArray())
        prefs.edit().putString("api_url", config.apiUrl.trimEnd('/'))
            .putString("agent_id", config.agentId).putString("hostname", config.hostname)
            .putString("credential", Base64.encodeToString(encrypted, Base64.NO_WRAP)).apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
        runCatching { KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry(alias) } }
    }

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(KeyGenParameterSpec.Builder(alias,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
            .build())
        return generator.generateKey()
    }
}
