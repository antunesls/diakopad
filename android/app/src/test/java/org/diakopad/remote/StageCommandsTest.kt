package org.diakopad.remote

import org.junit.Assert.assertEquals
import org.junit.Test

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

    @Test fun assignsSoundAndUpdatesPadMixWithJsonBody() {
        assertEquals("/api/pads/2/assign", StageCommands.assign(2, 19).path)
        assertEquals("{\"sample_id\":19}", StageCommands.assign(2, 19).json)
        assertEquals("/api/pads/2/mix", StageCommands.mix(2, "pan", 0.5).path)
        assertEquals("{\"pan\":0.5}", StageCommands.mix(2, "pan", 0.5).json)
    }
}
