import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import midi


class MidiHardwareFilterTests(unittest.TestCase):
    def test_filters_aftertouch_before_forwarding_hardware_midi_to_players(self):
        input_port = Mock()
        hardware_output = Mock()
        mido_module = SimpleNamespace(
            open_input=Mock(return_value=input_port),
            open_output=Mock(return_value=hardware_output),
        )
        original_input = midi._input_port
        original_hardware_output = midi._hardware_output_port

        try:
            with patch.dict(sys.modules, {"mido": mido_module}):
                self.assertTrue(midi.open_input(Mock()))

            callback = mido_module.open_input.call_args.kwargs["callback"]
            note_on = SimpleNamespace(type="note_on", note=41, velocity=127)
            aftertouch = SimpleNamespace(type="aftertouch", value=110)
            callback(note_on)
            callback(aftertouch)

            hardware_output.send.assert_called_once_with(note_on)
        finally:
            midi._input_port = original_input
            midi._hardware_output_port = original_hardware_output
