package org.diakopad.remote

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import org.json.JSONObject
import kotlin.math.roundToInt

private val Accent = Color(0xFFFF7A3D)
private val PadFilled = Color(0xFF33507A)
private val PadEmpty = Color(0xFF1F222B)
private val Sidebar = Color(0xFF151820)
private val Live = Color(0xFF3FBF6F)
private val Rec = Color(0xFFD24343)
private val Line = Color(0xFF2C313D)

/** Seções da navegação, agrupadas como na proposta Console. */
enum class Section(val label: String, val group: String) {
    PADS("Pads", "Tocar"),
    SCENES("Cenas", "Tocar"),
    SOUNDS("Sons", "Som"),
    KITS("Kits", "Som"),
    MIX("Mix", "Som"),
    EFFECTS("Efeitos", "Som"),
    TRANSPORT("Transporte", "Ritmo"),
    MASTER("Master", "Controle"),
}

data class StageStatus(
    val serverName: String,
    val status: String,
    val sceneName: String?,
    val bpm: String,
    val sequencerRunning: Boolean,
    val looperState: String,
    val padsFilled: Int,
    val padsTotal: Int,
    val activeScenes: Int,
)

/**
 * Casca do app, pensada para o Galaxy Tab A9+ (~1280×800dp):
 * - horizontal (>= 1000dp): lateral fixa + conteúdo + painel do pad sempre à direita;
 * - vertical (>= 700dp): lateral fixa + conteúdo; o painel abre numa folha inferior;
 * - telas menores: menu deslizante + folha inferior.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConsoleShell(
    section: Section,
    onSection: (Section) -> Unit,
    stage: StageStatus,
    onToggleSequencer: () -> Unit,
    onPanic: () -> Unit,
    onSwitchServer: () -> Unit,
    inspector: (@Composable ColumnScope.() -> Unit)?,
    inspectorOpen: Boolean,
    onCloseInspector: () -> Unit,
    content: @Composable (inspectorDocked: Boolean) -> Unit,
) {
    var confirmPanic by remember { mutableStateOf(false) }
    if (confirmPanic) {
        AlertDialog(
            onDismissRequest = { confirmPanic = false },
            title = { Text("PANIC") },
            text = { Text("Parar sequencer, metrônomo e looper e silenciar as notas agora?") },
            confirmButton = {
                Button(onClick = { onPanic(); confirmPanic = false }, colors = ButtonDefaults.buttonColors(containerColor = Rec)) {
                    Text("Parar tudo")
                }
            },
            dismissButton = { TextButton(onClick = { confirmPanic = false }) { Text("Cancelar") } },
        )
    }

    BoxWithConstraints(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        val sideInspector = maxWidth >= 1000.dp
        val wide = maxWidth >= 700.dp
        if (wide) {
            Row(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
                Surface(color = Sidebar, contentColor = Color(0xFFF2F2F2), modifier = Modifier.width(220.dp).fillMaxHeight()) {
                    NavigationList(section, stage, onSection, onSwitchServer)
                }
                Column(Modifier.weight(1f).fillMaxHeight()) {
                    StageBar(stage, menu = null, onToggleSequencer = onToggleSequencer, onPanic = { confirmPanic = true })
                    Box(Modifier.weight(1f).fillMaxWidth().padding(12.dp)) { content(sideInspector) }
                }
                if (inspector != null && sideInspector) {
                    Surface(color = Sidebar, contentColor = Color(0xFFF2F2F2), modifier = Modifier.width(320.dp).fillMaxHeight()) {
                        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp), content = inspector)
                    }
                }
            }
        } else {
            val drawer = rememberDrawerState(DrawerValue.Closed)
            val scope = rememberCoroutineScope()
            ModalNavigationDrawer(
                drawerState = drawer,
                drawerContent = {
                    ModalDrawerSheet(drawerContainerColor = Sidebar, drawerContentColor = Color(0xFFF2F2F2), modifier = Modifier.width(260.dp)) {
                        NavigationList(section, stage, { scope.launch { drawer.close() }; onSection(it) }, onSwitchServer)
                    }
                },
            ) {
                Column(Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding()) {
                    StageBar(stage, menu = { scope.launch { drawer.open() } }, onToggleSequencer = onToggleSequencer,
                        onPanic = { confirmPanic = true })
                    Box(Modifier.weight(1f).fillMaxWidth().padding(10.dp)) { content(false) }
                }
            }
        }
        if (inspector != null && inspectorOpen && !sideInspector) {
            ModalBottomSheet(onDismissRequest = onCloseInspector, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
                containerColor = Sidebar, contentColor = Color(0xFFF2F2F2)) {
                Column(Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 20.dp).padding(bottom = 24.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp), content = inspector)
            }
        }
    }
}

@Composable
private fun NavigationList(section: Section, stage: StageStatus, onSection: (Section) -> Unit, onSwitchServer: () -> Unit) {
    Column(Modifier.fillMaxHeight().verticalScroll(rememberScrollState()).padding(vertical = 14.dp, horizontal = 8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text("DiakoPad", fontSize = 22.sp, fontWeight = FontWeight.Bold, modifier = Modifier.padding(horizontal = 12.dp))
        Text(stage.serverName, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 12.dp))
        for ((group, items) in Section.entries.groupBy { it.group }) {
            Text(group.uppercase(), fontSize = 10.sp, letterSpacing = 1.4.sp, fontWeight = FontWeight.Bold,
                color = Color(0xFF6B7180), modifier = Modifier.padding(start = 12.dp, top = 16.dp, bottom = 4.dp))
            for (item in items) {
                val badge = when (item) {
                    Section.PADS -> "${stage.padsFilled}/${stage.padsTotal}" to null
                    Section.SCENES -> (if (stage.activeScenes > 0) "${stage.activeScenes}" else "") to null
                    Section.TRANSPORT -> when {
                        stage.looperState == "recording" || stage.looperState == "overdubbing" -> "●" to Rec
                        stage.sequencerRunning || stage.looperState == "playing" -> "▶" to Live
                        else -> "" to null
                    }
                    else -> "" to null
                }
                val selected = item == section
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp))
                        .background(if (selected) Color(0xFF242833) else Color.Transparent)
                        .clickable { onSection(item) }
                        .padding(horizontal = 12.dp, vertical = 11.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(Modifier.width(3.dp).height(18.dp).background(if (selected) Accent else Color.Transparent))
                    Spacer(Modifier.width(10.dp))
                    Text(item.label, modifier = Modifier.weight(1f), fontSize = 15.sp,
                        fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
                    Text(badge.first, fontSize = 12.sp, color = badge.second ?: MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Spacer(Modifier.height(18.dp))
        HorizontalDivider(color = Line)
        TextButton(onClick = onSwitchServer, modifier = Modifier.padding(top = 6.dp)) { Text("Trocar DiakoPad") }
    }
}

@Composable
private fun StageBar(stage: StageStatus, menu: (() -> Unit)?, onToggleSequencer: () -> Unit, onPanic: () -> Unit) {
    Surface(color = Color(0xFF171A22), contentColor = Color(0xFFF2F2F2)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (menu != null) TextButton(onClick = menu) { Text("☰", fontSize = 22.sp) }
            Column(Modifier.weight(1f)) {
                Text(stage.sceneName ?: "Sem cena", fontWeight = FontWeight.Bold, color = Accent, fontSize = 16.sp,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(stage.status, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Text("${stage.bpm} BPM", fontFamily = FontFamily.Monospace, fontSize = 13.sp)
            OutlinedButton(onClick = onToggleSequencer,
                border = BorderStroke(1.dp, if (stage.sequencerRunning) Accent else Line),
                colors = ButtonDefaults.outlinedButtonColors(containerColor = if (stage.sequencerRunning) Accent else Color.Transparent,
                    contentColor = if (stage.sequencerRunning) Color(0xFF1A0D05) else MaterialTheme.colorScheme.onSurface)) {
                Text(if (stage.sequencerRunning) "SEQ ■" else "SEQ ▶", fontSize = 12.sp)
            }
            Button(onClick = onPanic, colors = ButtonDefaults.buttonColors(containerColor = Color(0xFF7F2C2C), contentColor = Color.White)) {
                Text("PANIC", fontWeight = FontWeight.Bold, fontSize = 12.sp)
            }
        }
    }
}

/** Grade 4×4 na mesma ordem do SMC-PAD; toque = tocar e selecionar, segurar = abrir o painel. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun PadGrid(
    pads: List<JSONObject>,
    selected: Int,
    onTap: (Int) -> Unit,
    onLongPress: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ordered = PadModel.displayOrder(pads)
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        for (row in ordered.chunked(4)) {
            Row(Modifier.fillMaxWidth().weight(1f), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                for (pad in row) {
                    val number = pad?.optInt("pad_number") ?: 0
                    val filled = PadModel.hasSample(pad)
                    val shape = RoundedCornerShape(12.dp)
                    Box(
                        Modifier.weight(1f).fillMaxHeight().clip(shape)
                            .background(if (filled) PadFilled else PadEmpty)
                            .border(if (number == selected) 3.dp else 0.dp, if (number == selected) Accent else Color.Transparent, shape)
                            .combinedClickable(enabled = pad != null, onClick = { onTap(number) }, onLongClick = { onLongPress(number) })
                            .padding(10.dp),
                    ) {
                        Text("$number", fontSize = 11.sp, color = Color(0xFF8E9BB5), modifier = Modifier.align(Alignment.TopStart))
                        Text(PadModel.name(pad), fontSize = 18.sp, fontWeight = FontWeight.SemiBold, maxLines = 2,
                            overflow = TextOverflow.Ellipsis, color = if (filled) Color.White else Color(0xFF5D6372),
                            modifier = Modifier.align(Alignment.BottomStart))
                    }
                }
            }
        }
    }
}

/** Painel do pad: som, mix, envios e efeitos só dele. */
@Composable
fun ColumnScope.PadInspector(
    pad: JSONObject?,
    fx: JSONObject?,
    effects: List<String>,
    onMix: (String, Double) -> Unit,
    onSend: (String, Int) -> Unit,
    onChangeSound: () -> Unit,
    onPlay: () -> Unit,
    onOpenEffects: () -> Unit,
) {
    if (pad == null) {
        Text("Nenhum pad selecionado", fontWeight = FontWeight.SemiBold)
        Text("Toque num pad para ajustar som, volume, pan e envios aqui.", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 14.sp)
        return
    }
    val number = pad.optInt("pad_number")
    val filled = PadModel.hasSample(pad)
    Text("PAD $number · NOTA ${pad.optInt("midi_note")}", fontSize = 11.sp, letterSpacing = 1.sp,
        color = MaterialTheme.colorScheme.onSurfaceVariant, fontWeight = FontWeight.Bold)
    Text(PadModel.name(pad), fontSize = 24.sp, fontWeight = FontWeight.Bold)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = onChangeSound, modifier = Modifier.weight(1f)) { Text(if (filled) "Trocar som" else "Escolher som") }
        OutlinedButton(onClick = onPlay, enabled = filled, modifier = Modifier.weight(1f)) { Text("▶ Tocar") }
    }
    SectionTitle("Mix")
    InspectorSlider("Volume", pad.optDouble("volume_db", 0.0).toFloat(), -24f..12f, 71, number, { "${"%.1f".format(it)} dB" }) { onMix("volume_db", (it * 2).roundToInt() / 2.0) }
    InspectorSlider("Pan", pad.optDouble("pan", 0.0).toFloat(), -100f..100f, 0, number, { PadModel.panLabel(it.roundToInt()) }) { onMix("pan", it.roundToInt().toDouble()) }
    InspectorSlider("Tom", PadModel.tone(pad).toFloat(), 0f..100f, 0, number, { if (it >= 100f) "aberto" else "${it.roundToInt()}%" }) { onMix("tone", it.roundToInt().toDouble()) }
    InspectorSlider("Afinação", pad.optDouble("tune", 0.0).toFloat(), -12f..12f, 47, number, { PadModel.tuneLabel((it * 2).roundToInt() / 2.0) }) { onMix("tune", (it * 2).roundToInt() / 2.0) }
    if (fx != null && fx.optBoolean("available", true)) {
        SectionTitle("Envios")
        for ((bus, label) in PadModel.BUSES) {
            InspectorSlider(label, PadModel.sendLevel(fx, number, bus).toFloat(), 0f..100f, 19, number, { if (it < 1f) "—" else "${it.roundToInt()}%" }) {
                onSend(bus, ((it / 5).roundToInt() * 5).coerceIn(0, 100))
            }
        }
    }
    SectionTitle("Efeitos só deste pad")
    Text(if (effects.isEmpty()) "Nenhum" else effects.joinToString(" · "), fontSize = 14.sp)
    OutlinedButton(onClick = onOpenEffects, modifier = Modifier.fillMaxWidth()) { Text("Abrir efeitos") }
}

@Composable
private fun SectionTitle(text: String) {
    HorizontalDivider(color = Line, modifier = Modifier.padding(top = 4.dp))
    Text(text.uppercase(), fontSize = 11.sp, letterSpacing = 1.2.sp, fontWeight = FontWeight.Bold,
        color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
private fun InspectorSlider(
    label: String,
    current: Float,
    range: ClosedFloatingPointRange<Float>,
    steps: Int,
    padNumber: Int,
    format: (Float) -> String,
    commit: (Float) -> Unit,
) {
    var value by remember(padNumber, label, current) { mutableFloatStateOf(current.coerceIn(range.start, range.endInclusive)) }
    Column {
        Row(Modifier.fillMaxWidth()) {
            Text(label, modifier = Modifier.weight(1f), fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(format(value), fontSize = 13.sp, fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace)
        }
        Slider(value = value, onValueChange = { value = it }, valueRange = range, steps = steps,
            onValueChangeFinished = { commit(value) })
    }
}

/** Barra do pad selecionado sob a grade (celular): abre o painel. */
@Composable
fun SelectedPadBar(pad: JSONObject?, onOpen: () -> Unit) {
    Surface(color = Color(0xFF1C1F28), contentColor = Color(0xFFF2F2F2), shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(if (pad == null) "Toque num pad" else "Pad ${pad.optInt("pad_number")}", fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(PadModel.name(pad).takeIf { pad != null } ?: "para tocar e ajustar", fontWeight = FontWeight.SemiBold)
            }
            Button(onClick = onOpen, enabled = pad != null) { Text("Ajustar") }
        }
    }
}
