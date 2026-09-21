"""Regressoes estruturais da interface de mixagem."""

from pathlib import Path
import unittest


FRONTEND = Path(__file__).resolve().parents[1]


class MixCardsTest(unittest.TestCase):
    def test_mixagem_usa_cards_recolhiveis_com_resumo_de_efeitos(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        css = (FRONTEND / "style.css").read_text(encoding="utf-8")

        self.assertIn('li.className = "mix-card"', app)
        self.assertIn('data-role="mix-card-toggle"', app)
        self.assertIn('class="mix-card-summary"', app)
        self.assertIn('effect-summary', app)
        self.assertIn('.mix-card.open .mix-card-body', css)
        self.assertIn('.mix-card.open {\n  grid-column: 1 / -1;', css)
        self.assertIn('.mix-card-grid', css)
        self.assertIn('grid-template-columns: repeat(4, minmax(0, 1fr));', css)
        self.assertNotIn('.mix-card-grid { grid-template-columns: 1fr; }', css)
        self.assertIn('@media (max-width: 700px)', css)

    def test_metronomo_carrega_e_persiste_assinatura_de_compasso(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/api/metronome/time-signatures")', app)
        self.assertIn('el.metronomeSignatureSelect.addEventListener("change"', app)
        self.assertIn('fetch("/api/metronome/signature"', app)
        self.assertIn('signature: msg.signature', app)

    def test_metronomo_usa_digitos_bpm_ampliados(self):
        css = (FRONTEND / "style.css").read_text(encoding="utf-8")

        self.assertIn("font-size: clamp(64px, 14vw, 96px);", css)
        self.assertIn("width: 180px;", css)

    def test_indicador_midi_exibe_a_oitava_explicitamente(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("· Oitava ${Math.floor(note / 12) - 2}", app)

    def test_biblioteca_aceita_selecao_e_arrasto_de_pastas(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="folder-input"', html)
        self.assertIn("webkitdirectory", html)
        self.assertIn("webkitGetAsEntry", app)
        self.assertIn("webkitRelativePath", app)

    def test_biblioteca_permite_selecao_e_exclusao_em_lote_de_sons_livres(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="tab-sounds" class="tab" type="button">Biblioteca</button>', html)
        self.assertIn('id="select-all-sounds"', html)
        self.assertIn('id="delete-selected-sounds"', html)
        self.assertIn("selectedSoundIds", app)
        self.assertIn("deleteSelectedSounds", app)
        self.assertIn('window.confirm(`Excluir ${selectedIds.length}', app)

    def test_biblioteca_permite_selecionar_pasta_com_seus_sons_removiveis(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("selectableSoundIdsInFolder", app)
        self.assertIn('class="sound-select folder-select"', app)
        self.assertIn("toggleSoundSelection", app)

    def test_modal_de_atribuicao_navega_por_pastas(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="modal-breadcrumb"', html)
        self.assertIn("state.modalSoundPath", app)
        self.assertIn("state.modalSoundView", app)
        self.assertIn("function loadModalSoundBrowser", app)
        self.assertIn("function navigateModalToFolder", app)
        self.assertIn("function renderModalBreadcrumb", app)
        # A busca ativa ignora a pasta atual e também casa pelo nome da pasta.
        self.assertIn("s.folder.toLowerCase().includes(query)", app)

    def test_upload_ignora_arquivos_que_nao_sao_audio(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("SUPPORTED_AUDIO_EXTENSIONS", app)
        self.assertIn("filter(isSupportedAudioFile)", app)

    def test_upload_de_muitos_arquivos_usa_concorrencia_limitada(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        # Regressão: subir uma pasta grande (ou várias arrastadas juntas) com
        # Promise.all sem limite disparava todos os XHRs de uma vez e uma
        # fatia falhava com "erro de rede" sob carga.
        self.assertIn("const UPLOAD_CONCURRENCY = 4;", app)
        self.assertIn("Math.min(UPLOAD_CONCURRENCY, entries.length)", app)
        self.assertNotIn(
            "await Promise.all(\n      entries.map(({ file, folder }, index) => uploadOne(file, folder,",
            app,
        )

    def test_upload_exibe_progresso_percentual(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        css = (FRONTEND / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="upload-progress"', html)
        self.assertIn('id="upload-progress-percent"', html)
        self.assertIn("XMLHttpRequest", app)
        self.assertIn('request.upload.addEventListener("progress"', app)
        self.assertIn(".upload-progress", css)

    def test_config_permite_reiniciar_diakopad_e_audio_com_confirmacao(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="full-restart-btn"', html)
        self.assertIn("Reiniciar DiakoPad e áudio", html)
        self.assertIn('window.confirm("Reiniciar o DiakoPad e o áudio do laptop?")', app)
        self.assertIn('fetch("/api/system/restart", { method: "POST" })', app)

    def test_config_permite_limpar_todos_os_pads_com_confirmacao(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="clear-all-pads-btn"', html)
        self.assertIn("Limpar todos os pads", html)
        self.assertIn('window.confirm("Limpar o som de todos os 16 pads?', app)
        self.assertIn('fetch("/api/pads/clear", { method: "POST" })', app)

    def test_aba_master_expoe_volume_mute_e_limiter(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="tab-master"', html)
        self.assertIn('id="master-limiter-threshold"', html)
        self.assertIn("masterLimiterThreshold", app)
        self.assertIn(
            '<button id="engine-status" class="engine-status" type="button" aria-expanded="false">Motor</button>\n'
            '      <output id="cpu-meter" class="cpu-meter" aria-live="polite">CPU --</output>\n'
            '      <button id="tab-master" class="tab" type="button">Master</button>',
            html,
        )

    def test_config_tem_mapa_midi_unificado_com_filtro_e_banner(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        css = (FRONTEND / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="midi-map-filter"', html)
        self.assertIn('id="midi-learn-banner"', html)
        self.assertIn('id="midi-map-sections"', html)
        self.assertIn('id="midi-map-add-knob-btn"', html)
        # As ações agora vêm do registry em JS, não de linhas estáticas no HTML.
        self.assertNotIn('data-controller-action="panic"', html)
        self.assertIn("const MIDI_ACTIONS = [", app)
        self.assertIn('action: "panic"', app)
        self.assertIn('action: "tap_tempo"', app)
        self.assertIn("function renderMidiMap()", app)
        self.assertIn("function midiNoteChipLabel(number)", app)
        self.assertIn("· Oitava ${Math.floor(number / 12) - 2}", app)
        self.assertIn("updateMidiLearnBanner", app)
        self.assertIn(".midi-map-cards", css)
        self.assertIn(".midi-map-section.collapsed .midi-map-cards { display: none; }", css)
        self.assertIn(".midi-learn-banner", css)

    def test_mapa_midi_cobre_pads_e_knobs_e_colapsa_kit_browse(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        # Seção de pads reusa o note-learn existente.
        self.assertIn("function midiPadCard(", app)
        self.assertIn("`/api/pads/${pad.pad_number}/note/learn`", app)
        self.assertIn("`/api/pads/${pendingPad}/note/learn/cancel`", app)
        # Seção de knobs lista os CCs com remoção individual.
        self.assertIn("function midiKnobCard(", app)
        self.assertIn("`/api/knobs/${k.cc_number}`", app)
        # Navegação de kits começa colapsada.
        self.assertIn("kit_browse: true", app)
        # "Limpar" remove todos os vínculos de uma ação de uma vez.
        self.assertIn("clearBtn.textContent = \"Limpar\"", app)

    def test_biblioteca_divide_a_tela_entre_sons_e_lista_de_cenas(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="scene-search"', html)
        self.assertIn('id="scene-list"', html)
        self.assertIn('class="library-layout"', html)
        self.assertIn('class="library-scenes"', html)
        # The scene list lives inside the Biblioteca view, not Performance.
        sounds_view = html.split('<main id="view-sounds"', 1)[1].split("</main>", 1)[0]
        self.assertIn('id="scene-list"', sounds_view)
        performance_view = html.split('<main id="view-performance"', 1)[1].split("</main>", 1)[0]
        self.assertNotIn('id="scene-list"', performance_view)
        self.assertIn("function activeKits()", app)
        self.assertIn("function renderSceneList()", app)
        self.assertIn("/active`, {", app)

    def test_topo_exibe_percentual_de_cpu_do_sistema(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="cpu-meter"', html)
        self.assertIn("cpuMeter", app)
        self.assertIn("cpu_percent", app)

    def test_parametros_de_efeito_permitem_aprendizado_direto_de_knob(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("effect-knob-assign", app)
        self.assertIn("slot${slot.slot_index}:${p.symbol}", app)
        self.assertIn("startKnobCapture(\"pad\", padNumber", app)
        self.assertIn("refreshKnobTargets", app)

    def test_looper_tem_quatro_trilhas_selecionaveis_com_mute_e_volume(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        css = (FRONTEND / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="looper-track-list"', html)
        self.assertIn('id="looper-play-btn"', html)
        self.assertIn("function renderLooperTracks()", app)
        self.assertIn("/api/looper/tracks/${trackIndex}/select", app)
        self.assertIn("/api/looper/tracks/${trackIndex}/mute", app)
        self.assertIn("/api/looper/tracks/${Number(card.dataset.track)}/volume", app)
        self.assertIn("/api/looper/tracks/${trackIndex}/clear", app)
        self.assertIn("/api/looper/play", app)
        self.assertIn("tracks: msg.tracks || []", app)
        self.assertIn(".looper-tracks", css)
        self.assertIn(".looper-track.selected", css)
        self.assertIn(".looper-track.muted", css)
        self.assertIn(".looper-volume", css)

    def test_modal_de_kits_mantem_as_selecoes_atuais_visiveis(self):
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn("let currentKitItem = null;", app)
        self.assertIn("let currentSoundItem = null;", app)
        self.assertIn('currentKitItem.scrollIntoView({ block: "nearest" })', app)
        self.assertIn('currentSoundItem.scrollIntoView({ block: "nearest" })', app)


if __name__ == "__main__":
    unittest.main()
