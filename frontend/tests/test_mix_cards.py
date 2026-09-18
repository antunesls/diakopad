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
        self.assertIn('.mix-card-grid', css)
        self.assertIn('@media (max-width: 700px)', css)


if __name__ == "__main__":
    unittest.main()
