package org.diakopad.remote

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Same-origin REST and WebSocket client; the token never enters a URL. */
class RemoteApi(
    private val address: ServerAddress,
    token: String,
    client: OkHttpClient = OkHttpClient(),
) {
    class HttpError(val status: Int, detail: String) : IOException("HTTP $status: $detail")

    private val cookie: String
    private val http = client.newBuilder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()

    init {
        require(token.isNotEmpty() && token.all { it.isLetterOrDigit() || it in "-_.~" })
        cookie = "diakopad_token=$token"
    }

    fun request(method: String, path: String, body: String? = null): String {
        val requestBody = if (method == "GET" || method == "DELETE") null else
            (body ?: "{}").toRequestBody("application/json; charset=utf-8".toMediaType())
        val request = Request.Builder().url(address.http(path)).header("Cookie", cookie)
            .method(method, requestBody).build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) throw HttpError(response.code, response.body?.string().orEmpty().take(200))
            return response.body?.string().orEmpty()
        }
    }

    fun subscribe(listener: WebSocketListener): WebSocket = http.newWebSocket(
        Request.Builder().url(address.socket()).header("Cookie", cookie).build(), listener
    )
}
