"""Kit de cores por tipo de som: classificador (com nomes reais das cenas), geração e endpoints."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as diakopad_app
import smcpad as sp
import smcpad_api
import smcpad_colors as sc
import smcpad_device as dev
import storage
from smcpad_sim import FakeSmcPad

# nome do som -> tipo esperado (nomes reais das cenas do DiakoPad)
REAL_NAMES = {
    "kick1": "kick", "deep_kick": "kick", "bumbo_reverb": "kick", "18BD": "kick", "Kick": "kick",
    "Diakonia bumbo reverb": "kick", "kick_tr808_low": "kick", "kick_Rock_a": "kick",
    "snare1": "snare", "_NARE_04": "snare", "Sn3": "snare", "Sn Swish": "snare", "sn_Rock_a": "snare", "sn_Roll": "snare",
    "stick_Woody": "rim", "stick_Side": "rim",
    "Fairlight Palma": "clap", "FINGER_SNAPS": "clap", "clap_Dry_a": "clap",
    "hihat": "hihat", "MPC3000 Chimbal Fechado": "hihat", "Hat Cl": "hihat", "Hat Open": "hihat", "hhc_Rock_a": "hihat", "hho_Jazz_a": "hihat",
    "Trap-Cymbal-TR MW Ring Cym": "cymbal", "Crash": "cymbal", "Ride1": "cymbal", "cra_Rock_b": "cymbal",
    "Cr17 Mx1": "cymbal", "ride_DryBell": "cymbal", "KIT_Reversed_Cymbal_086": "cymbal",
    "Tom Hi": "tom", "Tom M1": "tom", "tom_Rock_lo": "tom", "Htmmf Mx1": "tom", "Mtmmf Mx1": "tom", "Ltmmf Mx1": "tom",
    "carrilhao": "bell", "707 Agogô": "bell", "TRIANGLE": "bell", "WINDCHIMES_1": "bell", "MARKTREE_L_H": "bell", "Diakonia carrilhao": "bell",
    "bongo1": "perc", "R8Shaker": "perc", "TYMP_REV": "perc", "Timpani4": "perc", "R8Pandiero1": "perc", "misc_Tambourine": "perc",
    "mixkit-crowd-laugh-424": "laugh", "mixkit-funny-giggling-2885": "laugh", "michael-jackson-hee-hee": "laugh",
    "errou_3": "fail", "fail-sound-effect": "fail", "mixkit-falling-male-scream-391": "fail", "mixkit-sad-game-over-trombone-471": "fail",
    "mixkit-cartoon-toy-whistle-616": "blip", "mixkit-hard-pop-click-2364": "blip",
    "como-e-amigo": "other", "oh-my-god-meme": "other",
}


class ClassifyTests(unittest.TestCase):
    def test_real_scene_sound_names(self):
        for name, expected in REAL_NAMES.items():
            with self.subTest(name=name):
                self.assertEqual(sc.classify(name), expected)

    def test_empty_pad(self):
        self.assertEqual(sc.classify(None), "empty")
        self.assertEqual(sc.classify(""), "empty")

    def test_case_accents_and_separators_do_not_matter(self):
        self.assertEqual(sc.classify("KICK"), "kick")
        self.assertEqual(sc.classify("hi-hat_fechado"), "hihat")
        self.assertEqual(sc.classify("Tom-Tom 2"), "tom")
        self.assertEqual(sc.classify("AGOGÔ grave"), "bell")

    def test_snare_does_not_swallow_snap_and_ride_beats_bell(self):
        self.assertEqual(sc.classify("snap_01"), "clap")
        self.assertEqual(sc.classify("ride bell"), "cymbal")

    def test_falls_back_to_the_folder_name(self):
        self.assertEqual(sc.classify("Sound 07", "MPC3000-Kit/Triangle"), "bell")
        self.assertEqual(sc.classify("Sound 07", "Kits/Hi Hats"), "hihat")
        self.assertEqual(sc.classify("Sound 07", "Misc"), "other")

    def test_legend_covers_every_type_with_distinct_colors(self):
        legend = sc.legend()
        self.assertEqual(len({item["color"] for item in legend}), len(legend))
        self.assertEqual({item["key"] for item in legend} >= {"kick", "snare", "hihat", "cymbal", "tom", "perc", "empty", "other"}, True)
        self.assertEqual(next(i for i in legend if i["key"] == "kick")["color"], "#0040f0")  # bumbo = azul


class KitColorsTests(unittest.TestCase):
    SOUNDS = [
        {"pad_number": 1, "midi_note": 36, "name": "kick1", "folder": None},
        {"pad_number": 2, "midi_note": 37, "name": "snare1", "folder": None},
        {"pad_number": 3, "midi_note": 38, "name": None, "folder": None},
        {"pad_number": 16, "midi_note": 51, "name": "Crash", "folder": None},
    ]

    def test_pads_go_to_the_bank_a_records_by_midi_note(self):
        colors, detail = sc.kit_colors(self.SOUNDS)
        self.assertEqual(len(colors), 128)
        self.assertEqual((colors[32], colors[33], colors[34], colors[47]), ("#0040f0", "#f00000", "#141414", "#00e0f0"))
        self.assertEqual([d["index"] for d in detail], [32, 33, 34, 47])
        self.assertEqual(detail[0]["label"], "Bumbo")

    def test_everything_else_keeps_the_base(self):
        base = [f"#{i:02x}0000" for i in range(128)]
        colors, _ = sc.kit_colors(self.SOUNDS, base)
        self.assertEqual(colors[:32], base[:32])
        self.assertEqual(colors[48:], base[48:])

    def test_without_base_other_records_are_dark(self):
        colors, _ = sc.kit_colors(self.SOUNDS)
        self.assertEqual(set(colors[:32]), {"#141414"})

    def test_explicit_note_map_wins_and_unmapped_notes_are_skipped(self):
        colors, detail = sc.kit_colors([{"pad_number": 1, "midi_note": 99, "name": "kick", "folder": None}], note_to_index={99: 70})
        self.assertEqual(colors[70], "#0040f0")
        _, detail = sc.kit_colors([{"pad_number": 1, "midi_note": 99, "name": "kick", "folder": None}])
        self.assertIsNone(detail[0]["index"])  # nota fora de 36–51 e sem mapa: não tem onde pintar

    def test_remapped_pad_notes_follow_the_note(self):
        colors, _ = sc.kit_colors([{"pad_number": 1, "midi_note": 40, "name": "kick", "folder": None}])
        self.assertEqual(colors[36], "#0040f0")  # nota 40 -> índice 32 + 4


class GenerationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        for p in (
            patch.object(storage, "DB_PATH", tmp / "t.db"),
            patch.dict(os.environ, {"DIAKOPAD_TOKEN": ""}),
            patch.object(smcpad_api, "BACKUP_DIR", tmp / "backups"),
        ):
            p.start()
            self.addCleanup(p.stop)
        storage.init_db()
        self.fake = FakeSmcPad()
        smcpad_api.set_service(dev.SmcPadService(lambda: self.fake, tmp / "backups", lambda: False))
        self.addCleanup(smcpad_api.set_service, None)
        self.addCleanup(self._tmp.cleanup)
        self.client = TestClient(diakopad_app.app)

    def make_scene(self, name, sounds):
        """Cria uma cena com os sons dados (lista de 16 nomes ou None) nos pads 1..16."""
        for pad, sound in enumerate(sounds, start=1):
            sample_id = storage.add_sample(f"{sound}.wav", sound, "Kits") if sound else None
            storage.assign_sample(pad, sample_id)
        return storage.save_scene(name, storage.list_pads(), storage.list_pad_effects())

    def colors_of(self, scene_id):
        return storage.get_scene_smc_colors(scene_id)


class StorageAndEndpointTests(GenerationCase):
    DIAKONIA = ["kick1", "snare1", "deep_kick", "bumbo_reverb", "carrilhao", "hihat", "bongo1", "bongo2", "18BD", "Crash", "_NARE_04", None, "Chimbal Fechado", "707 Agogô", None, "Fairlight Palma"]

    def test_scene_pad_sounds(self):
        sid = self.make_scene("Diakonia", self.DIAKONIA)
        sounds = storage.scene_pad_sounds(sid)
        self.assertEqual(len(sounds), 16)
        self.assertEqual((sounds[0]["pad_number"], sounds[0]["midi_note"], sounds[0]["name"], sounds[0]["folder"]), (1, 36, "kick1", "Kits"))
        self.assertIsNone(sounds[11]["name"])
        self.assertIsNone(storage.scene_pad_sounds(999))

    def test_generate_one_scene_groups_the_same_types(self):
        sid = self.make_scene("Diakonia", self.DIAKONIA)
        body = self.client.post(f"/api/smcpad/scenes/{sid}/colors/generate").json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["applied"])
        colors = self.colors_of(sid)
        kicks = [colors[32 + i] for i in (0, 2, 3, 8)]  # kick1, deep_kick, bumbo_reverb, 18BD
        self.assertEqual(set(kicks), {"#0040f0"})  # todos os bumbos em azul
        self.assertEqual({colors[32 + 1], colors[32 + 10]}, {"#f00000"})  # as caixas em vermelho
        self.assertEqual({colors[32 + 11], colors[32 + 14]}, {"#141414"})  # pads vazios apagados
        self.assertEqual(self.fake.writes, [])  # sem "aplicar", o aparelho não é tocado

    def test_base_comes_from_the_device_and_is_kept_on_regeneration(self):
        sid = self.make_scene("Diakonia", self.DIAKONIA)
        self.client.post(f"/api/smcpad/scenes/{sid}/colors/generate")
        first = self.colors_of(sid)
        self.assertEqual(first[0], self.service_colors()[0])  # grupo 1 veio do aparelho
        # edita à mão um registro fora do kit e gera de novo: a edição fica
        manual = list(first)
        manual[5] = "#123456"
        storage.set_scene_smc_colors(sid, manual)
        self.client.post(f"/api/smcpad/scenes/{sid}/colors/generate")
        self.assertEqual(self.colors_of(sid)[5], "#123456")

    def service_colors(self):
        image = bytes(self.fake.regions[sp.REGION_CONFIG])
        return [p["color"] for p in sp.parse_preset(sp.preset_slice(image, 1))["pads"]]

    def test_works_without_the_device_with_a_dark_base(self):
        sid = self.make_scene("Diakonia", self.DIAKONIA)
        self.fake.drop = 1000
        self.assertEqual(self.client.post(f"/api/smcpad/scenes/{sid}/colors/generate").status_code, 200)
        colors = self.colors_of(sid)
        self.assertEqual(colors[32], "#0040f0")
        self.assertEqual(set(colors[:32]), {"#141414"})

    def test_apply_paints_the_device_live(self):
        sid = self.make_scene("Diakonia", self.DIAKONIA)
        body = self.client.post(f"/api/smcpad/scenes/{sid}/colors/generate?apply=true").json()
        self.assertTrue(body["applied"])
        self.assertEqual(self.service_colors()[32], "#0040f0")
        self.assertEqual(self.fake.commits, [])  # ao vivo, sem gravar de vez

    def test_apply_failure_is_reported_but_the_colors_are_still_saved(self):
        sid = self.make_scene("Diakonia", self.DIAKONIA)
        storage.set_scene_smc_colors(sid, ["#000000"] * 128)  # já tem base: o aparelho só é usado ao aplicar
        self.fake.drop = 1000
        body = self.client.post(f"/api/smcpad/scenes/{sid}/colors/generate?apply=true").json()
        self.assertFalse(body["applied"])
        self.assertIn("apply_error", body)
        self.assertEqual(self.colors_of(sid)[32], "#0040f0")

    def test_generate_all_scenes_and_unknown_scene(self):
        a = self.make_scene("Diakonia", self.DIAKONIA)
        b = self.make_scene("Funny", ["errou_3"] + [None] * 15)
        body = self.client.post("/api/smcpad/scene-colors/generate-all").json()
        self.assertEqual({s["name"] for s in body["scenes"]}, {"Diakonia", "Funny"})
        self.assertEqual(self.colors_of(b)[32], "#ff2020")
        funny = next(s for s in body["scenes"] if s["name"] == "Funny")
        self.assertEqual(funny["types"], {"Falhas / gritos": 1, "Pad vazio": 15})
        self.assertIsNotNone(self.colors_of(a))
        self.assertEqual(self.client.post("/api/smcpad/scenes/999/colors/generate").status_code, 404)

    def test_generate_all_can_paint_the_current_scene(self):
        a = self.make_scene("Diakonia", self.DIAKONIA)
        self.make_scene("Funny", ["errou_3"] + [None] * 15)
        body = self.client.post(f"/api/smcpad/scene-colors/generate-all?apply_current={a}").json()
        self.assertEqual({s["name"]: s["applied"] for s in body["scenes"]}, {"Diakonia": True, "Funny": False})
        self.assertEqual(self.service_colors()[32], "#0040f0")

    def test_legend_is_listed_with_the_scenes(self):
        legend = self.client.get("/api/smcpad/scene-colors").json()["legend"]
        self.assertIn({"key": "kick", "label": "Bumbo", "color": "#0040f0"}, legend)


if __name__ == "__main__":
    unittest.main()
