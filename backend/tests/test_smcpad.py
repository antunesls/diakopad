"""Testes do protocolo/modelo do SMC-PAD.

Os vetores "ouro" são mensagens reais capturadas do Midi Suite falando com um
SMC-PAD (veja docs/smcpad-protocolo.md): gerar exatamente os mesmos bytes é a
prova de que o protocolo está certo.
"""
import unittest

import smcpad as sp
from smcpad_sim import make_image, make_preset

# Capturados (hex da mensagem SysEx completa, F0..F7)
GOLD_IDENTIFY_TX = "f0003245000000407ff7"
GOLD_IDENTIFY_RX = "f000324558010040294d0635011508512f3060500100000000000000000000000000000000006009f7"
GOLD_READ_REGION4_TX = "f000320d410000000200000000400100006f01f7"
GOLD_READ_REGION5_FIRST_TX = "f000320d410000400200000000107e00000600f7"
GOLD_WRITE_PAD1_NOTE_37_TX = "f000320949000040021508000010000000257602f7"
GOLD_COMMIT_TX = "f0003209410000400200000000000000007a01f7"


class BytesTests(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        for n in range(0, 40):
            data = bytes((i * 37 + 11) & 0xFF for i in range(n))
            packed = sp.pack7(data)
            self.assertTrue(all(b < 0x80 for b in packed))
            self.assertEqual(sp.unpack7(packed), data)

    def test_checksum_empty_payload_is_ff(self):
        self.assertEqual(sp.checksum(b""), 0xFF)

    def test_unpack_accepts_sysex_markers(self):
        frame = sp.identify_request()
        self.assertEqual(sp.from_sysex(sp.to_sysex(frame)), frame)
        self.assertEqual(sp.from_sysex(sp.pack7(frame)), frame)


class GoldenFrameTests(unittest.TestCase):
    def test_requests_match_real_captures(self):
        self.assertEqual(sp.to_sysex(sp.identify_request()).hex(), GOLD_IDENTIFY_TX)
        self.assertEqual(sp.to_sysex(sp.read_request(4, 0, 12)).hex(), GOLD_READ_REGION4_TX)
        self.assertEqual(sp.to_sysex(sp.read_request(5, 0, 1009)).hex(), GOLD_READ_REGION5_FIRST_TX)
        self.assertEqual(sp.to_sysex(sp.write_request(5, 0x415, b"\x25")).hex(), GOLD_WRITE_PAD1_NOTE_37_TX)
        self.assertEqual(sp.to_sysex(sp.commit_request()).hex(), GOLD_COMMIT_TX)

    def test_identity_response_decodes_to_model_name(self):
        frame = sp.from_sysex(bytes.fromhex(GOLD_IDENTIFY_RX))
        self.assertEqual(sp.parse_identify(frame), "SMC-PAD_004")

    def test_parse_frame_rejects_corruption(self):
        frame = bytearray(sp.read_request(4, 0, 12))
        frame[-1] ^= 0x01
        with self.assertRaises(sp.ProtocolError):
            sp.parse_frame(bytes(frame))
        with self.assertRaises(sp.ProtocolError):
            sp.parse_frame(b"\x00\x58" + sp.read_request(4, 0, 12)[2:])
        with self.assertRaises(sp.ProtocolError):
            sp.parse_frame(sp.read_request(4, 0, 12)[:-2])

    def test_read_response_must_match_request(self):
        data = bytes(range(12))
        payload = bytes([4]) + (0).to_bytes(4, "little") + (12).to_bytes(3, "little") + data
        frame = sp.build_frame(sp.CMD_READ, payload)
        self.assertEqual(sp.parse_read_response(frame, 4, 0, 12), data)
        with self.assertRaises(sp.ProtocolError):
            sp.parse_read_response(frame, 5, 0, 12)

    def test_ack_status(self):
        sp.parse_ack(sp.build_frame(sp.CMD_ACK, b"\x00"))
        with self.assertRaises(sp.ProtocolError):
            sp.parse_ack(sp.build_frame(sp.CMD_ACK, b"\x01"))


class ModelTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(sp.CONFIG_SIZE, 28312)
        self.assertEqual(sp.PAD_BASE + sp.PAD_COUNT * sp.PAD_SIZE, sp.PRESET_SIZE)
        self.assertEqual(sp.KNOB_BASE + sp.KNOB_COUNT * sp.KNOB_SIZE, sp.PAD_BASE)
        self.assertEqual(sp.BUTTON_BASE + sp.BUTTON_COUNT * sp.BUTTON_SIZE, sp.KNOB_BASE)

    def test_factory_globals(self):
        g = sp.parse_globals(bytes.fromhex("780032040000020001010000"))
        self.assertEqual((g["tempo"], g["swing"], g["time"]), (120, 50, 4))
        self.assertEqual(sp.TIME_VALUES[g["time"]], "1/16")
        self.assertEqual((g["octave"], g["pad_curve"], g["aftertouch"], g["preset"], g["pad_bank"]), (3, 2, 1, 1, 0))

    def test_preset_parse_matches_midi_suite_screen(self):
        preset = sp.parse_preset(make_preset())
        pad1 = preset["pads"][32]  # nota 36: o "PAD1" do Midi Suite
        self.assertEqual((pad1["type"], pad1["channel"], pad1["note"]), (0, 10, 36))
        self.assertEqual((pad1["min_vel"], pad1["max_vel"], pad1["led"]), (0, 127, 255))
        self.assertEqual(pad1["color"], "#f000f0")
        self.assertEqual(sp.PAD_BASE + 32 * sp.PAD_SIZE, 0x413)  # início do registro (byte Type) capturado
        button = preset["buttons"][0]
        self.assertEqual((button["mode"], button["channel"], button["cc"], button["value1"], button["value2"], button["led"]), (2, 1, 25, 0, 127, 255))
        self.assertEqual(button["sysex"], "F0 7F 7F 06 01 F7")
        knob = preset["knobs"][0]
        self.assertEqual((knob["type"], knob["speed"], knob["channel"], knob["cc"], knob["min"], knob["max"]), (0, 2, 1, 30, 0, 127))

    def test_calibration(self):
        data = (bytes.fromhex("dc00 2602 d002 5203 b603".replace(" ", "")) * 16)
        pads = sp.parse_calibration(data)
        self.assertEqual(pads[0], {"pad": 1, "raw": 220, "level": 2})
        self.assertEqual([sp.level_to_raw(n) for n in range(1, 9)], [50, 150, 250, 350, 450, 550, 650, 750])
        self.assertEqual(sp.raw_to_level(150), 2)


class EditTests(unittest.TestCase):
    def test_pad_note_edit_matches_capture(self):
        region, address, data = sp.plan_edit({"scope": "pad", "preset": 1, "index": 32, "field": "note", "value": 37})
        self.assertEqual((region, address, data), (5, 0x415, b"\x25"))

    def test_pad_fields_match_captured_addresses(self):
        def addr(field, value):
            return sp.plan_edit({"scope": "pad", "preset": 1, "index": 32, "field": field, "value": value})[:2]

        self.assertEqual(addr("type", 1), (5, 0x413))
        self.assertEqual(addr("channel", 3), (5, 0x414))
        self.assertEqual(addr("min_vel", 10), (5, 0x416))
        self.assertEqual(addr("max_vel", 100), (5, 0x417))
        self.assertEqual(addr("led", 100), (5, 0x41B))
        region, address, data = sp.plan_edit({"scope": "pad", "preset": 1, "index": 32, "field": "color", "value": "#FF0004"})
        self.assertEqual((region, address, data), (5, 0x418, b"\xff\x00\x04"))
        self.assertEqual(sp.plan_edit({"scope": "pad", "preset": 1, "index": 32, "field": "channel", "value": 3})[2], b"\x02")

    def test_button_and_knob_addresses_match_captures(self):
        self.assertEqual(sp.plan_edit({"scope": "button", "preset": 1, "index": 0, "field": "led", "value": 100}), (5, 0x16, b"\x64"))
        self.assertEqual(sp.plan_edit({"scope": "button", "preset": 1, "index": 0, "field": "cc", "value": 40}), (5, 0x02, b"\x28"))
        self.assertEqual(sp.plan_edit({"scope": "knob", "preset": 1, "index": 0, "field": "speed", "value": 3})[:2], (5, 0x74))
        self.assertEqual(sp.plan_edit({"scope": "knob", "preset": 1, "index": 0, "field": "cc", "value": 50}), (5, 0x76, b"\x32"))
        self.assertEqual(sp.plan_edit({"scope": "knob", "preset": 1, "index": 0, "field": "type", "value": 1})[:2], (5, 0x73))

    def test_sysex_edit_matches_capture(self):
        region, address, data = sp.plan_edit({"scope": "button", "preset": 1, "index": 0, "field": "sysex", "value": "F0 7F 7F 06 02 F7"})
        self.assertEqual((region, address), (5, 0x05))
        self.assertEqual(data, bytes.fromhex("06f07f7f0602f7") + bytes(10))
        self.assertEqual(len(data), 17)

    def test_other_preset_adds_offset(self):
        _, address, _ = sp.plan_edit({"scope": "pad", "preset": 2, "index": 32, "field": "note", "value": 37})
        self.assertEqual(address, sp.PRESET_SIZE + 0x415)

    def test_globals_match_captures(self):
        self.assertEqual(sp.plan_edit({"scope": "globals", "field": "tempo", "value": 90}), (4, 0, b"\x5a\x00"))
        self.assertEqual(sp.plan_edit({"scope": "globals", "field": "swing", "value": 60}), (4, 2, b"\x3c"))
        self.assertEqual(sp.plan_edit({"scope": "globals", "field": "octave", "value": 5}), (4, 6, b"\x04"))
        self.assertEqual(sp.plan_edit({"scope": "globals", "field": "pad_curve", "value": 3}), (4, 8, b"\x02"))
        self.assertEqual(sp.plan_edit({"scope": "globals", "field": "preset", "value": 2}), (4, 10, b"\x01"))

    def test_calibration_matches_capture(self):
        self.assertEqual(sp.plan_edit({"scope": "calibration", "pad": 1, "field": "level", "value": 8}), (3, 0, (750).to_bytes(2, "little")))
        self.assertEqual(sp.plan_edit({"scope": "calibration", "pad": 13, "field": "level", "value": 2}), (3, 0x78, (150).to_bytes(2, "little")))
        self.assertEqual(sp.plan_edit({"scope": "calibration", "pad": 1, "field": "raw", "value": 220})[2], (220).to_bytes(2, "little"))

    def test_invalid_edits_are_rejected(self):
        bad = [
            {"scope": "pad", "preset": 1, "index": 32, "field": "note", "value": 128},
            {"scope": "pad", "preset": 1, "index": 128, "field": "note", "value": 1},
            {"scope": "pad", "preset": 9, "index": 0, "field": "note", "value": 1},
            {"scope": "pad", "preset": 1, "index": 0, "field": "channel", "value": 0},
            {"scope": "pad", "preset": 1, "index": 0, "field": "channel", "value": 17},
            {"scope": "pad", "preset": 1, "index": 0, "field": "color", "value": "vermelho"},
            {"scope": "pad", "preset": 1, "index": 0, "field": "nome", "value": 1},
            {"scope": "pad", "preset": 1, "index": 0, "field": "note", "value": "36"},
            {"scope": "pad", "preset": 1, "index": 0, "field": "note", "value": True},
            {"scope": "button", "preset": 1, "index": 0, "field": "sysex", "value": "F0 " + "00 " * 16},
            {"scope": "button", "preset": 1, "index": 0, "field": "sysex", "value": "ZZ"},
            {"scope": "globals", "field": "pad_bank", "value": 1},
            {"scope": "globals", "field": "tempo", "value": 5},
            {"scope": "calibration", "pad": 17, "field": "level", "value": 1},
            {"scope": "calibration", "pad": 1, "field": "level", "value": 9},
            {"scope": "calibration", "pad": 1, "field": "raw", "value": 5000},
            {"scope": "nada"},
        ]
        for edit in bad:
            with self.subTest(edit=edit), self.assertRaises(sp.EditError):
                sp.plan_edit(edit)

    def test_apply_to_image(self):
        image = bytearray(make_image())
        region, address, data = sp.plan_edit({"scope": "pad", "preset": 1, "index": 32, "field": "note", "value": 99})
        sp.apply_to_image(image, region, address, data)
        self.assertEqual(sp.parse_preset(sp.preset_slice(bytes(image), 1))["pads"][32]["note"], 99)
        with self.assertRaises(sp.EditError):
            sp.apply_to_image(image, 5, sp.CONFIG_SIZE, b"\x00")


if __name__ == "__main__":
    unittest.main()
