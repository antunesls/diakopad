package org.diakopad.remote

import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingClientTest {
    private fun client(status: Int, body: String, seen: MutableList<okhttp3.Request>? = null) =
        OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            seen?.add(chain.request())
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(status).message("x").body(body.toResponseBody()).build()
        }).build()

    @Test fun requestSendsDeviceNameWithoutCookie() {
        val seen = mutableListOf<okhttp3.Request>()
        val api = PairingClient(ServerAddress.parse("192.168.0.40:8080"),
            client(200, "{\"request_id\":\"abc\",\"expires_in\":120,\"server_name\":\"DiakoPad (palco)\"}", seen))
        val started = api.request("Moto G")
        assertEquals("abc", started.requestId)
        assertEquals("DiakoPad (palco)", started.serverName)
        val request = seen.single()
        assertEquals("http://192.168.0.40:8080/api/pair/request", request.url.toString())
        assertNull(request.header("Cookie"))
        val body = Buffer().also { request.body!!.writeTo(it) }.readUtf8()
        assertEquals("Moto G", JSONObject(body).getString("device_name"))
    }

    @Test fun confirmReturnsDeviceToken() {
        val api = PairingClient(ServerAddress.parse("192.168.0.40:8080"),
            client(200, "{\"token\":\"tok_123\",\"server_name\":\"Palco\"}"))
        assertEquals("tok_123", api.confirm("abc", "123456").token)
    }

    @Test fun serverDetailBecomesTheErrorMessage() {
        val api = PairingClient(ServerAddress.parse("192.168.0.40:8080"),
            client(403, "{\"detail\":\"Código incorreto. Restam 4 tentativas.\"}"))
        val error = assertThrows(PairingClient.PairingFailed::class.java) { api.confirm("abc", "000000") }
        assertEquals("Código incorreto. Restam 4 tentativas.", error.message)
    }

    @Test fun oldServerWithoutPairingGetsAClearMessage() {
        assertTrue(PairingClient.errorMessage(404, "{\"detail\":\"Not Found\"}").contains("ainda não aceita pareamento"))
    }

    @Test fun codeMustBeSixDigits() {
        assertTrue(PairingClient.isValidCode("012345"))
        for (bad in listOf("12345", "1234567", "12a456", "")) assertFalse(PairingClient.isValidCode(bad))
        val api = PairingClient(ServerAddress.parse("192.168.0.40"), client(200, "{}"))
        assertThrows(IllegalArgumentException::class.java) { api.confirm("abc", "12") }
    }
}
