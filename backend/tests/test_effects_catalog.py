import unittest

from engine import effects_catalog


class CatalogShapeTests(unittest.TestCase):
    def test_every_entry_has_validated_uri_ports_and_params(self):
        for plugin_id, entry in effects_catalog.PLUGIN_CATALOG.items():
            with self.subTest(plugin_id=plugin_id):
                self.assertTrue(entry["lv2_uri"])
                self.assertEqual(len(entry["in_ports"]), 2)
                self.assertEqual(len(entry["out_ports"]), 2)
                self.assertTrue(entry["params"])
                symbols = [p["symbol"] for p in entry["params"]]
                self.assertEqual(len(symbols), len(set(symbols)))

    def test_every_param_default_lies_within_its_range(self):
        for plugin_id, entry in effects_catalog.PLUGIN_CATALOG.items():
            for p in entry["params"]:
                with self.subTest(plugin_id=plugin_id, symbol=p["symbol"]):
                    self.assertLessEqual(p["min"], p["default"])
                    self.assertLessEqual(p["default"], p["max"])
                    self.assertIn(p["curve"], ("linear", "log"))

    def test_curated_reverbs_and_modulation_are_in_the_catalog(self):
        for plugin_id in ("reverb", "reverb_plate", "reverb_ir", "chorus", "flanger", "phaser"):
            self.assertIn(plugin_id, effects_catalog.PLUGIN_CATALOG)

    def test_list_catalog_is_serializable_and_keeps_plugin_ids(self):
        listed = effects_catalog.list_catalog()
        self.assertEqual(
            [e["plugin_id"] for e in listed], list(effects_catalog.PLUGIN_CATALOG)
        )
        self.assertTrue(all(e["params"] for e in listed))


class EffectiveParamsTests(unittest.TestCase):
    def test_legacy_mda_reverb_params_are_dropped_and_clamped(self):
        legacy = {"mix": 0.3, "size": 0.5, "hf_damp": 0.5}  # mda/Ambience symbols
        effective = effects_catalog.effective_params("reverb", legacy)
        self.assertNotIn("mix", effective)
        self.assertNotIn("hf_damp", effective)
        # Dragonfly Hall also has a `size` port (10..60 m): the legacy 0..1
        # value is clamped up to the new range's minimum, never passed raw.
        self.assertEqual(effective["size"], 10.0)
        self.assertEqual(effective["decay"], effects_catalog.default_params("reverb")["decay"])

    def test_stored_values_override_defaults_and_out_of_range_is_clamped(self):
        stored = {"decay": 4.0, "late_level": 500.0}
        effective = effects_catalog.effective_params("reverb", stored)
        self.assertEqual(effective["decay"], 4.0)
        self.assertEqual(effective["late_level"], 100.0)  # clamped to max

    def test_non_numeric_and_none_stored_values_fall_back_to_default(self):
        defaults = effects_catalog.default_params("reverb")
        stored = {"decay": "abc", "width": None}
        effective = effects_catalog.effective_params("reverb", stored)
        self.assertEqual(effective["decay"], defaults["decay"])
        self.assertEqual(effective["width"], defaults["width"])

    def test_none_stored_and_unknown_plugin_return_empty_handled(self):
        self.assertEqual(effects_catalog.effective_params("nope", {"mix": 1}), {})
        self.assertEqual(
            effects_catalog.effective_params("reverb", None),
            effects_catalog.default_params("reverb"),
        )


if __name__ == "__main__":
    unittest.main()
