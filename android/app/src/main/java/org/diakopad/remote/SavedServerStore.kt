package org.diakopad.remote

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class SavedServer(val name: String, val address: String, val token: String)

/**
 * Lembra o último DiakoPad usado. O token é cifrado com uma chave AES do
 * Android Keystore (não sai do aparelho); se a chave sumir ou falhar, o app
 * apenas volta para a tela de pareamento.
 */
class SavedServerStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences("diakopad_server", Context.MODE_PRIVATE)

    fun load(): SavedServer? = runCatching {
        val name = prefs.getString(KEY_NAME, null) ?: return null
        val address = prefs.getString(KEY_ADDRESS, null) ?: return null
        val iv = Base64.decode(prefs.getString(KEY_IV, null) ?: return null, Base64.NO_WRAP)
        val data = Base64.decode(prefs.getString(KEY_TOKEN, null) ?: return null, Base64.NO_WRAP)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
        SavedServer(name, address, String(cipher.doFinal(data), Charsets.UTF_8))
    }.getOrNull()

    fun save(server: SavedServer) {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val data = cipher.doFinal(server.token.toByteArray(Charsets.UTF_8))
        prefs.edit()
            .putString(KEY_NAME, server.name)
            .putString(KEY_ADDRESS, server.address)
            .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString(KEY_TOKEN, Base64.encodeToString(data, Base64.NO_WRAP))
            .apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "diakopad_server_token"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_NAME = "name"
        const val KEY_ADDRESS = "address"
        const val KEY_IV = "token_iv"
        const val KEY_TOKEN = "token"
    }
}
