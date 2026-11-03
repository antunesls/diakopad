package org.diakopad.remote

import org.json.JSONObject
import kotlin.math.ln
import kotlin.math.roundToInt

/** Cálculos da tela de pads (mesmas regras da interface web, sem Compose). */
object PadModel {
    val BUSES = listOf("hall" to "Hall", "plate" to "Plate", "delay" to "Delay")

    /**
     * O SMC-PAD numera de baixo para cima (pad 1 embaixo à esquerda); a grade
     * na tela é desenhada de cima para baixo, então as linhas são invertidas
     * para bater com o que o músico vê no controlador.
     */
    fun displayOrder(pads: List<JSONObject>): List<JSONObject?> {
        val byNumber = pads.associateBy { it.optInt("pad_number") }
        return (3 downTo 0).flatMap { row -> (0 until 4).map { col -> byNumber[row * 4 + col + 1] } }
    }

    fun name(pad: JSONObject?): String {
        if (pad == null || !pad.optBoolean("has_sample", !pad.isNull("sample_id"))) return "Vazio"
        val display = pad.optString("display_name")
        return if (display.isBlank() || display == "null") "Som ${pad.optInt("sample_id")}" else display
    }

    fun hasSample(pad: JSONObject?): Boolean = pad != null && pad.optBoolean("has_sample", !pad.isNull("sample_id"))

    /** 0..100, 100 = filtro aberto (sem corte). */
    fun toneFromCutoff(cutoffHz: Double?): Int {
        if (cutoffHz == null || cutoffHz.isNaN() || cutoffHz <= 0) return 100
        val frac = ln(cutoffHz / 200.0) / ln(20000.0 / 200.0)
        return (frac * 100).roundToInt().coerceIn(0, 100)
    }

    fun tone(pad: JSONObject): Int =
        toneFromCutoff(if (pad.isNull("cutoff_hz")) null else pad.optDouble("cutoff_hz"))

    fun panLabel(pan: Int): String = when {
        pan == 0 -> "centro"
        pan < 0 -> "E ${-pan}"
        else -> "D $pan"
    }

    fun tuneLabel(tune: Double): String {
        if (tune == 0.0) return "original"
        val text = if (tune % 1.0 == 0.0) tune.toInt().toString() else "%.1f".format(tune)
        return "${if (tune > 0) "+" else ""}$text st"
    }

    fun sendLevel(fx: JSONObject?, pad: Int, bus: String): Int {
        val sends = fx?.optJSONArray("sends") ?: return 0
        for (i in 0 until sends.length()) {
            val send = sends.getJSONObject(i)
            if (send.optInt("pad_number") == pad && send.optString("bus") == bus) return send.optDouble("level").roundToInt()
        }
        return 0
    }

    fun padEffects(effects: List<JSONObject>, catalog: List<JSONObject>, pad: Int): List<String> =
        effects.filter { it.optInt("pad_number") == pad && !it.isNull("plugin_id") && it.optString("plugin_id").isNotBlank() }
            .sortedBy { it.optInt("slot_index") }
            .mapNotNull { effect -> catalog.firstOrNull { it.optString("plugin_id") == effect.optString("plugin_id") }?.optString("label") }
}
