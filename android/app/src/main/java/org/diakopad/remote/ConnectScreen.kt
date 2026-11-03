package org.diakopad.remote

import android.os.Build
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning

private val Accent = Color(0xFFFF7A3D)
private val Ok = Color(0xFF3FBF6F)
private val Danger = Color(0xFFFF8A8A)

private data class PairingState(val server: FoundServer, val requestId: String, val expiresAtMs: Long)

fun deviceDisplayName(): String {
    val maker = Build.MANUFACTURER.orEmpty().replaceFirstChar { it.uppercase() }
    val model = Build.MODEL.orEmpty()
    return if (model.startsWith(maker, ignoreCase = true)) model else "$maker $model".trim()
}

/** Escolhe o DiakoPad: último usado, encontrados na rede (com código) ou endereço manual. */
@Composable
fun ConnectScreen(
    saved: SavedServer?,
    message: String?,
    incomingLink: String?,
    onLinkHandled: () -> Unit,
    onConnect: (SavedServer) -> Unit,
    onForget: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var servers by remember { mutableStateOf(emptyList<FoundServer>()) }
    var pairing by remember { mutableStateOf<PairingState?>(null) }
    var code by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var manual by remember { mutableStateOf(false) }
    var host by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var askLink by remember { mutableStateOf<PairingLink?>(null) }

    DisposableEffect(Unit) {
        val discovery = ServerDiscovery(context) { servers = it }
        discovery.start()
        onDispose { discovery.stop() }
    }

    fun startPairing(server: FoundServer) {
        busy = true
        error = null
        scope.launch {
            try {
                val started = withContext(Dispatchers.IO) {
                    PairingClient(ServerAddress.parse(server.address)).request(deviceDisplayName())
                }
                code = ""
                pairing = PairingState(server, started.requestId, System.currentTimeMillis() + started.expiresInSeconds * 1000L)
            } catch (e: Exception) {
                error = e.message ?: "Não consegui falar com ${server.name}"
            } finally {
                busy = false
            }
        }
    }

    // QR do Config no navegador (ou link aberto pela câmera do celular):
    // endereço, pedido e código já vêm prontos, nada para digitar.
    fun pairWithLink(raw: String) {
        busy = true
        error = null
        scope.launch {
            try {
                val link = PairingLink.parse(raw)
                val paired = withContext(Dispatchers.IO) {
                    PairingClient(ServerAddress.parse(link.address)).confirm(link.requestId, link.code, deviceDisplayName())
                }
                onConnect(SavedServer(paired.serverName, link.address, paired.token))
            } catch (e: Exception) {
                error = e.message ?: "Não consegui parear com este QR"
            } finally {
                busy = false
            }
        }
    }

    // Link vindo de fora do app (câmera comum, navegador): confirma antes de
    // parear, para um link qualquer não ligar o tablet a outro servidor.
    LaunchedEffect(incomingLink) {
        val link = incomingLink ?: return@LaunchedEffect
        onLinkHandled()
        try {
            askLink = PairingLink.parse(link)
        } catch (e: IllegalArgumentException) {
            error = e.message
        }
    }

    fun scanQr() {
        val options = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(context, options).startScan()
            .addOnSuccessListener { barcode -> barcode.rawValue?.let { pairWithLink(it) } }
            .addOnFailureListener {
                error = "Leitor de QR indisponível neste aparelho. Aponte a câmera do celular para o QR ou use o código."
            }
    }

    fun confirm(state: PairingState, typed: String) {
        busy = true
        error = null
        scope.launch {
            try {
                val paired = withContext(Dispatchers.IO) {
                    PairingClient(ServerAddress.parse(state.server.address)).confirm(state.requestId, typed)
                }
                pairing = null
                onConnect(SavedServer(paired.serverName, state.server.address, paired.token))
            } catch (e: Exception) {
                code = ""
                error = e.message ?: "Código não aceito"
            } finally {
                busy = false
            }
        }
    }

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Box(Modifier.size(52.dp).clip(RoundedCornerShape(14.dp)).background(Color(0xFF171A22))) {
                Image(painterResource(R.drawable.ic_launcher_foreground), contentDescription = null, modifier = Modifier.size(52.dp))
            }
            Column {
                Text("DiakoPad", fontSize = 26.sp, fontWeight = FontWeight.Bold)
                Text("Controle remoto do palco", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
            }
        }

        val shownError = error ?: message
        if (shownError != null) {
            Surface(color = Color(0xFF2A1416), shape = RoundedCornerShape(12.dp)) {
                Text(shownError, color = Danger, modifier = Modifier.padding(12.dp), fontSize = 14.sp)
            }
        }

        askLink?.let { link ->
            Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(14.dp),
                border = androidx.compose.foundation.BorderStroke(2.dp, Accent)) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Parear com ${link.serverName}?", fontWeight = FontWeight.Bold, fontSize = 17.sp)
                    Text(link.address, fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { askLink = null; pairWithLink(link.toUri()) }) { Text("Parear") }
                        OutlinedButton(onClick = { askLink = null }) { Text("Cancelar") }
                    }
                }
            }
        }

        val current = pairing
        if (current == null) {
            Button(onClick = { scanQr() }, enabled = !busy, modifier = Modifier.fillMaxWidth()) {
                Text(if (busy) "Pareando…" else "Escanear QR do laptop", fontSize = 16.sp, modifier = Modifier.padding(vertical = 6.dp))
            }
            Text(
                "No navegador do DiakoPad: Config ▸ Celulares pareados ▸ Mostrar QR.",
                color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
            )
        }
        if (current != null) {
            PairingPanel(
                state = current,
                code = code,
                busy = busy,
                onCode = { typed ->
                    code = typed.filter { it.isDigit() }.take(6)
                    if (PairingClient.isValidCode(code) && !busy) confirm(current, code)
                },
                onRetry = { startPairing(current.server) },
                onCancel = { pairing = null; error = null },
            )
            return@Column
        }

        if (saved != null) {
            SectionLabel("Último usado")
            ServerCard(
                title = saved.name,
                subtitle = saved.address,
                highlighted = true,
                online = servers.any { it.address == saved.address },
                onClick = { onConnect(saved) },
                trailing = {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        TextButton(onClick = onForget) { Text("Esquecer") }
                        Button(onClick = { onConnect(saved) }) { Text("Entrar") }
                    }
                },
            )
        }

        SectionLabel("Encontrados nesta rede")
        val others = servers.filter { it.address != saved?.address }
        if (others.isEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                Text(
                    if (servers.isEmpty()) "Procurando DiakoPad no Wi-Fi…" else "Nenhum outro DiakoPad por aqui.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 14.sp,
                )
            }
        }
        for (server in others) {
            ServerCard(
                title = server.name,
                subtitle = server.address,
                highlighted = false,
                online = true,
                onClick = { if (!busy) startPairing(server) },
                trailing = { if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp) else Text("Parear ›", color = Accent) },
            )
        }
        Text(
            "Sem QR: toque no DiakoPad e digite o código que vai aparecer na tela do laptop.",
            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp,
        )

        Spacer(Modifier.size(8.dp))
        TextButton(onClick = { manual = !manual }) {
            Text(if (manual) "Fechar endereço manual" else "Não apareceu? Digitar endereço e token")
        }
        if (manual) {
            OutlinedTextField(host, { host = it }, label = { Text("IP ou nome .local:8080") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            OutlinedTextField(token, { token = it.trim() }, label = { Text("Token do servidor") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                try {
                    ServerAddress.parse(host)
                    onConnect(SavedServer(host.trim(), host.trim().removePrefix("http://"), token))
                } catch (e: Exception) {
                    error = e.message ?: "Endereço inválido"
                }
            }, enabled = host.isNotBlank() && token.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Conectar") }
            Text(
                "O token fica guardado cifrado neste aparelho. Use só em Wi-Fi confiável: a conexão local não é criptografada.",
                color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp,
            )
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(text.uppercase(), fontSize = 11.sp, letterSpacing = 1.2.sp, fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun ServerCard(
    title: String,
    subtitle: String,
    highlighted: Boolean,
    online: Boolean,
    onClick: () -> Unit,
    trailing: @Composable () -> Unit,
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(14.dp),
        border = if (highlighted) androidx.compose.foundation.BorderStroke(2.dp, Accent) else null,
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(10.dp).clip(CircleShape).background(if (online) Ok else Color(0xFF6B7180)))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                Text(subtitle, fontFamily = FontFamily.Monospace, fontSize = 12.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            trailing()
        }
    }
}

@Composable
private fun PairingPanel(
    state: PairingState,
    code: String,
    busy: Boolean,
    onCode: (String) -> Unit,
    onRetry: () -> Unit,
    onCancel: () -> Unit,
) {
    var secondsLeft by remember(state) { mutableIntStateOf(((state.expiresAtMs - System.currentTimeMillis()) / 1000).toInt()) }
    LaunchedEffect(state) {
        while (secondsLeft > 0) {
            delay(1000)
            secondsLeft = ((state.expiresAtMs - System.currentTimeMillis()) / 1000).toInt().coerceAtLeast(0)
        }
    }
    SectionLabel("Parear com ${state.server.name}")
    Text("Digite o código de 6 números que apareceu na tela do DiakoPad.", fontSize = 15.sp)
    OutlinedTextField(
        value = code,
        onValueChange = onCode,
        enabled = !busy && secondsLeft > 0,
        singleLine = true,
        placeholder = { Text("000000", fontSize = 34.sp, letterSpacing = 10.sp, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()) },
        textStyle = MaterialTheme.typography.headlineLarge.copy(
            fontSize = 34.sp, letterSpacing = 10.sp, textAlign = TextAlign.Center, fontFamily = FontFamily.Monospace,
        ),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
        modifier = Modifier.fillMaxWidth(),
    )
    if (busy) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            Text("Conferindo…")
        }
    } else if (secondsLeft > 0) {
        Text("O código vale por mais ${secondsLeft / 60}:${"%02d".format(secondsLeft % 60)}. Depois disso, este celular fica pareado e entra direto.",
            color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp)
    } else {
        Text("O código expirou.", color = Danger, fontSize = 14.sp)
        Button(onClick = onRetry, modifier = Modifier.fillMaxWidth()) { Text("Pedir novo código") }
    }
    OutlinedButton(onClick = onCancel, modifier = Modifier.fillMaxWidth()) { Text("Cancelar") }
}
