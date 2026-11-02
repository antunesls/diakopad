package org.diakopad.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ServerAddressTest {
    @Test fun acceptsLanAddressAndBuildsApiUrl() {
        val server = ServerAddress.parse("192.168.1.40:8080")
        assertEquals("http://192.168.1.40:8080/api/pads", server.http("/api/pads"))
        assertEquals("ws://192.168.1.40:8080/ws", server.socket())
    }

    @Test fun rejectsCredentialsPathAndPublicHost() {
        for (value in listOf("example.com:8080", "https://user:pass@192.168.1.40", "192.168.1.40:8080/evil")) {
            assertThrows(IllegalArgumentException::class.java) { ServerAddress.parse(value) }
        }
    }
}
