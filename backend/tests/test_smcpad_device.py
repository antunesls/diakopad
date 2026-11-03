"""Testes da camada de acesso ao SMC-PAD usando um simulador que fala o protocolo real."""
import tempfile
import unittest
from pathlib import Path
import smcpad as sp
import smcpad_device as dev
from smcpad_sim import FakeSmcPad

class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeSmcPad()
        self.enabled = True
        self.service = dev.SmcPadService(lambda: self.fake, Path(self.tmp.name), lambda: self.enabled)

    def pad1(self):
        return sp.parse_preset(sp.preset_slice(bytes(self.fake.regions[sp.REGION_CONFIG]), 1))["pads"][32]

    NOTE_EDIT = {"scope": "pad", "preset": 1, "index": 32, "field": "note", "value": 37}


class PortChoiceTests(unittest.TestCase):
    LINUX = [
        "Midi-Bridge:Midi Through Port-0 (capture)",
        "Midi-Bridge:SINCO SMC-PAD-Private (capture)",
        "Midi-Bridge:SINCO SMC-PAD-Master (capture)",
        "Midi-Bridge:SINCO  (capture)",
        "BLE MIDI 1:out",
    ]

    def test_prefers_the_private_port_on_linux(self):
        self.assertEqual(dev.choose_port(self.LINUX), "Midi-Bridge:SINCO SMC-PAD-Private (capture)")

    def test_does_not_use_alphabetical_order(self):
        # a porta sem nome ("SINCO  (capture)") vem primeiro em ordem alfabética, mas não é a de configuração
        self.assertNotEqual(dev.choose_port(self.LINUX), sorted(n for n in self.LINUX if "SINCO" in n)[0])

    def test_windows_style_names_use_the_first_match(self):
        self.assertEqual(dev.choose_port(["Microsoft GS Wavetable Synth", "SMC-PAD", "MIDIIN2 (SMC-PAD)", "MIDIIN3 (SMC-PAD)"]), "SMC-PAD")

    def test_ignores_midi_through_and_missing_device(self):
        self.assertIsNone(dev.choose_port(["Midi-Bridge:Midi Through Port-0 (capture)", "BLE MIDI 1:out"], r"SINCO|SMC-PAD|Through"))
        self.assertIsNone(dev.choose_port([]))


class ReadTests(ServiceCase):
    def test_read_all_returns_the_whole_memory(self):
        snap = self.service.snapshot()
        self.assertEqual(snap.identity, "SMC-PAD_004")
        self.assertEqual(snap.config, bytes(self.fake.regions[sp.REGION_CONFIG]))
        self.assertEqual(len(snap.config), sp.CONFIG_SIZE)
        self.assertEqual(snap.current_preset, 1)  # byte 10 = 0 -> Preset 1
        self.assertEqual(snap.globals["tempo"], 120)

    def test_status(self):
        status = self.service.status()
        self.assertTrue(status["connected"])
        self.assertEqual(status["identity"], "SMC-PAD_004")
        self.assertTrue(status["write_enabled"])

    def test_retries_when_the_device_drops_a_reply(self):
        self.fake.drop = 2
        self.assertEqual(self.service.snapshot().identity, "SMC-PAD_004")

    def test_gives_up_when_the_device_never_answers(self):
        self.fake.drop = 1000
        with self.assertRaises(dev.DeviceError):
            self.service.snapshot()

    def test_transport_is_closed(self):
        self.service.snapshot()
        self.assertGreaterEqual(self.fake.closed, 1)


class WriteTests(ServiceCase):
    def test_disabled_by_default_touches_nothing(self):
        self.enabled = False
        with self.assertRaises(dev.WriteDisabled):
            self.service.apply_edits([self.NOTE_EDIT])
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(list(Path(self.tmp.name).glob("*")), [])

    def test_edit_writes_verifies_commits_and_backs_up(self):
        original = bytes(self.fake.regions[sp.REGION_CONFIG])
        report = self.service.apply_edits([self.NOTE_EDIT])
        self.assertTrue(report["ok"] and report["committed"])
        self.assertEqual(self.pad1()["note"], 37)
        self.assertEqual(report["applied"][0]["before"], 36)
        self.assertEqual(self.fake.writes, [(5, 0x415, b"\x25")])
        self.assertEqual(self.fake.commits, [sp.REGION_CONFIG])
        self.assertEqual(Path(report["backup"]).read_bytes(), original)  # backup = estado anterior
        self.assertTrue(Path(report["backup"]).with_suffix(".json").exists())

    def test_commit_can_be_skipped(self):
        report = self.service.apply_edits([self.NOTE_EDIT], commit=False)
        self.assertFalse(report["committed"])
        self.assertEqual(self.fake.commits, [])

    def test_calibration_edit_uses_its_own_commit(self):
        self.service.apply_edits([{"scope": "calibration", "pad": 1, "field": "level", "value": 3}])
        self.assertEqual(bytes(self.fake.regions[sp.REGION_CALIBRATION][:2]), (250).to_bytes(2, "little"))
        self.assertEqual(self.fake.commits, [sp.REGION_CALIBRATION])

    def test_globals_and_config_share_the_config_commit(self):
        self.service.apply_edits([{"scope": "globals", "field": "tempo", "value": 90}, self.NOTE_EDIT])
        self.assertEqual(self.fake.commits, [sp.REGION_CONFIG])
        self.assertEqual(sp.parse_globals(bytes(self.fake.regions[sp.REGION_GLOBALS]))["tempo"], 90)

    def test_other_preset_requires_activation(self):
        edit = {**self.NOTE_EDIT, "preset": 2}
        with self.assertRaises(sp.EditError):
            self.service.apply_edits([edit])
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(list(Path(self.tmp.name).glob("*")), [])  # nem backup: nada foi tentado

    def test_activating_the_preset_in_the_same_batch_is_allowed(self):
        edit = {**self.NOTE_EDIT, "preset": 2}
        self.service.apply_edits([edit, {"scope": "globals", "field": "preset", "value": 2}])
        self.assertEqual(self.fake.writes[0], (4, 10, b"\x01"))  # o preset vai primeiro
        self.assertEqual(self.fake.writes[1][1], sp.PRESET_SIZE + 0x415)

    def test_expected_value_mismatch_is_rejected(self):
        with self.assertRaises(sp.EditError):
            self.service.apply_edits([{**self.NOTE_EDIT, "expected": 99}])
        self.assertEqual(self.fake.writes, [])
        self.service.apply_edits([{**self.NOTE_EDIT, "expected": 36}])
        self.assertEqual(self.pad1()["note"], 37)

    def test_invalid_edit_is_rejected_before_any_write(self):
        with self.assertRaises(sp.EditError):
            self.service.apply_edits([self.NOTE_EDIT, {**self.NOTE_EDIT, "value": 500}])
        self.assertEqual(self.fake.writes, [])

    def test_verify_failure_rolls_back_and_does_not_commit(self):
        original = bytes(self.fake.regions[sp.REGION_CONFIG])
        self.fake.corrupt_next_write = True
        with self.assertRaises(dev.VerifyError):
            self.service.apply_edits([self.NOTE_EDIT])
        self.assertEqual(bytes(self.fake.regions[sp.REGION_CONFIG]), original)  # restaurado
        self.assertEqual(self.fake.commits, [])

    def test_empty_edit_list_is_rejected(self):
        with self.assertRaises(sp.EditError):
            self.service.apply_edits([])


class PresetFileTests(ServiceCase):
    def test_export_is_one_preset(self):
        data = self.service.export_preset(1)
        self.assertEqual(len(data), sp.PRESET_SIZE)
        self.assertEqual(data, sp.preset_slice(bytes(self.fake.regions[sp.REGION_CONFIG]), 1))

    def test_import_roundtrip_and_chunking(self):
        data = bytearray(self.service.export_preset(1))
        data[0x415] = 99
        report = self.service.import_preset(1, bytes(data))
        self.assertTrue(report["committed"])
        self.assertEqual(self.pad1()["note"], 99)
        sizes = [len(w[2]) for w in self.fake.writes]
        self.assertEqual(sizes, [1024, 1024, 1024, 467])  # igual ao Importar do Midi Suite
        self.assertEqual(self.fake.commits, [sp.REGION_CONFIG])

    def test_import_checks_size_preset_and_flag(self):
        with self.assertRaises(sp.EditError):
            self.service.import_preset(1, b"\x00" * 10)
        with self.assertRaises(sp.EditError):
            self.service.import_preset(2, bytes(sp.PRESET_SIZE))
        self.enabled = False
        with self.assertRaises(dev.WriteDisabled):
            self.service.import_preset(1, bytes(sp.PRESET_SIZE))
        self.assertEqual(self.fake.writes, [])

    def test_import_failure_restores_the_preset(self):
        original = bytes(self.fake.regions[sp.REGION_CONFIG])
        data = bytearray(original[: sp.PRESET_SIZE])
        data[10] ^= 0xFF
        self.fake.corrupt_next_write = True
        with self.assertRaises(dev.VerifyError):
            self.service.import_preset(1, bytes(data))
        self.assertEqual(bytes(self.fake.regions[sp.REGION_CONFIG]), original)
        self.assertEqual(self.fake.commits, [])


if __name__ == "__main__":
    unittest.main()
