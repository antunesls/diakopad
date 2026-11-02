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
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { RemoteScreen() } }
    }
}

private fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RemoteScreen() {
    var host by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var api by remember { mutableStateOf<RemoteApi?>(null) }
    var status by remember { mutableStateOf("Desconectado") }
    var pads by remember { mutableStateOf(emptyList<JSONObject>()) }
    var scenes by remember { mutableStateOf(emptyList<JSONObject>()) }
    var sounds by remember { mutableStateOf(emptyList<JSONObject>()) }
    var tempo by remember { mutableStateOf("--") }
    var master by remember { mutableStateOf<JSONObject?>(null) }
    var tab by remember { mutableStateOf("Pads") }
    var sceneName by remember { mutableStateOf("") }
    var bpmInput by remember { mutableStateOf("") }
    var confirmPanic by remember { mutableStateOf(false) }
    var selectedPad by remember { mutableStateOf(1) }
    var mixField by remember { mutableStateOf("volume_db") }
    var mixValue by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()

    fun command(method: String, path: String, body: String? = null) {
        val remote = api ?: return
        scope.launch {
            try {
                withContext(Dispatchers.IO) { remote.request(method, path, body) }
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
            tempo = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/tempo") }).optString("bpm", "--")
            master = JSONObject(withContext(Dispatchers.IO) { remote.request("GET", "/api/master") })
        } catch (e: Exception) {
            status = "Conexão recusada: ${e.message ?: "verifique o endereço e token"}"
            return@LaunchedEffect
        }
        while (true) {
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
                                "scenes" -> scenes = msg.getJSONArray("scenes").objects()
                                "sounds" -> sounds = msg.getJSONArray("sounds").objects()
                                "tempo" -> tempo = msg.optString("bpm", "--")
                                "master" -> master = msg
                            }
                        } catch (_: Exception) { /* A future event type must not stop the stream. */ }
                    }
                }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { closed.complete(Unit) }
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { closed.complete(Unit) }
            })
            try { closed.await() } finally { socket.cancel() }
            status = "Reconectando…"
            delay(2000)
        }
    }

    Scaffold(topBar = { TopAppBar(title = { Text("DiakoPad · $status") }) }) { insets ->
        Column(Modifier.fillMaxSize().padding(insets).padding(12.dp)) {
            if (api == null) {
                Text("Conectar ao DiakoPad na mesma rede Wi-Fi")
                OutlinedTextField(host, { host = it }, label = { Text("IP ou nome .local:8080") }, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(token, { token = it }, label = { Text("Token do servidor") },
                    visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
                Button(onClick = {
                    try { api = RemoteApi(ServerAddress.parse(host), token); status = "Conectando…" }
                    catch (e: Exception) { status = e.message ?: "Endereço ou token inválido" }
                }) { Text("Conectar") }
                Text("O token não é salvo no aparelho. Use apenas em Wi-Fi confiável: HTTP não criptografa a conexão.")
            } else {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    for (name in listOf("Pads", "Cenas", "Biblioteca", "Mix", "Transporte", "Master")) {
                        TextButton(onClick = { tab = name }) { Text(if (tab == name) "● $name" else name) }
                    }
                    TextButton(onClick = { api = null; token = ""; status = "Desconectado" }) { Text("Sair") }
                }
                when (tab) {
                    "Pads" -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        items(pads.chunked(2)) { row ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                row.forEach { pad ->
                                    val number = pad.optInt("pad_number")
                                    Button(onClick = { command("POST", StageCommands.trigger(number).path) }, modifier = Modifier.weight(1f)) {
                                        Text("$number · ${pad.optString("sample_name", "Pad")}")
                                    }
                                }
                            }
                        }
                    }
                    "Cenas" -> Column {
                        OutlinedTextField(sceneName, { sceneName = it }, label = { Text("Nome da cena") })
                        Button(onClick = { if (sceneName.isNotBlank()) command("POST", "/api/scenes", JSONObject().put("name", sceneName).toString()) }) {
                            Text("Salvar cena")
                        }
                        LazyColumn { items(scenes) { scene ->
                            val id = scene.optInt("id")
                            Row(Modifier.fillMaxWidth()) {
                                TextButton(onClick = { command("POST", StageCommands.loadScene(id).path) }) {
                                    Text(scene.optString("name", "Cena $id"))
                                }
                                TextButton(onClick = { command("POST", "/api/scenes/$id/active", "{\"active\":${!scene.optBoolean("active")}}") }) {
                                    Text(if (scene.optBoolean("active")) "Ativa" else "Inativa")
                                }
                            }
                        } }
                    }
                    "Biblioteca" -> Column {
                        Text("Selecione o pad que receberá o som: $selectedPad")
                        Row(Modifier.horizontalScroll(rememberScrollState())) {
                            for (number in 1..16) TextButton(onClick = { selectedPad = number }) { Text("$number") }
                        }
                        Button(onClick = {
                            val cmd = StageCommands.assign(selectedPad, null)
                            command(cmd.method, cmd.path, cmd.json)
                        }) { Text("Remover som do pad") }
                        LazyColumn { items(sounds) { sound ->
                            TextButton(onClick = {
                                val cmd = StageCommands.assign(selectedPad, sound.getInt("id"))
                                command(cmd.method, cmd.path, cmd.json)
                            }, modifier = Modifier.fillMaxWidth()) {
                                Text(sound.optString("display_name", "Som"))
                            }
                        } }
                    }
                    "Mix" -> Column {
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
                    "Transporte" -> Column {
                        Text("BPM atual: $tempo")
                        OutlinedTextField(bpmInput, { bpmInput = it }, label = { Text("Novo BPM") })
                        Button(onClick = { bpmInput.toFloatOrNull()?.let { command("POST", "/api/tempo", JSONObject().put("bpm", it).toString()) } }) { Text("Definir BPM") }
                        Row { Button(onClick = { command("POST", "/api/sequencer/transport", "{\"running\":true}") }) { Text("Seq ▶") }
                            Button(onClick = { command("POST", "/api/sequencer/transport", "{\"running\":false}") }) { Text("Seq ■") } }
                        Row { Button(onClick = { command("POST", "/api/metronome/transport", "{\"running\":true}") }) { Text("Click ▶") }
                            Button(onClick = { command("POST", "/api/metronome/transport", "{\"running\":false}") }) { Text("Click ■") } }
                        Row { Button(onClick = { command("POST", "/api/looper/record/start") }) { Text("Gravar") }
                            Button(onClick = { command("POST", "/api/looper/record/stop") }) { Text("Parar gravação") } }
                        Row { Button(onClick = { command("POST", "/api/looper/play") }) { Text("Looper ▶") }
                            Button(onClick = { command("POST", "/api/looper/stop") }) { Text("Looper ■") } }
                    }
                    "Master" -> Column {
                        Text("Volume: ${master?.optString("volume", "--") ?: "--"}")
                        Row { for (volume in listOf(0, 25, 50, 75, 100)) {
                            TextButton(onClick = { command("POST", "/api/master", "{\"volume\":$volume}") }) { Text("$volume") }
                        } }
                        Button(onClick = { command("POST", "/api/master", "{\"muted\":true}") }) { Text("Mute") }
                        Button(onClick = { command("POST", "/api/master", "{\"muted\":false}") }) { Text("Unmute") }
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
