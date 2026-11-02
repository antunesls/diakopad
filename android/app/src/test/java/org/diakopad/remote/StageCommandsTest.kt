package org.diakopad.remote

import org.junit.Assert.assertEquals
import org.junit.Test
import org.json.JSONObject

class StageCommandsTest {
    @Test fun triggersPadAndLoadsSceneUsingBackendRoutes() {
        assertEquals("/api/pads/4/trigger", StageCommands.trigger(4).path)
        assertEquals("POST", StageCommands.trigger(4).method)
        assertEquals("/api/scenes/7/load", StageCommands.loadScene(7).path)
    }

    @Test fun preventsDestructiveOutOfRangePad() {
        try {
            StageCommands.trigger(17)
            throw AssertionError("Expected invalid pad to fail")
        } catch (_: IllegalArgumentException) { /* expected */ }
    }

    @Test fun updatesPadMixWithJsonBody() {
        assertEquals("/api/pads/2/mix", StageCommands.mix(2, "pan", 0.5).path)
        assertEquals("{\"pan\":0.5}", StageCommands.mix(2, "pan", 0.5).json)
    }

    @Test fun loadsExistingKitAndAdjustsLiveFxSend() {
        assertEquals("{\"clear_other_pads\":false}", StageCommands.loadKit(7).json)
        assertEquals("/api/kits/7/load", StageCommands.loadKit(7).path)
        assertEquals("/api/pads/4/sends/hall", StageCommands.send(4, "hall", 35).path)
        assertEquals("{\"level\":35}", StageCommands.send(4, "hall", 35).json)
    }

    @Test fun rejectsInvalidLiveParameters() {
        for (block in listOf<() -> Command>({ StageCommands.send(1, "unknown", 30) },
            { StageCommands.send(1, "hall", 101) }, { StageCommands.loadKit(0) })) {
            try { block(); throw AssertionError("Expected invalid command to fail") }
            catch (_: IllegalArgumentException) { /* expected */ }
        }
    }

    @Test fun createsKitFromExistingSamplesWithoutUploadingFiles() {
        val command = StageCommands.createKit("Domingo", "Culto", mapOf(1 to 12, 4 to 8))
        assertEquals("/api/kits", command.path)
        val body = JSONObject(command.json!!)
        assertEquals("Domingo", body.getString("name"))
        assertEquals(2, body.getJSONArray("pads").length())
        assertEquals(12, body.getJSONArray("pads").getJSONObject(0).getInt("sample_id"))
    }

    @Test fun editsSequencerAndLooperTracks() {
        assertEquals("/api/sequencer/steps/3/15", StageCommands.step(3, 15, true).path)
        assertEquals("{\"active\":true}", StageCommands.step(3, 15, true).json)
        assertEquals("/api/looper/tracks/0/volume", StageCommands.trackVolume(0, 42).path)
        assertEquals("{\"volume\":42}", StageCommands.trackVolume(0, 42).json)
    }

    @Test fun sendsValidatedMasterControls() {
        assertEquals("{\"limiter_threshold_db\":-3.0}", StageCommands.master("limiter_threshold_db", -3.0).json)
        try { StageCommands.master("unknown", 2.0); throw AssertionError("Unexpected master field accepted") }
        catch (_: IllegalArgumentException) { /* expected */ }
    }

    @Test fun changesEffectOptionThroughSameEndpointAsSlider() {
        assertEquals("/api/fx-buses/delay/param", StageCommands.busParam("delay", "division", 0.5).path)
        assertEquals("{\"symbol\":\"division\",\"value\":0.5}", StageCommands.busParam("delay", "division", 0.5).json)
    }
}
