package org.diakopad.remote

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import java.net.Inet4Address

data class FoundServer(val name: String, val address: String)

/**
 * Procura DiakoPads anunciados como `_diakopad._tcp` no Wi-Fi (o servidor
 * anuncia via Avahi). Callbacks do NSD chegam em outra thread; tudo é
 * repassado para a main thread. O Android só resolve um serviço por vez,
 * por isso a fila.
 */
class ServerDiscovery(context: Context, private val onChange: (List<FoundServer>) -> Unit) {
    private val nsd = context.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val main = Handler(Looper.getMainLooper())
    private val found = linkedMapOf<String, FoundServer>()
    private val queue = ArrayDeque<NsdServiceInfo>()
    private var resolving = false
    private var listener: NsdManager.DiscoveryListener? = null

    fun start() {
        if (listener != null) return
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) { main.post { listener = null } }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceFound(info: NsdServiceInfo) {
                main.post { queue.addLast(info); resolveNext() }
            }
            override fun onServiceLost(info: NsdServiceInfo) {
                main.post { if (found.remove(info.serviceName) != null) onChange(found.values.toList()) }
            }
        }
        listener = l
        nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, l)
    }

    fun stop() {
        listener?.let { runCatching { nsd.stopServiceDiscovery(it) } }
        listener = null
        queue.clear()
        main.removeCallbacksAndMessages(null)
    }

    @Suppress("DEPRECATION") // resolveService segue funcionando no 34+ e cobre o minSdk 26.
    private fun resolveNext() {
        if (resolving || listener == null) return
        val info = queue.removeFirstOrNull() ?: return
        resolving = true
        nsd.resolveService(info, object : NsdManager.ResolveListener {
            override fun onResolveFailed(service: NsdServiceInfo, errorCode: Int) {
                main.post {
                    resolving = false
                    if (errorCode == NsdManager.FAILURE_ALREADY_ACTIVE) {
                        queue.addLast(service)
                        main.postDelayed({ resolveNext() }, 300)
                    } else {
                        resolveNext()
                    }
                }
            }
            override fun onServiceResolved(service: NsdServiceInfo) {
                main.post {
                    resolving = false
                    val host = service.host
                    // Só IPv4 privado: o ServerAddress recusa o resto.
                    if (host is Inet4Address) {
                        val address = "${host.hostAddress}:${service.port}"
                        if (runCatching { ServerAddress.parse(address) }.isSuccess) {
                            found[service.serviceName] = FoundServer(service.serviceName, address)
                            onChange(found.values.toList())
                        }
                    }
                    resolveNext()
                }
            }
        })
    }

    companion object {
        const val SERVICE_TYPE = "_diakopad._tcp"
    }
}
