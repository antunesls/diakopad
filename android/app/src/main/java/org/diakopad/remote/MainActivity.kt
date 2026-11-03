package org.diakopad.remote

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    // Link diakopad://pair aberto pela câmera do celular (QR do navegador).
    private val incomingLink = androidx.compose.runtime.mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        incomingLink.value = intent?.dataString
        setContent {
            MaterialTheme(colorScheme = DiakoColors) {
                // Sem esta Surface, textos sem cor explícita saem pretos no fundo escuro.
                androidx.compose.material3.Surface(
                    color = DiakoColors.background,
                    contentColor = DiakoColors.onBackground,
                    modifier = Modifier.fillMaxSize(),
                ) {
                    RemoteScreen(incomingLink.value) { incomingLink.value = null }
                }
            }
        }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        intent.dataString?.let { incomingLink.value = it }
    }
}

private val DiakoColors = darkColorScheme(
    primary = Color(0xFFFF7A3D),
    onPrimary = Color(0xFF1A0D05),
    secondary = Color(0xFF79A6FF),
    background = Color(0xFF12141A),
    onBackground = Color(0xFFF2F2F2),
    surface = Color(0xFF12141A),
    onSurface = Color(0xFFF2F2F2),
    surfaceVariant = Color(0xFF1C1F28),
    onSurfaceVariant = Color(0xFF9AA0AC),
    error = Color(0xFFFF8A8A),
)

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RemoteScreen(incomingLink: String?, onLinkHandled: () -> Unit) {
    val context = LocalContext.current
    val store = remember { SavedServerStore(context) }
    var saved by remember { mutableStateOf(store.load()) }
    var connectMessage by remember { mutableStateOf<String?>(null) }
    var serverName by remember { mutableStateOf("") }
    var api by remember { mutableStateOf<RemoteApi?>(null) }
    var status by remember { mutableStateOf("Desconectado") }
    var pads by remember { mutableStateOf(emptyList<JSONObject>()) }
    var scenes by remember { mutableStateOf(emptyList<JSONObject>()) }
    var sounds by remember { mutableStateOf(emptyList<JSONObject>()) }
    var kits by remember { mutableStateOf(emptyList<JSONObject>()) }
    var fx by remember { mutableStateOf<JSONObject?>(null) }
    var effects by remember { mutableStateOf(emptyList<JSONObject>()) }
    var effectCatalog by remember { mutableStateOf(emptyList<JSONObject>()) }
    var sequencer by remember { mutableStateOf<JSONObject?>(null) }
    var patterns by remember { mutableStateOf(emptyList<JSONObject>()) }
    var looper by remember { mutableStateOf<JSONObject?>(null) }
    var metronome by remember { mutableStateOf<JSONObject?>(null) }
    var styles by remember { mutableStateOf(emptyList<JSONObject>()) }
    var signatures by remember { mutableStateOf(emptyList<JSONObject>()) }
    var tempo by remember { mutableStateOf("--") }
    var master by remember { mutableStateOf<JSONObject?>(null) }
    var section by remember { mutableStateOf(Section.PADS) }
    var currentSceneId by remember { mutableStateOf("") }
    var inspectorOpen by remember { mutableStateOf(false) }
    var sceneName by remember { mutableStateOf("") }
    var confirmPanic by remember { mutableStateOf(false) }
    var selectedPad by remember { mutableStateOf(1) }
    var mixField by remember { mutableStateOf("volume_db") }
    var mixValue by remember { mutableStateOf("") }
    var kitName by remember { mutableStateOf("") }
    var kitCategory by remember { mutableStateOf("") }
    var kitSamples by remember { mutableStateOf(emptyMap<Int, Int>()) }
    var deleteScene by remember { mutableStateOf<Int?>(null) }
    var deleteKit by remember { mutableStateOf<Int?>(null) }
    val scope = rememberCoroutineScope()

    fun connect(server: SavedServer) {
        try {
            api = RemoteApi(ServerAddress.parse(server.address), server.token)
            serverName = server.name
            status = "Conectando…"
            connectMessage = null
        } catch (e: Exception) {
            connectMessage = e.message ?: "Endereço ou token inválido"
        }
    }

    fun forget(message: String) {
        store.clear()
        saved = null
        api = null
        status = "Desconectado"
        connectMessage = message
    }

    LaunchedEffect(Unit) { saved?.let { connect(it) } }

    fun command(method: String, path: String, body: String? = null, onSuccess: (() -> Unit)? = null) {
        val remote = api ?: return
        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) { remote.request(method, path, body) }
                if (result.isNotBlank() && JSONObject(result).opt("engine_applied") == false) {
                    status = "Comando salvo, motor de áudio indisponível"
                }
                onSuccess?.invoke()
            } catch (e: Exception) {
                status = "Falha: ${e.message ?: "sem conexão"}"
            }
        }
    }

    LaunchedEffect(api) {
        val remote = api ?: return@LaunchedEffect
        try {
            pads = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/pads") }).objects()
            scenes = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/scenes") }).objects()
            sounds = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/sounds") }).objects()
            kits = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/kits") }).objects()
            fx = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/fx-buses") })
            effects = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/pad-effects") }).objects()
            effectCatalog = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/effects/catalog") }).objects()
            sequencer = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/sequencer") })
            patterns = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/patterns") }).objects()
            looper = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/looper") })
            metronome = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/metronome") })
            styles = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/metronome/styles") }).objects()
            signatures = JSONArray(withContext(Dispatchers.IO) { remote.request("GET", "/api/metronome/time-signatures") }).objects()
            tempo = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/tempo") }).optString("bpm", "--")
            master = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/master") })
        } catch (e: Exception) {
            if (e is CancellationException) throw e
            if (e is RemoteApi.HttpError && e.status == 401) {
                forget("Este celular foi removido no DiakoPad (ou o token mudou). Pareie de novo.")
            } else {
                api = null
                status = "Desconectado"
                connectMessage = "Não consegui falar com $serverName. O celular está no mesmo Wi-Fi e o DiakoPad está ligado?"
            }
            return@LaunchedEffect
        }
        var revoked = false
        while (!revoked) {
            val closed = CompletableDeferred<Unit>()
            val socket = remote.subscribe(object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    scope.launch { status = "Ao vivo" }
                }
                override fun onMessage(webSocket: WebSocket, text: String) {
                    scope.launch {
                        try {
                            val msg = JSONObject(text)
                            when (msg.optString("type")) {
                                "pads" -> pads = msg.getJSONArray("pads").objects()
                                "scenes" -> {
                                    scenes = msg.getJSONArray("scenes").objects()
                                    currentSceneId = msg.optString("current_scene_id", "")
                                }
                                "sounds" -> sounds = msg.getJSONArray("sounds").objects()
                                "tempo" -> tempo = msg.optString("bpm", "--")
                                "master" -> master = msg
                                "fx_buses" -> fx = msg
                                "pad_effects" -> effects = msg.getJSONArray("pad_effects").objects()
                                "sequencer" -> sequencer = msg
                                "patterns" -> patterns = msg.getJSONArray("patterns").objects()
                                "looper" -> looper = msg
                                "metronome" -> metronome = msg
                            }
                        } catch (_: Exception) { /* A future event type must not stop the stream. */ }
                    }
                }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { closed.complete(Unit) }
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    // Handshake recusado = acesso removido no laptop.
                    if (response?.code == 403 || response?.code == 401) revoked = true
                    closed.complete(Unit)
                }
            })
            try { closed.await() } finally { socket.cancel() }
            if (revoked) break
            status = "Reconectando…"
            delay(2000)
        }
        forget("Este celular foi removido no DiakoPad. Pareie de novo.")
    }

    LaunchedEffect(incomingLink) {
        if (incomingLink != null && api != null) {
            api = null
            status = "Desconectado"
        }
    }

    if (api == null) {
        Scaffold { insets ->
            Column(Modifier.fillMaxSize().padding(insets)) {
                ConnectScreen(
                    saved = saved,
                    message = connectMessage,
                    incomingLink = incomingLink,
                    onLinkHandled = onLinkHandled,
                    onConnect = { server ->
                        try {
                            store.save(server)
                            saved = server
                        } catch (_: Exception) {
                            // Sem Keystore: conecta mesmo assim, só não lembra.
                        }
                        connect(server)
                    },
                    onForget = { forget("DiakoPad esquecido. Para voltar, pareie de novo.") },
                )
            }
        }
        return
    }

    val selectedPadJson = pads.firstOrNull { it.optInt("pad_number") == selectedPad }
    val stage = StageStatus(
        serverName = serverName,
        status = status,
        sceneName = scenes.firstOrNull { it.optInt("id").toString() == currentSceneId }?.optString("name"),
        bpm = tempo.substringBefore('.'),
        sequencerRunning = sequencer?.optBoolean("running") == true,
        looperState = looper?.optString("state", "stopped") ?: "stopped",
        padsFilled = pads.count { PadModel.hasSample(it) },
        padsTotal = pads.size,
        activeScenes = scenes.count { it.optBoolean("active") },
    )
    ConsoleShell(
        section = section,
        onSection = { section = it; inspectorOpen = false },
        stage = stage,
        onToggleSequencer = {
            command("POST", "/api/sequencer/transport", "{\"running\":${sequencer?.optBoolean("running") != true}}")
        },
        onPanic = { command("POST", "/api/panic") },
        onSwitchServer = { api = null; status = "Desconectado"; connectMessage = null },
        inspector = if (section == Section.PADS) {
            {
                PadInspector(
                    pad = selectedPadJson,
                    fx = fx,
                    effects = PadModel.padEffects(effects, effectCatalog, selectedPad),
                    onMix = { field, value ->
                        val cmd = StageCommands.mix(selectedPad, field, value)
                        command(cmd.method, cmd.path, cmd.json)
                    },
                    onSend = { bus, level ->
                        val cmd = StageCommands.send(selectedPad, bus, level)
                        command(cmd.method, cmd.path, cmd.json)
                    },
                    onChangeSound = { inspectorOpen = false; section = Section.SOUNDS },
                    onPlay = { command("POST", StageCommands.trigger(selectedPad).path) },
                    onOpenEffects = { inspectorOpen = false; section = Section.EFFECTS },
                )
            }
        } else null,
        inspectorOpen = inspectorOpen,
        onCloseInspector = { inspectorOpen = false },
    ) { inspectorDocked ->
        Column(Modifier.fillMaxSize()) {
            run {
                when (section) {
                    Section.PADS -> Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        PadGrid(
                            pads = pads,
                            selected = selectedPad,
                            onTap = { number ->
                                selectedPad = number
                                command("POST", StageCommands.trigger(number).path)
                            },
                            onLongPress = { number -> selectedPad = number; inspectorOpen = true },
                            modifier = Modifier.weight(1f),
                        )
                        if (!inspectorDocked) SelectedPadBar(selectedPadJson) { inspectorOpen = true }
                    }
                    Section.SCENES -> Column {
                        OutlinedTextField(sceneName, { sceneName = it }, label = { Text("Nome da cena") })
                        Button(onClick = { if (sceneName.isNotBlank()) command("POST", "/api/scenes", JSONObject().put("name", sceneName).toString()) }) {
                            Text("Salvar cena")
                        }
                        LazyColumn(Modifier.weight(1f)) { items(scenes) { scene ->
                            val id = scene.optInt("id")
                            Row(Modifier.fillMaxWidth()) {
                                TextButton(onClick = { command("POST", StageCommands.loadScene(id).path) }) {
                                    Text(scene.optString("name", "Cena $id"))
                                }
                                TextButton(onClick = { command("POST", "/api/scenes/$id/active", "{\"active\":${!scene.optBoolean("active")}}") }) {
                                    Text(if (scene.optBoolean("active")) "Ativa" else "Inativa")
                                }
                                TextButton(onClick = { deleteScene = id }) { Text("Excluir") }
                            }
                        } }
                        deleteScene?.let { id ->
                            Text("Excluir a cena definitivamente?")
                            Row {
                                Button(onClick = { command("DELETE", "/api/scenes/$id"); deleteScene = null }) { Text("Excluir cena") }
                                TextButton(onClick = { deleteScene = null }) { Text("Cancelar") }
                            }
                        }
                    }
                    Section.KITS -> KitScreen(kits, kitName, { kitName = it }, kitCategory, { kitCategory = it },
                        kitSamples, { kitSamples = it }, sounds, selectedPad, { selectedPad = it },
                        { kit ->
                            val cmd = StageCommands.loadKit(kit)
                            command(cmd.method, cmd.path, cmd.json)
                        }, { kit -> deleteKit = kit }, {
                            try {
                                val cmd = StageCommands.createKit(kitName, kitCategory, kitSamples)
                                command(cmd.method, cmd.path, cmd.json) {
                                    kitSamples = emptyMap()
                                    kitName = ""
                                    scope.launch {
                                        try { kits = JSONArray(withContext(Dispatchers.IO) { api?.request("GET", "/api/kits").orEmpty() }).objects() }
                                        catch (e: Exception) { status = "Kits: ${e.message}" }
                                    }
                                }
                            } catch (e: IllegalArgumentException) { status = "Selecione nome e pelo menos um som" }
                        }, deleteKit, { deleteKit = null }, { kit ->
                            command("DELETE", "/api/kits/$kit") {
                                kits = kits.filterNot { it.optInt("id") == kit }
                            }
                            deleteKit = null
                        })
                    Section.SOUNDS -> Column {
                        Text("Selecione o pad que receberá o som: $selectedPad")
                        Row(Modifier.horizontalScroll(rememberScrollState())) {
                            for (number in 1..16) TextButton(onClick = { selectedPad = number }) { Text("$number") }
                        }
                        Button(onClick = {
                            val cmd = StageCommands.assign(selectedPad, null)
                            command(cmd.method, cmd.path, cmd.json)
                        }) { Text("Remover som do pad") }
                        LazyColumn(Modifier.weight(1f)) { items(sounds) { sound ->
                            TextButton(onClick = {
                                val cmd = StageCommands.assign(selectedPad, sound.getInt("id"))
                                command(cmd.method, cmd.path, cmd.json)
                            }, modifier = Modifier.fillMaxWidth()) {
                                Text(sound.optString("display_name", "Som"))
                            }
                        } }
                    }
                    Section.MIX -> Column {
                        Text("Pad selecionado: $selectedPad")
                        Row(Modifier.horizontalScroll(rememberScrollState())) {
                            for (number in 1..16) TextButton(onClick = { selectedPad = number }) { Text("$number") }
                        }
                        Row(Modifier.horizontalScroll(rememberScrollState())) {
                            for (field in listOf("volume_db", "pan", "tone", "tune")) {
                                TextButton(onClick = { mixField = field }) { Text(if (mixField == field) "● $field" else field) }
                            }
                        }
                        OutlinedTextField(mixValue, { mixValue = it }, label = { Text("$mixField para pad $selectedPad") })
                        Button(onClick = {
                            mixValue.toDoubleOrNull()?.let { value ->
                                try {
                                    val cmd = StageCommands.mix(selectedPad, mixField, value)
                                    command(cmd.method, cmd.path, cmd.json)
                                } catch (e: IllegalArgumentException) { status = e.message ?: "Valor inválido" }
                            }
                        }) { Text("Aplicar mix") }
                    }
                    Section.EFFECTS -> EffectsScreen(selectedPad, { selectedPad = it }, fx, effects, effectCatalog,
                        { pad, bus, level ->
                            val cmd = StageCommands.send(pad, bus, level)
                            command(cmd.method, cmd.path, cmd.json)
                        }, { method, path, body -> command(method, path, body) })
                    Section.TRANSPORT -> TransportScreen(tempo, sequencer, patterns, looper, metronome,
                        styles, signatures, { method, path, body -> command(method, path, body) })
                    Section.MASTER -> Column(Modifier.verticalScroll(rememberScrollState())) {
                        Text("Volume: ${master?.optString("volume", "--") ?: "--"}")
                        val volumeNow = master?.optDouble("volume", 100.0)?.toFloat() ?: 100f
                        var volumeSlider by remember(volumeNow) { mutableFloatStateOf(volumeNow) }
                        Slider(value = volumeSlider, onValueChange = { volumeSlider = it }, valueRange = 0f..100f,
                            onValueChangeFinished = {
                                val cmd = StageCommands.master("volume", volumeSlider.toDouble())
                                command(cmd.method, cmd.path, cmd.json)
                            })
                        Button(onClick = { command("POST", "/api/master", "{\"muted\":true}") }) { Text("Mute") }
                        Button(onClick = { command("POST", "/api/master", "{\"muted\":false}") }) { Text("Unmute") }
                        Text("Limiter: ${if (master?.optBoolean("limiter_enabled") == true) "ligado" else "desligado"}")
                        Button(onClick = { command("POST", "/api/master", "{\"limiter_enabled\":${master?.optBoolean("limiter_enabled") != true}}") }) {
                            Text("Alternar limiter")
                        }
                        Text("Compressor: ${if (master?.optBoolean("compressor_enabled") == true) "ligado" else "desligado"}")
                        Button(onClick = { command("POST", "/api/master", "{\"compressor_enabled\":${master?.optBoolean("compressor_enabled") != true}}") }) {
                            Text("Alternar compressor")
                        }
                        for ((field, label, range) in listOf(
                            Triple("limiter_threshold_db", "Limiar do limiter", -12f..0f),
                            Triple("compressor_threshold_db", "Limiar do compressor", -30f..0f),
                            Triple("compressor_ratio", "Ratio do compressor", 1f..10f),
                            Triple("compressor_makeup_db", "Ganho do compressor", 0f..12f),
                        )) {
                            val current = master?.optDouble(field, range.start.toDouble())?.toFloat() ?: range.start
                            var slider by remember(field, current) { mutableFloatStateOf(current.coerceIn(range.start, range.endInclusive)) }
                            Text("$label: ${"%.1f".format(slider)}")
                            Slider(value = slider, onValueChange = { slider = it }, valueRange = range,
                                onValueChangeFinished = {
                                    val cmd = StageCommands.master(field, slider.toDouble())
                                    command(cmd.method, cmd.path, cmd.json)
                                })
                        }
                        Button(onClick = { confirmPanic = true }) { Text("PANIC") }
                        if (confirmPanic) {
                            Text("Interromper tudo e silenciar o master?")
                            Row {
                                Button(onClick = { command("POST", "/api/panic"); confirmPanic = false }) { Text("Confirmar PANIC") }
                                TextButton(onClick = { confirmPanic = false }) { Text("Cancelar") }
                            }
                        }
                    }
                }
            }
        }
    }
}
