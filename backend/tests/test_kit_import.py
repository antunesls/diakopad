import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import storage
from curate_kit_pack import score_role


class ScoreRoleTests(unittest.TestCase):
    """Pure function, no disk access - keyword-matching against real
    filenames seen in the archive."""

    def test_matches_kick_variants(self):
        for name in ("TR-808Kick01.wav", "BDRUM1.WAV", "Bassdrum.wav", "bumbo_reverb.wav"):
            self.assertEqual(score_role(name), "kick", name)

    def test_matches_snare(self):
        for name in ("TR-909Snare 01.wav", "SNARE1.WAV", "SDSnare01.wav"):
            self.assertEqual(score_role(name), "snare", name)

    def test_matches_closed_and_open_hat_variants(self):
        self.assertEqual(score_role("TR606Hat_C01.wav"), "closed_hat")
        self.assertEqual(score_role("TR-909Hat C 01.wav"), "closed_hat")
        self.assertEqual(score_role("HHCLOSE1.WAV"), "closed_hat")
        self.assertEqual(score_role("hihat.wav"), "closed_hat")
        self.assertEqual(score_role("TR606Hat_O01.wav"), "open_hat")
        self.assertEqual(score_role("HHOPEN1.WAV"), "open_hat")

    def test_matches_cowbell_including_the_bare_cow_abbreviation(self):
        for name in ("TR-808Cow.wav", "DR550Cowbell.wav", "Cowbel.wav", "DMXAgogo.wav"):
            self.assertEqual(score_role(name), "cowbell", name)

    def test_matches_role_via_parent_folder_name(self):
        # Linn LinnDrum/Bassdrums/Bassdrum.wav - filename alone already says
        # "bassdrum", but confirm the parent-folder signal also works on its
        # own too (Korg M1/Percussion/Hit.wav - a generic filename that only
        # reads as percussion because of the folder it's in).
        self.assertEqual(score_role("Bassdrum-01.wav", parent_folder="Bassdrums"), "kick")
        self.assertEqual(score_role("Hit.wav", parent_folder="Percussion"), "perc")

    def test_no_match_returns_none(self):
        self.assertIsNone(score_role("Electric Piano.wav"))
        self.assertIsNone(score_role("desktop.ini"))


class ImportPackTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"
        storage.init_db()

        import kit_import
        self._kit_import = kit_import
        self._original_samples_dir = kit_import.SAMPLES_DIR
        self._samples_dir = Path(self._temp_dir.name) / "samples"
        kit_import.SAMPLES_DIR = self._samples_dir

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._kit_import.SAMPLES_DIR = self._original_samples_dir
        self._temp_dir.cleanup()

    def _build_pack(self, path: Path) -> None:
        manifest = {
            "pack_name": "Test Pack",
            "created_at": "2026-01-01",
            "kits": [
                {
                    "name": "Roland TR-808",
                    "category": "01_electronic",
                    "sort_index": 10,
                    "source_path": "Roland TR-808",
                    "pads": [
                        {
                            "pad_number": 1,
                            "role": "kick",
                            "filename": "roland-tr-808/samples/Kick01.wav",
                            "display_name": "808 Kick",
                            "source_file": "Roland TR-808/Kick01.wav",
                        },
                        {
                            "pad_number": 2,
                            "role": "snare",
                            "filename": "roland-tr-808/samples/Snare01.wav",
                            "display_name": "808 Snare",
                            "source_file": "Roland TR-808/Snare01.wav",
                        },
                    ],
                }
            ],
        }
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("roland-tr-808/samples/Kick01.wav", b"fake-kick-audio")
            zf.writestr("roland-tr-808/samples/Snare01.wav", b"fake-snare-audio")

    def test_import_pack_creates_kit_with_samples(self):
        pack_path = Path(self._temp_dir.name) / "pack.zip"
        self._build_pack(pack_path)

        kit_ids = self._kit_import.import_pack(pack_path)

        self.assertEqual(len(kit_ids), 1)
        kit = storage.get_kit(kit_ids[0])
        self.assertEqual(kit["name"], "Roland TR-808")
        self.assertEqual(kit["category"], "01_electronic")
        self.assertEqual(len(kit["pads"]), 2)
        pads_by_number = {p["pad_number"]: p for p in kit["pads"]}
        self.assertEqual(pads_by_number[1]["display_name"], "808 Kick")
        self.assertIsNotNone(pads_by_number[1]["sample_id"])
        self.assertTrue(pads_by_number[1]["filename"])

        samples = storage.list_samples()
        self.assertEqual(len(samples), 2)
        for sample in samples:
            self.assertTrue((self._samples_dir / sample["filename"]).is_file())

    def test_reimporting_the_same_pack_upserts_instead_of_duplicating(self):
        pack_path = Path(self._temp_dir.name) / "pack.zip"
        self._build_pack(pack_path)

        first_ids = self._kit_import.import_pack(pack_path)
        second_ids = self._kit_import.import_pack(pack_path)

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(len(storage.list_kits()), 1)
        # Re-running re-adds fresh sample rows/files under new uuid names
        # (create_kit replaces the kit's pad rows wholesale) - not a
        # duplicate kit, but does leave the previous samples/files as
        # unreferenced orphans, same tradeoff import_tool.py already accepts
        # for re-imports of changed content.
        self.assertEqual(len(storage.list_kits()), 1)

    def _build_single_pad_pack(self, path: Path, pad: dict, extra_files: dict | None = None) -> None:
        manifest = {"kits": [{"name": "Evil Kit", "pads": [pad]}]}
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            for name, data in (extra_files or {}).items():
                zf.writestr(name, data)

    def test_rejects_filename_escaping_the_pack_with_dotdot(self):
        outside = Path(self._temp_dir.name) / "outside.wav"
        outside.write_bytes(b"not-from-the-pack")
        pack_path = Path(self._temp_dir.name) / "pack.zip"
        # O pacote é extraído num tmpdir do sistema; "../" suficientes saem dele.
        target = outside.resolve()
        rel = "../" * 40 + target.relative_to(target.anchor).as_posix()
        self._build_single_pad_pack(pack_path, {"pad_number": 1, "filename": rel})

        with self.assertRaisesRegex(ValueError, "fora do pacote"):
            self._kit_import.import_pack(pack_path)
        self.assertEqual(storage.list_samples(), [])

    def test_rejects_absolute_filename(self):
        outside = Path(self._temp_dir.name) / "outside.wav"
        outside.write_bytes(b"not-from-the-pack")
        pack_path = Path(self._temp_dir.name) / "pack.zip"
        self._build_single_pad_pack(pack_path, {"pad_number": 1, "filename": str(outside.resolve())})

        with self.assertRaisesRegex(ValueError, "fora do pacote"):
            self._kit_import.import_pack(pack_path)
        self.assertEqual(storage.list_samples(), [])

    def test_rejects_pad_number_out_of_range(self):
        pack_path = Path(self._temp_dir.name) / "pack.zip"
        for bad in (0, 17, "1", True):
            self._build_single_pad_pack(
                pack_path,
                {"pad_number": bad, "filename": "a.wav"},
                {"a.wav": b"audio"},
            )
            with self.assertRaisesRegex(ValueError, "pad_number"):
                self._kit_import.import_pack(pack_path)

    def test_rejects_pack_whose_uncompressed_size_exceeds_limit(self):
        pack_path = Path(self._temp_dir.name) / "pack.zip"
        self._build_single_pad_pack(pack_path, {"pad_number": 1, "filename": "a.wav"}, {"a.wav": b"x" * 2048})
        original = self._kit_import.MAX_PACK_UNCOMPRESSED_BYTES
        self._kit_import.MAX_PACK_UNCOMPRESSED_BYTES = 1024
        try:
            with self.assertRaisesRegex(ValueError, "grande demais"):
                self._kit_import.import_pack(pack_path)
        finally:
            self._kit_import.MAX_PACK_UNCOMPRESSED_BYTES = original


if __name__ == "__main__":
    unittest.main()
