package org.diakopad.remote

import java.net.URI

/** Only accept LAN destinations; requests must never follow redirects to other hosts. */
class ServerAddress private constructor(private val base: String) {
    fun http(path: String): String {
        require(path.startsWith("/") && !path.startsWith("//"))
        return base + path
    }

    fun socket(): String = base.replaceFirst("http", "ws") + "/ws"

    companion object {
        fun parse(input: String): ServerAddress {
            val value = input.trim()
            val uri = URI(if ("://" in value) value else "http://$value")
            require(uri.scheme == "http" && uri.host != null && uri.userInfo == null)
            require(uri.rawPath.isNullOrEmpty() && uri.rawQuery == null && uri.rawFragment == null)
            val host = uri.host.lowercase()
            val parts = host.split(".")
            val privateIpv4 = if (parts.size == 4) parts.map { it.toIntOrNull() } else emptyList()
            val lan = host.endsWith(".local") || (privateIpv4.size == 4 && privateIpv4.all { it != null && it in 0..255 } &&
                (privateIpv4[0] == 10 || privateIpv4[0] == 192 && privateIpv4[1] == 168 ||
                    privateIpv4[0] == 172 && privateIpv4[1] in 16..31))
            require(lan) { "Informe um IP privado ou endereço .local da rede" }
            require(uri.port in -1..65535 && uri.port != 0)
            return ServerAddress("http://${uri.rawAuthority}")
        }
    }
}
