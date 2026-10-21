package co.sarae.agent

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.SharedPreferencesMigration
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private val Context.agentConfigDataStore by preferencesDataStore(
    name = "agent-v1",
    produceMigrations = { context -> listOf(SharedPreferencesMigration(context, "agent-v1")) }
)

data class AgentConfig(val apiUrl: String, val agentId: String, val credential: String, val hostname: String)

class AgentConfigStore(context: Context) {
    private val dataStore = context.applicationContext.agentConfigDataStore
    private val alias = "sarae.android.agent.v1"
    private val apiUrlKey = stringPreferencesKey("api_url")
    private val agentIdKey = stringPreferencesKey("agent_id")
    private val hostnameKey = stringPreferencesKey("hostname")
    private val credentialKey = stringPreferencesKey("credential")

    val config: Flow<AgentConfig?> = dataStore.data
        .catch { emit(emptyPreferences()) }
        .map { prefs -> decrypt(prefs[credentialKey], prefs[apiUrlKey], prefs[agentIdKey], prefs[hostnameKey]) }

    suspend fun load(): AgentConfig? {
        val prefs = dataStore.data.first()
        return decrypt(prefs[credentialKey], prefs[apiUrlKey], prefs[agentIdKey], prefs[hostnameKey])
    }

    private fun decrypt(encryptedValue: String?, apiUrl: String?, agentId: String?, hostname: String?): AgentConfig? = runCatching {
        val encrypted = encryptedValue ?: return null
        val bytes = Base64.decode(encrypted, Base64.NO_WRAP)
        val iv = bytes.copyOfRange(0, 12)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        val credential = cipher.doFinal(bytes.copyOfRange(12, bytes.size)).decodeToString()
        AgentConfig(apiUrl.orEmpty(), agentId.orEmpty(), credential, hostname ?: "Android")
    }.getOrNull()

    suspend fun save(config: AgentConfig) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.iv + cipher.doFinal(config.credential.toByteArray())
        dataStore.edit { prefs ->
            prefs[apiUrlKey] = config.apiUrl.trimEnd('/')
            prefs[agentIdKey] = config.agentId
            prefs[hostnameKey] = config.hostname
            prefs[credentialKey] = Base64.encodeToString(encrypted, Base64.NO_WRAP)
        }
    }

    suspend fun clear() {
        dataStore.edit { it.clear() }
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
