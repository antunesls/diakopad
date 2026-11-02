package org.diakopad.remote

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * Pareamento por código: o servidor mostra 6 dígitos na tela do laptop e o
 * celular troca o código por um token próprio. Nenhuma chamada aqui envia
 * token; a resposta de erro do servidor vira a mensagem mostrada ao usuário.
 */
class PairingClient(private val address: ServerAddress, client: OkHttpClient = OkHttpClient()) {
    class PairingFailed(message: String) : IOException(message)

    data class Started(val requestId: String, val expiresInSeconds: Int, val serverName: String)
    data class Paired(val token: String, val serverName: String)

    private val http = client.newBuilder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()

    fun request(deviceName: String): Started {
        val body = post("/api/pair/request", JSONObject().put("device_name", deviceName.take(60)))
        return Started(
            body.getString("request_id"),
            body.optInt("expires_in", 120),
            body.optString("server_name", "DiakoPad"),
        )
    }

    fun confirm(requestId: String, code: String): Paired {
        require(isValidCode(code)) { "O código tem 6 números" }
        val body = post("/api/pair/confirm", JSONObject().put("request_id", requestId).put("code", code))
        return Paired(body.getString("token"), body.optString("server_name", "DiakoPad"))
    }

    private fun post(path: String, json: JSONObject): JSONObject {
        val request = Request.Builder().url(address.http(path))
            .post(json.toString().toRequestBody("application/json; charset=utf-8".toMediaType())).build()
        http.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw PairingFailed(errorMessage(response.code, text))
            return JSONObject(text)
        }
    }

    companion object {
        fun isValidCode(code: String): Boolean = code.length == 6 && code.all { it in '0'..'9' }

        fun errorMessage(status: Int, body: String): String {
            val detail = runCatching { JSONObject(body).optString("detail") }.getOrNull()
            return when {
                status == 404 -> "Este DiakoPad ainda não aceita pareamento. Atualize o servidor ou use endereço e token."
                !detail.isNullOrBlank() -> detail
                else -> "Falha no pareamento (HTTP $status)"
            }
        }
    }
}
