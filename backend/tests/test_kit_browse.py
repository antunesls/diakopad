import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from fastapi import HTTPException

import app as diakopad_app
import storage


class KitBrowseTests(unittest.IsolatedAsyncioTestCase):
    def _with_temp_db(self):
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        return temp_ctx, original_db_path

    def _seed_kits(self):
        """4 kits, one flat list (no separate category level - "category" is
        just metadata on a kit, not a navigation dimension) - enough to
        exercise wraparound on the kit list (up/down) and, within one kit,
        its own sound list (left/right). "Kit A" deliberately has a sample
        at pad_number 7 (so a target_pad=7 session gets a same-number
        default candidate) but not at pad_number 2 (so a target_pad=2
        session falls back to the first populated pad_number, 3), plus a
        third sound (10) so left/right within it has more than one
        direction to prove."""
        sample = storage.add_sample("s.wav", "S")
        storage.create_kit("Kit A", "x", 0, [
            {"pad_number": 3, "sample_id": sample, "display_name": "Three"},
            {"pad_number": 7, "sample_id": sample, "display_name": "Seven"},
            {"pad_number": 10, "sample_id": sample, "display_name": "Ten"},
        ])
        storage.create_kit("Kit B", "x", 1, [
            {"pad_number": 2, "sample_id": sample, "display_name": "B"},
        ])
        storage.create_kit("Kit C", "y", 0, [
            {"pad_number": 2, "sample_id": sample, "display_name": "C"},
        ])
        storage.create_kit("Kit D", "y", 1, [
            {"pad_number": 2, "sample_id": sample, "display_name": "D"},
        ])
        return sample

    def setUp(self):
        self._original_pad_notes = diakopad_app._pad_notes
        # Matches the DEFAULT_BASE_NOTE=36 sequential layout (pad n -> note
        # 35+n) for exactly the pads this feature cares about.
        diakopad_app._pad_notes = {
            36: [1], 37: [2], 38: [3], 39: [4], 41: [6], 42: [7],
            48: [13], 49: [14], 50: [15], 51: [16],
        }
        self._original_kit_browse_state = diakopad_app._kit_browse_state
        diakopad_app._kit_browse_state = None

    def tearDown(self):
        diakopad_app._pad_notes = self._original_pad_notes
        diakopad_app._kit_browse_state = self._original_kit_browse_state

    # --- _handle_note priority-chain wiring ---------------------------------

    async def test_normal_pad_trigger_untouched_when_idle(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with patch("app._queue_pad_hit") as queue_hit:
                diakopad_app._handle_note(36, 100)  # pad 1
                await asyncio.sleep(0)
                queue_hit.assert_called_once_with(1, 100)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_armed_tap_selects_target_not_nav_confirm_back(self):
        """While ARMED (target_pad is None), every pad - including 1/4/13-16
        - selects itself as the target instead of doing its usual
        nav/confirm/back job."""
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with (
                patch("app._kit_browse_select_target", new=AsyncMock()) as select_target,
                patch("app._kit_browse_nav", new=AsyncMock()) as nav,
                patch("app._kit_browse_confirm", new=AsyncMock()) as confirm,
                patch("app._kit_browse_back", new=AsyncMock()) as back,
                patch("app._queue_pad_hit") as queue_hit,
            ):
                diakopad_app._kit_browse_state = {"target_pad": None}

                diakopad_app._handle_note(39, 100)  # pad 4, normally "confirm"
                await asyncio.sleep(0)
                select_target.assert_awaited_once_with(4)
                nav.assert_not_awaited()
                confirm.assert_not_awaited()
                back.assert_not_awaited()
                queue_hit.assert_not_called()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_browsing_dispatches_nav_confirm_back_and_candidate_selection(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with (
                patch("app._kit_browse_nav", new=AsyncMock()) as nav,
                patch("app._kit_browse_confirm", new=AsyncMock()) as confirm,
                patch("app._kit_browse_back", new=AsyncMock()) as back,
                patch("app._kit_browse_select_candidate", new=AsyncMock()) as select_candidate,
                patch("app._queue_pad_hit") as queue_hit,
            ):
                diakopad_app._kit_browse_state = {
                    "target_pad": 1,
                    "kits": [{"id": 1, "name": "k", "pads": []}], "kit_idx": 0,
                    "candidate_pad_number": None,
                }

                diakopad_app._handle_note(48, 100)  # pad 13 -> up
                await asyncio.sleep(0)
                nav.assert_awaited_once_with("up")

                diakopad_app._handle_note(49, 100)  # pad 14 -> left
                await asyncio.sleep(0)
                nav.assert_awaited_with("left")

                diakopad_app._handle_note(50, 100)  # pad 15 -> right
                await asyncio.sleep(0)
                nav.assert_awaited_with("right")

                diakopad_app._handle_note(51, 100)  # pad 16 -> down
                await asyncio.sleep(0)
                nav.assert_awaited_with("down")

                diakopad_app._handle_note(36, 100)  # pad 1 -> back
                await asyncio.sleep(0)
                back.assert_awaited_once()

                diakopad_app._handle_note(39, 100)  # pad 4 -> confirm
                await asyncio.sleep(0)
                confirm.assert_awaited_once()

                diakopad_app._handle_note(37, 100)  # pad 2 -> select candidate
                await asyncio.sleep(0)
                select_candidate.assert_awaited_once_with(2)

                queue_hit.assert_not_called()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_kit_browse_toggle_arms_then_cancels(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            await diakopad_app._dispatch_controller_action("kit_browse_toggle")
            self.assertEqual(diakopad_app._kit_browse_state, {"target_pad": None})

            with patch("app.orchestrator.stop_preview", new=AsyncMock()) as stop_preview:
                await diakopad_app._dispatch_controller_action("kit_browse_toggle")
            self.assertIsNone(diakopad_app._kit_browse_state)
            stop_preview.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_dedicated_nav_actions_route_to_the_same_nav_confirm_back_functions(self):
        """Optional alternative to the pad-corner layout: a dedicated
        physical control learned as kit_browse_up/down/left/right/confirm/
        back must drive the exact same functions the pad corners do."""
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with (
                patch("app._kit_browse_nav", new=AsyncMock()) as nav,
                patch("app._kit_browse_confirm", new=AsyncMock()) as confirm,
                patch("app._kit_browse_back", new=AsyncMock()) as back,
            ):
                await diakopad_app._dispatch_controller_action("kit_browse_up")
                await diakopad_app._dispatch_controller_action("kit_browse_down")
                await diakopad_app._dispatch_controller_action("kit_browse_left")
                await diakopad_app._dispatch_controller_action("kit_browse_right")
                await diakopad_app._dispatch_controller_action("kit_browse_confirm")
                await diakopad_app._dispatch_controller_action("kit_browse_back")

            nav.assert_has_awaits([call("up"), call("down"), call("left"), call("right")])
            confirm.assert_awaited_once()
            back.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_dedicated_nav_actions_are_a_no_op_while_idle(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self.assertIsNone(diakopad_app._kit_browse_state)
            with patch("app.storage.assign_sample") as assign_sample:
                await diakopad_app._dispatch_controller_action("kit_browse_confirm")
                await diakopad_app._dispatch_controller_action("kit_browse_up")
            assign_sample.assert_not_called()
            self.assertIsNone(diakopad_app._kit_browse_state)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    # --- payload shape --------------------------------------------------------

    async def test_payload_lists_every_kit_and_sound_not_just_the_current_one(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                await diakopad_app._kit_browse_select_target(7)

            payload = diakopad_app._kit_browse_payload()
            self.assertEqual(
                [k["name"] for k in payload["kits"]], ["Kit A", "Kit B", "Kit C", "Kit D"]
            )
            self.assertEqual(
                payload["sounds"],
                [
                    {"pad_number": 3, "display_name": "Three"},
                    {"pad_number": 7, "display_name": "Seven"},
                    {"pad_number": 10, "display_name": "Ten"},
                ],
            )
            self.assertEqual(payload["candidate_pad_number"], 7)
            self.assertEqual(payload["sound_index"], 1)
            self.assertEqual(payload["sound_count"], 3)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    # --- select-target / nav state machine ----------------------------------

    async def test_select_target_defaults_candidate_to_same_pad_number(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)) as apply_preview:
                await diakopad_app._kit_browse_select_target(7)

            state = diakopad_app._kit_browse_state
            self.assertEqual(state["target_pad"], 7)
            self.assertEqual(state["kits"][state["kit_idx"]]["name"], "Kit A")
            self.assertEqual(state["candidate_pad_number"], 7)  # kit has pad 7
            apply_preview.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_select_target_falls_back_to_first_populated_pad(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()  # "Kit A" (first kit) has pads 3, 7 and 10, not 2
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                await diakopad_app._kit_browse_select_target(2)

            state = diakopad_app._kit_browse_state
            self.assertEqual(state["target_pad"], 2)
            self.assertEqual(state["candidate_pad_number"], 3)  # first populated, not 2
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_select_target_is_a_noop_back_to_idle_with_no_kits(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock()) as apply_preview:
                diakopad_app._kit_browse_state = {"target_pad": None}
                await diakopad_app._kit_browse_select_target(5)

            self.assertIsNone(diakopad_app._kit_browse_state)
            apply_preview.assert_not_awaited()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_nav_left_right_steps_through_the_current_kits_sounds(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)) as apply_preview:
                await diakopad_app._kit_browse_select_target(7)  # Kit A, candidate=7 (sounds: 3,7,10)

                await diakopad_app._kit_browse_nav("right")
                state = diakopad_app._kit_browse_state
                self.assertEqual(state["kits"][state["kit_idx"]]["name"], "Kit A")  # same kit
                self.assertEqual(state["candidate_pad_number"], 10)

                await diakopad_app._kit_browse_nav("right")  # wraps
                state = diakopad_app._kit_browse_state
                self.assertEqual(state["candidate_pad_number"], 3)

                await diakopad_app._kit_browse_nav("left")
                state = diakopad_app._kit_browse_state
                self.assertEqual(state["candidate_pad_number"], 10)

            # left/right never respawns the preview instance - it's the same
            # kit throughout, and apply_preview_kit already pre-loaded every
            # one of its sounds as regions in one go (see _kit_browse_nav).
            apply_preview.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_nav_up_down_wraps_through_the_flat_kit_list(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)) as apply_preview:
                await diakopad_app._kit_browse_select_target(7)  # Kit A, kit_idx 0

                await diakopad_app._kit_browse_nav("up")  # wraps to the last kit
                state = diakopad_app._kit_browse_state
                self.assertEqual(state["kit_idx"], 3)
                self.assertEqual(state["kits"][state["kit_idx"]]["name"], "Kit D")
                self.assertEqual(state["candidate_pad_number"], 2)  # Kit D's only sound

                await diakopad_app._kit_browse_nav("down")  # wraps back to the first
                state = diakopad_app._kit_browse_state
                self.assertEqual(state["kit_idx"], 0)
                self.assertEqual(state["candidate_pad_number"], 7)  # recomputed default for Kit A

            # unlike left/right, every up/down step lands on a different kit
            # and must reload the preview instance for it.
            self.assertEqual(apply_preview.await_count, 3)  # select_target + up + down
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_select_candidate_overrides_default_without_changing_kit(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                await diakopad_app._kit_browse_select_target(7)  # default candidate = 7
                with patch("app.trigger.trigger_preview", new=AsyncMock()) as preview:
                    await diakopad_app._kit_browse_select_candidate(3)  # override to the kit's other sound

            state = diakopad_app._kit_browse_state
            self.assertEqual(state["candidate_pad_number"], 3)
            self.assertEqual(state["kits"][state["kit_idx"]]["name"], "Kit A")  # unchanged
            preview.assert_awaited_once_with(3)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    # --- preview on/off ------------------------------------------------------

    async def test_preview_enabled_calls_trigger_preview_on_candidate_change(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            storage.set_setting("kit_browse_preview_enabled", "1")
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                with patch("app.trigger.trigger_preview", new=AsyncMock()) as preview:
                    await diakopad_app._kit_browse_select_target(7)
            preview.assert_awaited_once_with(7)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_preview_disabled_never_calls_trigger_preview_but_state_still_works(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            storage.set_setting("kit_browse_preview_enabled", "0")
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                with patch("app.trigger.trigger_preview", new=AsyncMock()) as preview:
                    await diakopad_app._kit_browse_select_target(7)
                    await diakopad_app._kit_browse_nav("up")  # wraps to Kit D
                    await diakopad_app._kit_browse_select_candidate(2)
            preview.assert_not_awaited()

            state = diakopad_app._kit_browse_state
            self.assertEqual(state["kits"][state["kit_idx"]]["name"], "Kit D")
            self.assertEqual(state["candidate_pad_number"], 2)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    # --- confirm/back ---------------------------------------------------------

    async def test_confirm_only_touches_the_target_pad(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            sample_a = storage.add_sample("a.wav", "A")
            storage.assign_sample(9, None)

            kit_id = storage.create_kit("Test Kit", "01_electronic", 0, [
                {"pad_number": 3, "sample_id": sample_a, "display_name": "A"},
            ])
            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [storage.get_kit(kit_id)], "kit_idx": 0,
                "candidate_pad_number": 3,
            }

            with (
                patch("app.orchestrator.apply_pad", new=AsyncMock(return_value=True)) as apply_pad,
                patch("app.orchestrator.stop_preview", new=AsyncMock()) as stop_preview,
            ):
                await diakopad_app._kit_browse_confirm()

            applied_pad_numbers = {call.args[0] for call in apply_pad.await_args_list}
            self.assertEqual(applied_pad_numbers, {9})  # only the target pad, never another
            stop_preview.assert_awaited_once()
            self.assertIsNone(diakopad_app._kit_browse_state)

            pads_by_number = {p["pad_number"]: p for p in storage.list_pads()}
            self.assertEqual(pads_by_number[9]["sample_id"], sample_a)
            self.assertEqual(storage.get_settings()["kit_browse_last_kit_id"], str(kit_id))
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_confirm_with_candidate_kit_has_no_sample_for_clears_target_pad(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            sample_a = storage.add_sample("a.wav", "A")
            storage.assign_sample(9, sample_a)

            kit_id = storage.create_kit("Empty-ish Kit", "01_electronic", 0, [])
            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [storage.get_kit(kit_id)], "kit_idx": 0,
                "candidate_pad_number": None,
            }

            with (
                patch("app.orchestrator.apply_pad", new=AsyncMock(return_value=True)),
                patch("app.orchestrator.stop_preview", new=AsyncMock()),
            ):
                await diakopad_app._kit_browse_confirm()

            pads_by_number = {p["pad_number"]: p for p in storage.list_pads()}
            self.assertIsNone(pads_by_number[9]["sample_id"])
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_back_leaves_live_pads_completely_unchanged_from_armed(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            sample_a = storage.add_sample("a.wav", "A")
            storage.assign_sample(2, sample_a)
            before = storage.list_pads()

            diakopad_app._kit_browse_state = {"target_pad": None}

            with patch("app.orchestrator.stop_preview", new=AsyncMock()) as stop_preview:
                await diakopad_app._kit_browse_back()

            stop_preview.assert_awaited_once()
            self.assertIsNone(diakopad_app._kit_browse_state)
            self.assertEqual(storage.list_pads(), before)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_back_leaves_live_pads_completely_unchanged_from_browsing(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            sample_a = storage.add_sample("a.wav", "A")
            storage.assign_sample(2, sample_a)
            before = storage.list_pads()

            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [{"id": 1, "name": "k", "pads": []}], "kit_idx": 0,
                "candidate_pad_number": None,
            }

            with patch("app.orchestrator.stop_preview", new=AsyncMock()) as stop_preview:
                await diakopad_app._kit_browse_back()

            stop_preview.assert_awaited_once()
            self.assertIsNone(diakopad_app._kit_browse_state)
            self.assertEqual(storage.list_pads(), before)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    # --- last-used-kit memory --------------------------------------------------

    async def test_confirming_remembers_the_kit_for_the_next_session(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                await diakopad_app._kit_browse_select_target(9)
                await diakopad_app._kit_browse_nav("down")  # move to "Kit B"
                remembered_kit_id = diakopad_app._kit_browse_state["kits"][diakopad_app._kit_browse_state["kit_idx"]]["id"]

                with (
                    patch("app.orchestrator.apply_pad", new=AsyncMock(return_value=True)),
                    patch("app.orchestrator.stop_preview", new=AsyncMock()),
                ):
                    await diakopad_app._kit_browse_confirm()

                # A brand new session, for a different pad, should open on
                # the kit that was just confirmed rather than the first kit
                # overall.
                await diakopad_app._kit_browse_select_target(10)

            state = diakopad_app._kit_browse_state
            self.assertEqual(state["target_pad"], 10)
            self.assertEqual(state["kits"][state["kit_idx"]]["id"], remembered_kit_id)
            self.assertEqual(state["kits"][state["kit_idx"]]["name"], "Kit B")
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


class KitBrowseEndpointTests(unittest.IsolatedAsyncioTestCase):
    """The /api/kit-browse/* endpoints exist purely so the touchscreen can
    exercise this feature without a physical SMC-PAD - see Phase 5 of the
    plan. Mirrors KitBrowseTests's temp-DB fixture."""

    def _with_temp_db(self):
        temp_ctx = tempfile.TemporaryDirectory()
        original_db_path = storage.DB_PATH
        storage.DB_PATH = Path(temp_ctx.name) / "test.db"
        storage.init_db()
        return temp_ctx, original_db_path

    def _seed_kits(self):
        sample = storage.add_sample("s.wav", "S")
        storage.create_kit("Electronic A", "01_electronic", 0, [
            {"pad_number": 2, "sample_id": sample, "display_name": "A"},
        ])
        storage.create_kit("Electronic B", "01_electronic", 1, [
            {"pad_number": 2, "sample_id": sample, "display_name": "B"},
        ])
        return sample

    def setUp(self):
        self._original_kit_browse_state = diakopad_app._kit_browse_state
        diakopad_app._kit_browse_state = None

    def tearDown(self):
        diakopad_app._kit_browse_state = self._original_kit_browse_state

    async def test_toggle_arms_then_cancels(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            payload = await diakopad_app.kit_browse_toggle()
            self.assertTrue(payload["active"])
            self.assertEqual(payload["phase"], "armed")
            self.assertIsNotNone(diakopad_app._kit_browse_state)

            with patch("app.orchestrator.stop_preview", new=AsyncMock()) as stop_preview:
                payload = await diakopad_app.kit_browse_toggle()
            self.assertFalse(payload["active"])
            self.assertIsNone(diakopad_app._kit_browse_state)
            stop_preview.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_select_target_returns_409_unless_armed(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_select_target(diakopad_app.KitBrowseSelectTargetRequest(pad_number=5))
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_select_target_endpoint_enters_browsing(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            await diakopad_app.kit_browse_toggle()  # arm
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                payload = await diakopad_app.kit_browse_select_target(diakopad_app.KitBrowseSelectTargetRequest(pad_number=5))
            self.assertEqual(payload["phase"], "browsing")
            self.assertEqual(payload["target_pad"], 5)
            self.assertEqual(payload["kit"]["name"], "Electronic A")
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_select_target_rejects_out_of_range_pad(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            await diakopad_app.kit_browse_toggle()  # arm
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_select_target(diakopad_app.KitBrowseSelectTargetRequest(pad_number=99))
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_nav_confirm_back_preview_return_409_when_idle(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_nav(diakopad_app.KitBrowseNavRequest(direction="left"))
            self.assertEqual(ctx.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_confirm_endpoint()
            self.assertEqual(ctx.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_back_endpoint()
            self.assertEqual(ctx.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_preview(2)
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_nav_confirm_preview_return_409_while_only_armed(self):
        """Armed-but-no-target-yet is "not None" but must still be rejected
        by the browsing-only endpoints (only select-target/back accept it)."""
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            await diakopad_app.kit_browse_toggle()  # arm, no target yet

            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_nav(diakopad_app.KitBrowseNavRequest(direction="left"))
            self.assertEqual(ctx.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_confirm_endpoint()
            self.assertEqual(ctx.exception.status_code, 409)

            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_preview(2)
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_nav_rejects_unknown_direction(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            await diakopad_app.kit_browse_toggle()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                await diakopad_app.kit_browse_select_target(diakopad_app.KitBrowseSelectTargetRequest(pad_number=5))
                with self.assertRaises(HTTPException) as ctx:
                    await diakopad_app.kit_browse_nav(diakopad_app.KitBrowseNavRequest(direction="sideways"))
                self.assertEqual(ctx.exception.status_code, 400)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_nav_moves_to_next_kit_while_browsing(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            self._seed_kits()
            await diakopad_app.kit_browse_toggle()
            with patch("app.orchestrator.apply_preview_kit", new=AsyncMock(return_value=True)):
                await diakopad_app.kit_browse_select_target(diakopad_app.KitBrowseSelectTargetRequest(pad_number=5))
                payload = await diakopad_app.kit_browse_nav(diakopad_app.KitBrowseNavRequest(direction="down"))
            self.assertEqual(payload["kit"]["name"], "Electronic B")
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_confirm_endpoint_applies_only_target_pad_and_exits(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            sample_a = storage.add_sample("a.wav", "A")
            kit_id = storage.create_kit("Test Kit", "01_electronic", 0, [
                {"pad_number": 2, "sample_id": sample_a, "display_name": "A"},
            ])
            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [storage.get_kit(kit_id)], "kit_idx": 0,
                "candidate_pad_number": 2,
            }
            with (
                patch("app.orchestrator.apply_pad", new=AsyncMock(return_value=True)),
                patch("app.orchestrator.stop_preview", new=AsyncMock()),
            ):
                payload = await diakopad_app.kit_browse_confirm_endpoint()
            self.assertFalse(payload["active"])
            self.assertIsNone(diakopad_app._kit_browse_state)
            pads_by_number = {p["pad_number"]: p for p in storage.list_pads()}
            self.assertEqual(pads_by_number[9]["sample_id"], sample_a)
            self.assertIsNone(pads_by_number[2]["sample_id"])  # untouched
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_back_endpoint_exits_without_changes(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [{"id": 1, "name": "k", "pads": []}], "kit_idx": 0,
                "candidate_pad_number": None,
            }
            with patch("app.orchestrator.stop_preview", new=AsyncMock()) as stop_preview:
                payload = await diakopad_app.kit_browse_back_endpoint()
            self.assertFalse(payload["active"])
            stop_preview.assert_awaited_once()
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_preview_endpoint_selects_candidate_while_browsing(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [{"id": 1, "name": "k", "pads": []}], "kit_idx": 0,
                "candidate_pad_number": None,
            }
            with patch("app.trigger.trigger_preview", new=AsyncMock(return_value=True)) as preview:
                result = await diakopad_app.kit_browse_preview(5)
            preview.assert_not_awaited()  # kit has no sample at pad 5 - gated silent
            self.assertEqual(result["candidate_pad_number"], 5)
            self.assertEqual(diakopad_app._kit_browse_state["candidate_pad_number"], 5)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_preview_endpoint_rejects_out_of_range_pad(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            diakopad_app._kit_browse_state = {
                "target_pad": 9,
                "kits": [{"id": 1, "name": "k", "pads": []}], "kit_idx": 0,
                "candidate_pad_number": None,
            }
            with self.assertRaises(HTTPException) as ctx:
                await diakopad_app.kit_browse_preview(99)
            self.assertEqual(ctx.exception.status_code, 400)
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()

    async def test_set_kit_browse_preview_setting(self):
        temp_ctx, original_db_path = self._with_temp_db()
        try:
            result = await diakopad_app.set_kit_browse_preview(diakopad_app.KitBrowsePreviewRequest(enabled=False))
            self.assertEqual(result, {"ok": True})
            self.assertEqual(storage.get_settings()["kit_browse_preview_enabled"], "0")

            await diakopad_app.set_kit_browse_preview(diakopad_app.KitBrowsePreviewRequest(enabled=True))
            self.assertEqual(storage.get_settings()["kit_browse_preview_enabled"], "1")
        finally:
            storage.DB_PATH = original_db_path
            temp_ctx.cleanup()


if __name__ == "__main__":
    unittest.main()
