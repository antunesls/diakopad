package org.diakopad.remote

data class Command(val method: String, val path: String, val json: String? = null)

object StageCommands {
    fun trigger(pad: Int): Command {
        require(pad in 1..16)
        return Command("POST", "/api/pads/$pad/trigger")
    }

    fun loadScene(scene: Int): Command {
        require(scene > 0)
        return Command("POST", "/api/scenes/$scene/load")
    }

    fun assign(pad: Int, sample: Int?): Command {
        require(pad in 1..16 && (sample == null || sample > 0))
        return Command("POST", "/api/pads/$pad/assign", "{\"sample_id\":${sample ?: "null"}}")
    }

    fun mix(pad: Int, field: String, value: Double): Command {
        require(pad in 1..16 && field in setOf("volume_db", "pan", "tone", "tune") && value.isFinite())
        return Command("POST", "/api/pads/$pad/mix", "{\"$field\":$value}")
    }
}
