package org.diakopad.remote

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class SoundTreeTest {
    private fun sound(id: Int, name: String, folder: String) =
        JSONObject().put("id", id).put("display_name", name).put("folder", folder)

    private val library = listOf(
        sound(1, "zumbido", ""),
        sound(2, "Kick A", "Drums/Kicks"),
        sound(3, "kick B", "Drums/Kicks"),
        sound(4, "Snare", "Drums"),
        sound(5, "Pad", "Synth"),
        sound(6, "Hat", "drums/Hats"),
    )

    @Test fun rootListsTopFoldersWithRecursiveCountsAndLooseSounds() {
        val root = SoundTree.list(library, "")
        assertEquals(listOf("Drums" to 3, "drums" to 1, "Synth" to 1), root.folders.map { it.name to it.soundCount })
        assertEquals(listOf("zumbido"), root.sounds.map { it.optString("display_name") })
    }

    @Test fun insideAFolderShowsSubfoldersAndOnlyItsOwnSounds() {
        val drums = SoundTree.list(library, "Drums")
        assertEquals(listOf("Kicks"), drums.folders.map { it.name })
        assertEquals(2, drums.folders.single().soundCount)
        assertEquals(listOf("Snare"), drums.sounds.map { it.optString("display_name") })
    }

    @Test fun soundsAreSortedIgnoringCase() {
        val kicks = SoundTree.list(library, "Drums/Kicks")
        assertEquals(listOf("Kick A", "kick B"), kicks.sounds.map { it.optString("display_name") })
    }

    @Test fun folderNamePrefixIsNotConfusedWithSimilarName() {
        val tricky = listOf(sound(1, "a", "Drum"), sound(2, "b", "Drums"))
        assertEquals(listOf("b"), SoundTree.list(tricky, "Drums").sounds.map { it.optString("display_name") })
    }

    @Test fun pathHelpers() {
        assertEquals("A/B", SoundTree.child("A", "B"))
        assertEquals("B", SoundTree.child("", "B"))
        assertEquals("A", SoundTree.parent("A/B"))
        assertEquals("", SoundTree.parent("A"))
        assertEquals(listOf("A" to "A", "B" to "A/B"), SoundTree.breadcrumb("A/B"))
        assertEquals(emptyList<Pair<String, String>>(), SoundTree.breadcrumb(""))
    }
}
