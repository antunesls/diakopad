package org.diakopad.remote

import java.net.URI
import java.net.URLDecoder

/**
 * Link do QR de pareamento mostrado no Config do navegador:
 * `diakopad://pair?a=<ip:porta>&r=<pedido>&c=<código>&n=<nome do servidor>`.
 * O endereço passa pelo ServerAddress, então só IP privado ou `.local`.
 */
data class PairingLink(val address: String, val requestId: String, val code: String, val serverName: String) {
    fun toUri(): String {
        fun enc(v: String) = java.net.URLEncoder.encode(v, "UTF-8")
        return "diakopad://pair?a=${enc(address)}&r=${enc(requestId)}&c=$code&n=${enc(serverName)}"
    }

    companion object {
        fun parse(raw: String): PairingLink {
            val uri = try { URI(raw.trim()) } catch (e: java.net.URISyntaxException) {
                throw IllegalArgumentException("Este QR não é de pareamento do DiakoPad", e)
            }
            require(uri.scheme == "diakopad" && uri.host == "pair") { "Este QR não é de pareamento do DiakoPad" }
            val query = (uri.rawQuery ?: "").split("&").filter { "=" in it }.associate { pair ->
                val (key, value) = pair.split("=", limit = 2)
                URLDecoder.decode(key, "UTF-8") to URLDecoder.decode(value, "UTF-8")
            }
            val address = requireNotNull(query["a"]) { "QR sem endereço" }
            ServerAddress.parse(address)
            val requestId = requireNotNull(query["r"]?.takeIf { it.isNotBlank() && it.length <= 64 }) { "QR sem pedido" }
            val code = requireNotNull(query["c"]?.takeIf { PairingClient.isValidCode(it) }) { "QR com código inválido" }
            return PairingLink(address, requestId, code, query["n"]?.takeIf { it.isNotBlank() }?.take(80) ?: "DiakoPad")
        }
    }
}
