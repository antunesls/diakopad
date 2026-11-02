package org.diakopad.remote

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
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
import org.json.JSONArray

@Composable
internal fun KitScreen(
    kits: List<JSONObject>, name: String, rename: (String) -> Unit,
    category: String, recategorize: (String) -> Unit,
    selection: Map<Int, Int>, select: (Map<Int, Int>) -> Unit,
    sounds: List<JSONObject>, selectedPad: Int, setPad: (Int) -> Unit,
    load: (Int) -> Unit, requestDelete: (Int) -> Unit, create: () -> Unit,
    pendingDelete: Int?, cancelDelete: () -> Unit, confirmDelete: (Int) -> Unit,
) {
    var search by remember { mutableStateOf("") }
    Column {
        Text("Carregar kit existente")
        LazyColumn(Modifier.heightIn(max = 180.dp)) { items(kits) { kit ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton(onClick = { load(kit.optInt("id")) }) {
                    Text("${kit.optString("category")} · ${kit.optString("name")}")
                }
                TextButton(onClick = { requestDelete(kit.optInt("id")) }) { Text("Excluir") }
            }
        } }
        pendingDelete?.let { id ->
            Text("Excluir este kit definitivamente?")
            Row {
                Button(onClick = { confirmDelete(id) }) { Text("Excluir kit") }
                TextButton(onClick = cancelDelete) { Text("Cancelar") }
            }
        }
        Text("Montar kit com sons existentes (${selection.size} pads)")
        OutlinedTextField(name, rename, label = { Text("Nome do kit") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(category, recategorize, label = { Text("Categoria") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = create, enabled = name.isNotBlank() && selection.isNotEmpty()) { Text("Salvar kit") }
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            for (pad in 1..16) TextButton(onClick = { setPad(pad) }) {
                Text(if (selectedPad == pad) "● $pad" else "$pad")
            }
        }
        Text("Pad $selectedPad: ${sounds.firstOrNull { it.optInt("id") == selection[selectedPad] }?.optString("display_name") ?: "sem som"}")
        OutlinedTextField(search, { search = it }, label = { Text("Buscar som já instalado") }, modifier = Modifier.fillMaxWidth())
        LazyColumn(Modifier.weight(1f)) {
            items(sounds.filter { it.optString("display_name").contains(search, ignoreCase = true) }) { sound ->
                TextButton(onClick = { select(selection + (selectedPad to sound.optInt("id"))) }) {
                    Text(sound.optString("display_name"))
                }
            }
        }
    }
}

@Composable
internal fun EffectsScreen(
    pad: Int, selectPad: (Int) -> Unit, fx: JSONObject?,
    effects: List<JSONObject>, catalog: List<JSONObject>,
    send: (Int, String, Int) -> Unit, command: (String, String, String) -> Unit,
) {
    Column {
        Text("Efeitos do pad $pad")
        Row(Modifier.horizontalScroll(rememberScrollState())) {
            for (number in 1..16) TextButton(onClick = { selectPad(number) }) {
                Text(if (pad == number) "● $number" else "$number")
            }
        }
        LazyColumn(Modifier.weight(1f)) {
            item {
                for (bus in listOf("hall", "plate", "delay")) {
                    val current = fx?.optJSONArray("sends")?.let { sends ->
                        (0 until sends.length()).map { sends.getJSONObject(it) }.firstOrNull {
                            it.optInt("pad_number") == pad && it.optString("bus") == bus
                        }?.optDouble("level")?.toFloat()
                    } ?: 0f
                    LiveSlider(bus, current, 0f..100f) { send(pad, bus, it.toInt()) }
                    val busParams = fx?.optJSONObject("params")?.optJSONObject(bus)
                    val busMeta = fx?.optJSONArray("catalog")?.let { list ->
                        (0 until list.length()).map { list.getJSONObject(it) }.firstOrNull { it.optString("bus") == bus }
                    }
                    busMeta?.optJSONArray("params")?.let { params ->
                        for (index in 0 until params.length()) {
                            val param = params.getJSONObject(index)
                            val symbol = param.optString("symbol")
                            val low = param.optDouble("min").toFloat()
                            val high = param.optDouble("max").toFloat()
                            val options = param.optJSONArray("options")
                            val value = busParams?.optDouble(symbol, param.optDouble("default"))?.toFloat() ?: low
                            if (options != null) {
                                OptionPicker(param.optString("label"), options, value) { next ->
                                    val cmd = StageCommands.busParam(bus, symbol, next.toDouble())
                                    command(cmd.method, cmd.path, cmd.json.orEmpty())
                                }
                            } else if (low < high) LiveSlider(param.optString("label"), value, low..high) { next ->
                                val cmd = StageCommands.busParam(bus, symbol, next.toDouble())
                                command(cmd.method, cmd.path, cmd.json.orEmpty())
                            }
                        }
                    }
                }
            }
            items(listOf(1, 2, 3)) { slot ->
                val selected = effects.firstOrNull { it.optInt("pad_number") == pad && it.optInt("slot_index") == slot }
                val plugin = selected?.optString("plugin_id", "") ?: ""
                var expanded by remember(pad, slot) { mutableStateOf(false) }
                Text("Efeito próprio $slot: ${catalog.firstOrNull { it.optString("plugin_id") == plugin }?.optString("label") ?: plugin.ifEmpty { "Nenhum" }}")
                Button(onClick = { expanded = true }) { Text("Escolher efeito") }
                DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                    DropdownMenuItem(text = { Text("Nenhum") }, onClick = {
                        expanded = false
                        command("POST", "/api/pads/$pad/effects/$slot", "{\"plugin_id\":null}")
                    })
                    catalog.forEach { item ->
                        DropdownMenuItem(text = { Text(item.optString("label", item.optString("plugin_id"))) }, onClick = {
                            expanded = false
                            command("POST", "/api/pads/$pad/effects/$slot",
                                JSONObject().put("plugin_id", item.optString("plugin_id")).toString())
                        })
                    }
                }
                val descriptor = catalog.firstOrNull { it.optString("plugin_id") == plugin }
                val parameters = descriptor?.optJSONArray("params")
                if (parameters != null) for (index in 0 until parameters.length()) {
                    val param = parameters.getJSONObject(index)
                    val symbol = param.optString("symbol")
                    val low = param.optDouble("min").toFloat()
                    val high = param.optDouble("max").toFloat()
                    val options = param.optJSONArray("options")
                    val value = selected?.optJSONObject("params")?.optDouble(symbol, param.optDouble("default"))?.toFloat() ?: low
                    val update: (Float) -> Unit = { next ->
                        command("POST", "/api/pads/$pad/effects/$slot/param",
                            JSONObject().put("symbol", symbol).put("value", next).toString())
                    }
                    if (options != null) OptionPicker(param.optString("label"), options, value, update)
                    else if (low < high) LiveSlider(param.optString("label"), value, low..high, update)
                }
            }
        }
    }
}

@Composable
private fun OptionPicker(label: String, options: JSONArray, current: Float, submit: (Float) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val values = (0 until options.length()).map { options.getJSONObject(it) }
    val selected = values.firstOrNull { kotlin.math.abs(it.optDouble("value") - current) < 0.001 }
    Text("$label: ${selected?.optString("label") ?: current}")
    Button(onClick = { expanded = true }) { Text("Alterar $label") }
    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
        values.forEach { option ->
            DropdownMenuItem(text = { Text(option.optString("label")) }, onClick = {
                expanded = false
                submit(option.optDouble("value").toFloat())
            })
        }
    }
}

@Composable
private fun LiveSlider(label: String, current: Float, range: ClosedFloatingPointRange<Float>, submit: (Float) -> Unit) {
    var value by remember(label, current) { mutableFloatStateOf(current.coerceIn(range.start, range.endInclusive)) }
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text("$label: ${"%.1f".format(value)}")
        Slider(value = value, onValueChange = { value = it }, valueRange = range,
            onValueChangeFinished = { submit(value) })
    }
}
