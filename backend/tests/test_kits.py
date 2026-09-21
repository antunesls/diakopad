import tempfile
import unittest
from pathlib import Path

import storage


class KitStorageTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"
        storage.init_db()

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def _create_kit(self, name: str, category: str = "01_electronic", sort_index: int = 0,
                     pads: list[dict] | None = None, source_path: str | None = None) -> int:
        if pads is None:
            pads = [{"pad_number": 1, "sample_id": None, "display_name": "Kick"}]
        return storage.create_kit(name, category, sort_index, pads, source_path=source_path)

    def test_create_and_get_kit_round_trips_all_fields(self):
        pads = [
            {"pad_number": 1, "sample_id": None, "display_name": "808 Kick"},
            {"pad_number": 2, "sample_id": None, "display_name": "808 Snare"},
        ]
        kit_id = self._create_kit(
            "Roland TR-808", category="01_electronic", sort_index=10,
            pads=pads, source_path="Roland TR-808",
        )

        kit = storage.get_kit(kit_id)

        self.assertEqual(kit["name"], "Roland TR-808")
        self.assertEqual(kit["category"], "01_electronic")
        self.assertEqual(kit["sort_index"], 10)
        self.assertEqual(kit["source_path"], "Roland TR-808")
        self.assertEqual(len(kit["pads"]), 2)
        self.assertEqual(
            {(p["pad_number"], p["display_name"]) for p in kit["pads"]},
            {(1, "808 Kick"), (2, "808 Snare")},
        )

    def test_create_kit_upserts_by_name_instead_of_duplicating(self):
        first_id = self._create_kit("Diakonia", pads=[
            {"pad_number": 1, "sample_id": None, "display_name": "Kick"},
        ])
        second_id = self._create_kit("Diakonia", category="03_custom", sort_index=5, pads=[
            {"pad_number": 1, "sample_id": None, "display_name": "Bumbo"},
            {"pad_number": 2, "sample_id": None, "display_name": "Snare"},
        ])

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(storage.list_kits()), 1)
        kit = storage.get_kit(second_id)
        self.assertEqual(kit["category"], "03_custom")
        self.assertEqual(len(kit["pads"]), 2)

    def test_list_kit_categories_returns_distinct_sorted_categories(self):
        self._create_kit("A", category="30_hiphop")
        self._create_kit("B", category="01_electronic")
        self._create_kit("C", category="01_electronic")

        self.assertEqual(storage.list_kit_categories(), ["01_electronic", "30_hiphop"])

    def test_list_kits_in_category_orders_by_sort_index_then_name(self):
        self._create_kit("Zebra", category="01_electronic", sort_index=1)
        self._create_kit("Alpha", category="01_electronic", sort_index=1)
        self._create_kit("First", category="01_electronic", sort_index=0)
        self._create_kit("Other category", category="99_other", sort_index=0)

        names = [k["name"] for k in storage.list_kits_in_category("01_electronic")]

        self.assertEqual(names, ["First", "Alpha", "Zebra"])

    def test_kit_with_fewer_than_16_pads_only_stores_the_populated_ones(self):
        pads = [{"pad_number": n, "sample_id": None, "display_name": f"pad{n}"} for n in range(1, 9)]
        kit_id = self._create_kit("Octapad feel", pads=pads)

        kit = storage.get_kit(kit_id)

        self.assertEqual(len(kit["pads"]), 8)
        self.assertEqual({p["pad_number"] for p in kit["pads"]}, set(range(1, 9)))

    def test_delete_kit_removes_kit_and_cascades_to_kit_pads(self):
        kit_id = self._create_kit("Gone soon", pads=[
            {"pad_number": 1, "sample_id": None, "display_name": "Kick"},
            {"pad_number": 2, "sample_id": None, "display_name": "Snare"},
        ])

        self.assertTrue(storage.delete_kit(kit_id))

        self.assertIsNone(storage.get_kit(kit_id))
        conn = storage.get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM kit_pads WHERE kit_id = ?", (kit_id,)
            ).fetchone()["c"]
        finally:
            conn.close()
        self.assertEqual(count, 0)

    def test_delete_kit_missing_returns_false(self):
        self.assertFalse(storage.delete_kit(999))


class KitEndpointTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"
        storage.init_db()

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def test_list_kits_endpoint_returns_created_kits(self):
        import app as diakopad_app

        storage.create_kit("Roland TR-909", "01_electronic", 0, [
            {"pad_number": 1, "sample_id": None, "display_name": "Kick"},
        ])

        result = diakopad_app.list_kits()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Roland TR-909")

    def test_list_kit_categories_endpoint(self):
        import app as diakopad_app

        storage.create_kit("A", "01_electronic", 0, [])
        storage.create_kit("B", "30_hiphop", 0, [])

        self.assertEqual(diakopad_app.list_kit_categories(), ["01_electronic", "30_hiphop"])

    def test_get_kit_endpoint_returns_kit_or_404(self):
        import app as diakopad_app
        from fastapi import HTTPException

        kit_id = storage.create_kit("Emu SP12", "01_electronic", 0, [
            {"pad_number": 1, "sample_id": None, "display_name": "Kick"},
        ])

        kit = diakopad_app.get_kit(kit_id)
        self.assertEqual(kit["name"], "Emu SP12")

        with self.assertRaises(HTTPException) as ctx:
            diakopad_app.get_kit(999)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_kit_endpoint_returns_ok_or_404(self):
        import app as diakopad_app
        from fastapi import HTTPException

        kit_id = storage.create_kit("Fairlight IIX", "01_electronic", 0, [])

        result = diakopad_app.delete_kit(kit_id)
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(storage.get_kit(kit_id))

        with self.assertRaises(HTTPException) as ctx:
            diakopad_app.delete_kit(kit_id)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
