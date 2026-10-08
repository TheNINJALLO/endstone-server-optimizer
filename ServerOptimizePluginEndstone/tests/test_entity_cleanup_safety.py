from __future__ import annotations

import types
import unittest
from unittest.mock import Mock, patch

from test_view_distance_safety import MODULE, _new_plugin


def _actor(runtime_id: int, actor_type: str = "minecraft:item", name: str = ""):
    return types.SimpleNamespace(
        runtime_id=runtime_id, type=actor_type, name_tag=name, remove=Mock()
    )


class EntityCleanupSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = patch.object(MODULE.time, "time", return_value=1000.0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.plugin = _new_plugin()
        self.plugin.logger = Mock()
        self.plugin.load_config = Mock()
        self.plugin.on_load()
        self.player = types.SimpleNamespace(send_message=Mock())
        self.plugin.server = types.SimpleNamespace(
            online_players=[self.player],
            level=types.SimpleNamespace(actors=[], dimensions=[]),
            scheduler=types.SimpleNamespace(run_task=Mock()),
        )
        self.plugin.calculate_tps = Mock(return_value=20.0)
        self.plugin.get_total_loaded_chunks = Mock(return_value=25)
        self.plugin.optimize_memory = Mock()

    def messages(self) -> list[str]:
        return [call.args[0] for call in self.player.send_message.call_args_list]

    def test_enable_starts_full_intervals_and_does_not_purge_on_first_tick(self) -> None:
        self.plugin.register_events = Mock()
        self.now.return_value = 2000
        self.plugin.on_enable()
        self.now.return_value = 2005

        self.plugin.auto_optimize_task()
        self.plugin.hourly_entity_purge_check()

        self.assertEqual(self.plugin.last_hourly_purge, 2000)
        self.assertEqual(self.plugin.last_optimization, 2000)
        self.assertIsNone(self.plugin.pending_entity_cleanup)
        self.assertEqual(self.messages(), [])

    def test_hourly_cleanup_warns_runs_then_reports_actual_removals(self) -> None:
        item = _actor(1)
        self.plugin.server.level.actors = [item]
        self.plugin.item_spawn_times[1] = 1000
        self.now.return_value = 4570
        self.plugin.hourly_entity_purge_check()
        self.assertIn("30 seconds", self.messages()[-1])
        item.remove.assert_not_called()

        self.now.return_value = 4595
        self.plugin.hourly_entity_purge_check()
        self.assertIn("5 seconds", self.messages()[-1])
        item.remove.assert_not_called()

        self.now.return_value = 4600
        self.plugin.hourly_entity_purge_check()
        item.remove.assert_called_once_with()
        self.assertIn("is running", self.messages()[-2])
        self.assertIn("cleared 1 items", self.messages()[-1])
        self.assertEqual(self.plugin.entities_removed_total, 1)
        self.assertEqual(self.plugin.total_optimizations, 1)
        self.assertEqual(self.plugin.last_hourly_purge, 4600)
        self.assertIsNone(self.plugin.pending_entity_cleanup)
        self.plugin.hourly_entity_purge_check()
        self.assertEqual(item.remove.call_count, 1)

    def test_overdue_hourly_cleanup_still_gives_full_warning(self) -> None:
        self.now.return_value = 9999
        self.plugin.hourly_entity_purge_check()
        self.assertIn("30 seconds", self.messages()[-1])
        self.assertEqual(self.plugin.pending_entity_cleanup["execute_at"], 10029)
        self.assertEqual(self.plugin.entities_removed_total, 0)

    def test_server_stall_cannot_skip_final_five_seconds(self) -> None:
        self.plugin.request_entity_cleanup("Test cleanup")
        self.plugin.optimize_entities = Mock(return_value=0)
        self.now.return_value = 1100
        self.plugin.hourly_entity_purge_check()
        self.assertIn("5 seconds", self.messages()[-1])
        self.plugin.optimize_entities.assert_not_called()
        self.now.return_value = 1104
        self.plugin.hourly_entity_purge_check()
        self.plugin.optimize_entities.assert_not_called()
        self.now.return_value = 1105
        self.plugin.hourly_entity_purge_check()
        self.plugin.optimize_entities.assert_called_once_with()

    def test_low_tps_cleanup_is_queued_and_rate_limited(self) -> None:
        self.plugin.calculate_tps.return_value = 14
        self.plugin.optimize_entities = Mock(return_value=3)
        self.now.return_value = 1020
        self.plugin.auto_optimize_task()
        self.assertIsNone(self.plugin.pending_entity_cleanup)
        self.now.return_value = 1060
        self.plugin.auto_optimize_task()
        self.assertIn("Low TPS cleanup", self.messages()[-1])
        self.plugin.auto_optimize_task()
        self.assertEqual(len(self.messages()), 1)
        self.plugin.optimize_entities.assert_not_called()
        self.now.return_value = 1085
        self.plugin.hourly_entity_purge_check()
        self.now.return_value = 1090
        self.plugin.hourly_entity_purge_check()
        self.assertIn("cleared 3 excess entities", self.messages()[-1])
        self.assertEqual(self.plugin.last_optimization, 1090)

    def test_emergency_cleanup_warns_and_does_not_repeat_restoration_tasks(self) -> None:
        self.plugin.optimize_entities = Mock()
        self.plugin.emergency_crash_recovery()
        self.plugin.emergency_crash_recovery()
        self.assertTrue(self.plugin.aggressive_mode)
        self.plugin.optimize_entities.assert_not_called()
        self.plugin.optimize_memory.assert_called_once_with()
        self.assertIn("Emergency lag cleanup", self.messages()[0])
        self.plugin.server.scheduler.run_task.assert_called_once()

    def test_disabled_optimization_prevents_every_automatic_trigger(self) -> None:
        self.plugin.auto_optimize = False
        self.now.return_value = 9999
        self.plugin.calculate_tps.return_value = 1
        self.plugin.auto_optimize_task()
        self.plugin.hourly_entity_purge_check()
        self.plugin.fast_optimization_check()
        self.plugin.emergency_crash_recovery()
        self.assertIsNone(self.plugin.pending_entity_cleanup)
        self.assertFalse(self.plugin.aggressive_mode)
        self.assertEqual(self.messages(), [])

    def test_disabling_optimization_cancels_pending_cleanup(self) -> None:
        self.plugin.request_entity_cleanup("Test cleanup")
        self.plugin.auto_optimize = False
        self.now.return_value = 2000
        self.plugin.optimize_entities = Mock()
        self.plugin.hourly_entity_purge_check()
        self.assertIsNone(self.plugin.pending_entity_cleanup)
        self.assertIn("cancelled", self.messages()[-1])
        self.plugin.optimize_entities.assert_not_called()

    def test_zero_interval_disables_hourly_purge(self) -> None:
        self.plugin.hourly_purge_interval = 0
        self.now.return_value = 9999
        self.plugin.hourly_entity_purge_check()
        self.assertIsNone(self.plugin.pending_entity_cleanup)

    def test_fresh_and_unobserved_items_survive_full_cleanup(self) -> None:
        old, fresh, unobserved = _actor(1), _actor(2), _actor(3)
        self.plugin.server.level.actors = [old, fresh, unobserved]
        self.plugin.item_spawn_times.update({1: 900, 2: 980})

        stats = self.plugin.hourly_entity_purge()

        old.remove.assert_called_once_with()
        fresh.remove.assert_not_called()
        unobserved.remove.assert_not_called()
        self.assertEqual(stats["items_cleared"], 1)
        self.assertEqual(stats["items_total"], 3)
        self.assertEqual(stats["chunks_cleared"], 0)
        self.assertEqual(stats["chunks_total"], 25)

    def test_fresh_items_survive_limit_cleanup_in_normal_and_aggressive_modes(self) -> None:
        for aggressive in (False, True):
            with self.subTest(aggressive=aggressive):
                self.plugin.aggressive_mode = aggressive
                items = [_actor(index) for index in range(202)]
                self.plugin.server.level.actors = items
                self.plugin.entity_limits["item"] = 200
                self.plugin.item_spawn_times = {index: 999 for index in range(202)}
                self.assertEqual(self.plugin.optimize_entities(), 0)
                self.assertTrue(all(item.remove.call_count == 0 for item in items))

    def test_grace_period_expiry_allows_older_excess_item_removal(self) -> None:
        item = _actor(1)
        self.plugin.server.level.actors = [item]
        self.plugin.entity_limits["item"] = 0
        self.plugin.item_spawn_times[1] = 1000
        self.now.return_value = 1059
        self.assertEqual(self.plugin.optimize_entities(), 0)
        self.now.return_value = 1060
        self.assertEqual(self.plugin.optimize_entities(), 1)
        item.remove.assert_called_once_with()

    def test_item_spawn_is_never_cancelled_even_over_dimension_limit(self) -> None:
        item = _actor(1)
        item.dimension = types.SimpleNamespace(actors=[None] * 2001)
        event = types.SimpleNamespace(actor=item, is_cancelled=False)
        self.plugin.on_actor_spawn(event)
        self.assertFalse(event.is_cancelled)
        self.assertEqual(self.plugin.item_spawn_times[1], 1000)
        self.assertIn("Entity buildup cleanup", self.messages()[0])
        item.remove.assert_not_called()

    def test_spawn_tracking_continues_when_automatic_cleanup_is_disabled(self) -> None:
        self.plugin.auto_optimize = False
        self.plugin.on_actor_spawn(types.SimpleNamespace(actor=_actor(1), is_cancelled=False))
        self.assertEqual(self.plugin.item_spawn_times[1], 1000)
        self.assertIsNone(self.plugin.pending_entity_cleanup)

    def test_removed_item_tracking_is_released(self) -> None:
        self.plugin.item_spawn_times[1] = 900
        self.plugin.on_actor_remove(types.SimpleNamespace(actor=_actor(1)))
        self.assertNotIn(1, self.plugin.item_spawn_times)

    def test_full_cleanup_preserves_frames_names_whitelist_and_vehicles(self) -> None:
        self.plugin.entity_whitelist = ["custom:decoration"]
        actors = [
            _actor(1, "minecraft:item_frame"),
            _actor(2, "minecraft:glow_item_frame"),
            _actor(3, "minecraft:zombie", "Pet"),
            _actor(4, "custom:decoration"),
            _actor(5, "minecraft:boat"),
            _actor(6, "minecraft:minecart"),
            _actor(7, "minecraft:player"),
        ]
        self.plugin.server.level.actors = actors
        self.plugin.hourly_entity_purge()
        self.assertTrue(all(actor.remove.call_count == 0 for actor in actors))

    def test_limit_cleanup_does_not_classify_item_frames_as_drops(self) -> None:
        actors = [_actor(1, "minecraft:item_frame"), _actor(2, "minecraft:glow_item_frame")]
        self.plugin.server.level.actors = actors
        self.plugin.entity_limits["item"] = 0
        self.plugin.item_spawn_times = {1: 900, 2: 900}
        self.assertEqual(self.plugin.optimize_entities(), 0)
        self.assertTrue(all(actor.remove.call_count == 0 for actor in actors))

    def test_one_failed_player_notification_does_not_hide_warnings_from_others(self) -> None:
        disconnected = types.SimpleNamespace(send_message=Mock(side_effect=RuntimeError("disconnected")))
        self.plugin.server.online_players.insert(0, disconnected)
        self.plugin.request_entity_cleanup("Test cleanup")
        self.assertIn("30 seconds", self.messages()[0])
        self.plugin.logger.warning.assert_called_once()

    def test_failed_removal_is_not_reported_as_success(self) -> None:
        item = _actor(1)
        item.remove.side_effect = RuntimeError("already removed")
        self.plugin.server.level.actors = [item]
        self.plugin.item_spawn_times[1] = 900
        self.plugin.entity_limits["item"] = 0
        self.assertEqual(self.plugin.optimize_entities(), 0)
        self.assertEqual(self.plugin.entities_removed_total, 0)

    def test_level_access_failure_does_not_invent_removal_statistics(self) -> None:
        class BrokenServer:
            @property
            def level(self):
                raise RuntimeError("level unavailable")

        self.plugin.server = BrokenServer()
        self.assertEqual(self.plugin.optimize_entities(), 0)
        self.assertEqual(self.plugin.entities_removed_total, 0)

    def test_manual_cleanup_broadcasts_and_resets_automatic_timers(self) -> None:
        self.plugin.request_entity_cleanup("Scheduled limit cleanup")
        sender = types.SimpleNamespace(
            has_permission=Mock(return_value=True), send_message=Mock(), send_error_message=Mock()
        )
        self.now.return_value = 1100
        self.assertTrue(self.plugin.handle_optimize_command(sender, ["full"]))
        self.assertIn("Manual entity cleanup is running", self.messages()[-2])
        self.assertIn("Manual entity cleanup complete", self.messages()[-1])
        self.assertIsNone(self.plugin.pending_entity_cleanup)
        self.assertEqual(self.plugin.last_hourly_purge, 1100)
        self.assertEqual(self.plugin.last_optimization, 1100)


if __name__ == "__main__":
    unittest.main()
