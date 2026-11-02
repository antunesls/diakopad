"""Aba Efeitos como mesa de envios (proposta A aprovada)."""

from pathlib import Path
import unittest


FRONTEND = Path(__file__).resolve().parents[1]


class FxMesaTest(unittest.TestCase):
    def setUp(self):
        self.html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        self.css = (FRONTEND / "style.css").read_text(encoding="utf-8")

    def test_aba_tem_barra_de_efeitos_legenda_e_mesa(self):
        self.assertIn('id="fx-bus-panel"', self.html)
        self.assertIn('id="fx-legend"', self.html)
        self.assertIn('<tbody id="effects-list">', self.html)
        for coluna in ("Hall", "Plate", "Delay", "Só deste pad"):
            self.assertIn(coluna, self.html)

    def test_envio_e_um_slider_acessivel_que_so_envia_ao_soltar(self):
        self.assertIn('setAttribute("role", "slider")', self.app)
        self.assertIn("aria-valuenow", self.app)
        self.assertIn('addEventListener("pointerup", end)', self.app)
        # a mesa não é redesenhada sob o dedo durante o arrasto
        self.assertIn("if (state.fxDragging) return;", self.app)

    def test_detalhe_do_pad_tem_tom_afinacao_e_slots(self):
        self.assertIn("function padDetailRow", self.app)
        self.assertIn("{ tune: Number(tuneInput.value) }", self.app)
        self.assertIn("renderEffectSlots(tr.querySelector(\".effect-slots\")", self.app)

    def test_cores_dos_efeitos_compartilhados(self):
        for token in ("--fx-hall", "--fx-plate", "--fx-delay"):
            self.assertIn(token, self.css)


if __name__ == "__main__":
    unittest.main()
