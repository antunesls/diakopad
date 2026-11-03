package org.diakopad.remote

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class PadModelTest {
    private fun pad(n: Int, name: String? = "Som $n") = JSONObject()
        .put("pad_number", n).put("display_name", name ?: JSONObject.NULL)
        .put("sample_id", if (name == null) JSONObject.NULL else n).put("has_sample", name != null)

    @Test fun gridMatchesControllerWithPadOneBottomLeft() {
        val order = PadModel.displayOrder((1..16).map { pad(it) }).map { it?.optInt("pad_number") }
        assertEquals(listOf(13, 14, 15, 16), order.take(4))
        assertEquals(listOf(1, 2, 3, 4), order.takeLast(4))
    }

    @Test fun namesAndLabels() {
        assertEquals("Vazio", PadModel.name(pad(3, null)))
        assertEquals("Caixa", PadModel.name(pad(2, "Caixa")))
        assertEquals("centro", PadModel.panLabel(0))
        assertEquals("E 30", PadModel.panLabel(-30))
        assertEquals("+2 st", PadModel.tuneLabel(2.0))
        assertEquals("original", PadModel.tuneLabel(0.0))
    }

    @Test fun toneFollowsWebFormula() {
        assertEquals(100, PadModel.toneFromCutoff(null))
        assertEquals(100, PadModel.toneFromCutoff(20000.0))
        assertEquals(0, PadModel.toneFromCutoff(200.0))
        assertEquals(50, PadModel.toneFromCutoff(2000.0))
    }

    @Test fun readsSendLevels() {
        val fx = JSONObject("{\"sends\":[{\"pad_number\":2,\"bus\":\"hall\",\"level\":35}]}")
        assertEquals(35, PadModel.sendLevel(fx, 2, "hall"))
        assertEquals(0, PadModel.sendLevel(fx, 2, "plate"))
        assertEquals(0, PadModel.sendLevel(null, 2, "hall"))
    }

    @Test fun parsesPairingLinkFromQr() {
        val link = PairingLink.parse("diakopad://pair?a=192.168.0.40%3A8080&r=abc_123&c=048213&n=DiakoPad+%28MARK42%29")
        assertEquals("192.168.0.40:8080", link.address)
        assertEquals("abc_123", link.requestId)
        assertEquals("048213", link.code)
        assertEquals("DiakoPad (MARK42)", link.serverName)
    }

    @Test fun rejectsForeignOrUnsafeQr() {
        for (bad in listOf(
            "https://example.com/?a=1",
            "diakopad://pair?a=8.8.8.8%3A8080&r=x&c=123456",
            "diakopad://pair?a=192.168.0.40%3A8080&r=x&c=12",
            "diakopad://other?a=192.168.0.40&r=x&c=123456",
        )) {
            assertThrows(IllegalArgumentException::class.java) { PairingLink.parse(bad) }
        }
    }
}
