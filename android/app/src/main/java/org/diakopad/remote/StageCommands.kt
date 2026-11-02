package org.diakopad.remote

import org.json.JSONArray
import org.json.JSONObject

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

    fun loadKit(kit: Int): Command {
        require(kit > 0)
        return Command("POST", "/api/kits/$kit/load", "{\"clear_other_pads\":false}")
    }

    fun send(pad: Int, bus: String, level: Int): Command {
        require(pad in 1..16 && bus in setOf("hall", "plate", "delay") && level in 0..100)
        return Command("POST", "/api/pads/$pad/sends/$bus", "{\"level\":$level}")
    }

    fun createKit(name: String, category: String, samples: Map<Int, Int>): Command {
        require(name.isNotBlank() && samples.isNotEmpty())
        require(samples.all { (pad, sample) -> pad in 1..16 && sample > 0 })
        val pads = JSONArray()
        samples.toSortedMap().forEach { (pad, sample) ->
            pads.put(JSONObject().put("pad_number", pad).put("sample_id", sample))
        }
        val body = JSONObject().put("name", name.trim()).put("category", category.trim()).put("pads", pads)
        return Command("POST", "/api/kits", body.toString())
    }

    fun step(pad: Int, index: Int, active: Boolean): Command {
        require(pad in 1..16 && index in 0..15)
        return Command("POST", "/api/sequencer/steps/$pad/$index", "{\"active\":$active}")
    }

    fun trackVolume(index: Int, volume: Int): Command {
        require(index in 0..3 && volume in 0..100)
        return Command("POST", "/api/looper/tracks/$index/volume", "{\"volume\":$volume}")
    }

    fun master(field: String, value: Double): Command {
        require(field in setOf("volume", "limiter_threshold_db", "compressor_threshold_db", "compressor_ratio", "compressor_makeup_db") && value.isFinite())
        return Command("POST", "/api/master", "{\"$field\":$value}")
    }

    fun busParam(bus: String, symbol: String, value: Double): Command {
        require(bus in setOf("hall", "plate", "delay") && symbol.isNotBlank() && value.isFinite())
        val json = JSONObject().put("symbol", symbol).put("value", value).toString()
        return Command("POST", "/api/fx-buses/$bus/param", json)
    }
}
