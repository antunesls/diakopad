package org.diakopad.remote

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONObject

@Composable
internal fun TransportScreen(
    tempo: String, sequencer: JSONObject?, patterns: List<JSONObject>, looper: JSONObject?,
    metronome: JSONObject?, styles: List<JSONObject>, signatures: List<JSONObject>,
    command: (String, String, String) -> Unit,
) {
    var bpm by remember { mutableStateOf("") }
    var patternName by remember { mutableStateOf("") }
    var confirmClear by remember { mutableStateOf<String?>(null) }
    val steps = sequencer?.optJSONArray("steps")?.let { entries ->
        (0 until entries.length()).map { entries.getJSONObject(it) }
    }.orEmpty()
    val tracks = looper?.optJSONArray("tracks")
    LazyColumn {
        item {
            Text("BPM $tempo")
            Row {
                OutlinedTextField(bpm, { bpm = it }, label = { Text("BPM") }, modifier = Modifier.weight(1f))
                Button(onClick = { bpm.toDoubleOrNull()?.let { command("POST", "/api/tempo", JSONObject().put("bpm", it).toString()) } }) {
                    Text("Definir")
                }
            }
            Text("Sequencer: ${if (sequencer?.optBoolean("running") == true) "tocando" else "parado"}")
            Row {
                Button(onClick = { command("POST", "/api/sequencer/transport", "{\"running\":true}") }) { Text("Seq ▶") }
                Button(onClick = { command("POST", "/api/sequencer/transport", "{\"running\":false}") }) { Text("Seq ■") }
            }
            Row {
                OutlinedTextField(patternName, { patternName = it }, label = { Text("Nome do padrão") }, modifier = Modifier.weight(1f))
                Button(onClick = { if (patternName.isNotBlank()) command("POST", "/api/patterns", JSONObject().put("name", patternName).toString()) }) {
                    Text("Salvar")
                }
            }
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                patterns.forEach { pattern ->
                    TextButton(onClick = { command("POST", "/api/patterns/${pattern.optInt("id")}/load", "{}") }) {
                        Text(pattern.optString("name"))
                    }
                }
            }
            Text("Passos (toque para alternar)")
        }
        items((1..16).toList()) { pad ->
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                Text("$pad", Modifier.padding(8.dp))
                for (step in 0..15) {
                    val active = steps.any { it.optInt("pad_number") == pad && it.optInt("step_index") == step && it.optBoolean("active") }
                    TextButton(onClick = {
                        val cmd = StageCommands.step(pad, step, !active)
                        command(cmd.method, cmd.path, cmd.json.orEmpty())
                    }) { Text(if (active) "●" else "○") }
                }
            }
        }
        item {
            Button(onClick = { confirmClear = "sequencer" }) { Text("Limpar sequência") }
            Text("Metrônomo: ${if (metronome?.optBoolean("running") == true) "tocando" else "parado"}")
            Row {
                Button(onClick = { command("POST", "/api/metronome/transport", "{\"running\":true}") }) { Text("Click ▶") }
                Button(onClick = { command("POST", "/api/metronome/transport", "{\"running\":false}") }) { Text("Click ■") }
            }
            Text("Estilo")
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                styles.forEach { style ->
                    TextButton(onClick = { command("POST", "/api/metronome/style",
                        JSONObject().put("style", style.optString("style")).toString()) }) {
                        Text(style.optString("label"))
                    }
                }
            }
            Text("Compasso")
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                signatures.forEach { signature ->
                    TextButton(onClick = { command("POST", "/api/metronome/signature",
                        JSONObject().put("signature", signature.optString("signature")).toString()) }) {
                        Text(signature.optString("label"))
                    }
                }
            }
            Text("Looper: ${looper?.optString("state", "parado") ?: "parado"}")
            Row(Modifier.horizontalScroll(rememberScrollState())) {
                for ((label, path) in listOf("Gravar" to "record/start", "Parar gravação" to "record/stop",
                    "Overdub" to "overdub/start", "Parar overdub" to "overdub/stop", "Tocar" to "play", "Parar" to "stop")) {
                    TextButton(onClick = { command("POST", "/api/looper/$path", "{}") }) { Text(label) }
                }
            }
            Text("Trilhas")
        }
        items(4) { index ->
            val track = tracks?.optJSONObject(index)
            val volume = track?.optDouble("volume", 100.0)?.toFloat() ?: 100f
            var slider by remember(index, volume) { mutableFloatStateOf(volume) }
            Column(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
                Text("${index + 1} · ${track?.optString("state") ?: "parada"} · ${track?.optInt("event_count") ?: 0} notas")
                Row(Modifier.horizontalScroll(rememberScrollState())) {
                    TextButton(onClick = { command("POST", "/api/looper/tracks/$index/select", "{}") }) {
                        Text(if (looper?.optInt("selected_track") == index) "● Selecionada" else "Selecionar")
                    }
                    TextButton(onClick = { command("POST", "/api/looper/tracks/$index/mute", "{}") }) {
                        Text(if (track?.optBoolean("muted") == true) "Desmutar" else "Mute")
                    }
                    TextButton(onClick = { confirmClear = "track/$index" }) { Text("Limpar") }
                }
                Text("Volume ${slider.toInt()}%")
                Slider(value = slider, onValueChange = { slider = it }, valueRange = 0f..100f,
                    onValueChangeFinished = {
                        val cmd = StageCommands.trackVolume(index, slider.toInt())
                        command(cmd.method, cmd.path, cmd.json.orEmpty())
                    })
            }
        }
        item {
            confirmClear?.let { item ->
                Text("Apagar ${if (item == "sequencer") "a sequência" else "esta trilha"}?")
                Row {
                    Button(onClick = {
                        val path = if (item == "sequencer") "/api/sequencer/clear"
                            else "/api/looper/tracks/${item.substringAfter('/')}/clear"
                        command("POST", path, "{}")
                        confirmClear = null
                    }) { Text("Confirmar") }
                    TextButton(onClick = { confirmClear = null }) { Text("Cancelar") }
                }
            }
        }
    }
}
