package org.diakopad.remote

import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class RemoteApiTest {
    @Test fun sendsCookieToConfiguredLanHostWithoutFollowingRedirects() {
        var cookie = ""
        val client = OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            cookie = chain.request().header("Cookie") ?: ""
            assertEquals("192.168.1.40", chain.request().url.host)
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(200).message("OK").body("{\"ok\":true}".toResponseBody()).build()
        }).build()
        val api = RemoteApi(ServerAddress.parse("192.168.1.40:8080"), "secret", client)
        assertEquals("{\"ok\":true}", api.request("GET", "/api/pads"))
        assertEquals("diakopad_token=secret", cookie)
    }

    @Test fun rejectsInvalidPathsAndHttpFailures() {
        val client = OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(401).message("Unauthorized").body("denied".toResponseBody()).build()
        }).build()
        val api = RemoteApi(ServerAddress.parse("192.168.1.40"), "token", client)
        assertThrows(IllegalArgumentException::class.java) { api.request("GET", "//evil.test/") }
        assertThrows(RemoteApi.HttpError::class.java) { api.request("GET", "/api/pads") }
    }
}
