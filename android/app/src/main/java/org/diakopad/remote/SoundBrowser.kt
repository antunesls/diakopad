package org.diakopad.remote

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject

/** Navegação por pastas da biblioteca: toque numa pasta para entrar, num som para atribuí-lo ao pad. */
@Composable
fun SoundBrowser(sounds: List<JSONObject>, onPick: (JSONObject) -> Unit, modifier: Modifier = Modifier) {
    var folder by rememberSaveable { mutableStateOf("") }
    // Se a biblioteca mudar e a pasta atual sumir, volta ao nível mais próximo que ainda existe.
    val current = generateSequence(folder) { if (it.isEmpty()) null else SoundTree.parent(it) }
        .first { it.isEmpty() || sounds.any { s -> s.optString("folder").let { f -> f == it || f.startsWith("$it/") } } }
    val listing = SoundTree.list(sounds, current)

    Column(modifier) {
        Row(Modifier.horizontalScroll(rememberScrollState()), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { folder = "" }) {
                Text("Biblioteca", fontWeight = if (current.isEmpty()) FontWeight.Bold else FontWeight.Normal)
            }
            for ((name, path) in SoundTree.breadcrumb(current)) {
                Text("›", color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { folder = path }) {
                    Text(name, fontWeight = if (path == current) FontWeight.Bold else FontWeight.Normal)
                }
            }
        }
        HorizontalDivider()
        LazyColumn(Modifier.weight(1f)) {
            if (current.isNotEmpty()) {
                item(key = "up") {
                    Entry("📁  ..", null) { folder = SoundTree.parent(current) }
                }
            }
            items(listing.folders, key = { "d:" + it.name }) { f ->
                Entry("📁  ${f.name}", "${f.soundCount}") { folder = SoundTree.child(current, f.name) }
            }
            items(listing.sounds, key = { "s:" + it.optInt("id") }) { sound ->
                Entry(sound.optString("display_name", "Som"), null, primary = false) { onPick(sound) }
            }
            if (listing.folders.isEmpty() && listing.sounds.isEmpty()) {
                item { Text("Nenhum som aqui.", color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(16.dp)) }
            }
        }
    }
}

@Composable
private fun Entry(label: String, badge: String?, primary: Boolean = true, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 15.sp,
            fontWeight = if (primary) FontWeight.SemiBold else FontWeight.Normal,
            color = if (primary) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface)
        if (badge != null) Text(badge, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}
