package org.diakopad.remote

import org.json.JSONObject

/**
 * Visão em diretórios da biblioteca de sons. O servidor guarda em cada som um
 * `folder` relativo separado por "/" ("" = raiz); a árvore é derivada da lista
 * já carregada, sem nova chamada à API.
 */
object SoundTree {
    data class Folder(val name: String, val soundCount: Int)
    data class Listing(val folders: List<Folder>, val sounds: List<JSONObject>)

    /** Um nível: subpastas imediatas de [folder] (com total recursivo) e os sons que estão direto nela. */
    fun list(sounds: List<JSONObject>, folder: String): Listing {
        val prefix = if (folder.isEmpty()) "" else "$folder/"
        val counts = mutableMapOf<String, Int>()
        val here = mutableListOf<JSONObject>()
        for (sound in sounds) {
            val f = sound.optString("folder", "")
            if (f == folder) {
                here += sound
            } else if (f.startsWith(prefix)) {
                val child = f.substring(prefix.length).substringBefore('/')
                counts[child] = (counts[child] ?: 0) + 1
            }
        }
        return Listing(
            folders = counts.map { Folder(it.key, it.value) }.sortedWith(compareBy({ it.name.lowercase() }, { it.name })),
            sounds = here.sortedBy { it.optString("display_name").lowercase() },
        )
    }

    fun child(folder: String, name: String): String = if (folder.isEmpty()) name else "$folder/$name"

    /** Pasta pai ("" quando já está na raiz). */
    fun parent(folder: String): String = folder.substringBeforeLast('/', "")

    /** Cada nível do caminho com seu caminho completo, para a barra de navegação. */
    fun breadcrumb(folder: String): List<Pair<String, String>> {
        if (folder.isEmpty()) return emptyList()
        val parts = folder.split('/')
        return parts.indices.map { i -> parts[i] to parts.take(i + 1).joinToString("/") }
    }
}
