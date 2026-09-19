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

    def test_config_expoe_midi_learn_para_panic(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-controller-action="panic"', html)
        self.assertIn('id="controller-learn-btn-panic"', html)
        self.assertIn("panic:", app)

    def test_config_expoe_midi_learn_para_o_tap_do_metronomo(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-controller-action="tap_tempo"', html)
        self.assertIn('id="controller-learn-btn-tap_tempo"', html)
        self.assertIn("tap_tempo:", app)

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


if __name__ == "__main__":
    unittest.main()
