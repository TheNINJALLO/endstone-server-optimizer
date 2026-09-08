from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "server_optimizer"
    / "server_optimizer_plugin.py"
)


class _EventPriority:
    MONITOR = object()
    HIGHEST = object()


def _event_handler(function=None, **_kwargs):
    if function is None:
        return lambda decorated: decorated
    return function


def _install_endstone_stubs() -> None:
    endstone = types.ModuleType("endstone")
    command = types.ModuleType("endstone.command")
    event = types.ModuleType("endstone.event")
    plugin = types.ModuleType("endstone.plugin")

    class _Stub:
        pass

    endstone.Player = _Stub
    command.Command = _Stub
    command.CommandSender = _Stub
    plugin.Plugin = _Stub
    event.EventPriority = _EventPriority
    event.event_handler = _event_handler
    for name in (
        "ServerLoadEvent",
        "PlayerJoinEvent",
        "PlayerQuitEvent",
        "PlayerMoveEvent",
        "ActorSpawnEvent",
        "PacketSendEvent",
    ):
        setattr(event, name, _Stub)

    sys.modules.update(
        {
            "endstone": endstone,
            "endstone.command": command,
            "endstone.event": event,
            "endstone.plugin": plugin,
        }
    )


_install_endstone_stubs()
SPEC = importlib.util.spec_from_file_location("server_optimizer_under_test", SOURCE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ServerOptimizerPlugin = MODULE.ServerOptimizerPlugin


def _new_plugin():
    return object.__new__(ServerOptimizerPlugin)


class ViewDistanceSafetyTests(unittest.TestCase):
    def test_source_has_no_runtime_view_distance_packet_path(self) -> None:
        source = SOURCE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("PACKET_ID_CHUNK_RADIUS_UPDATED", source)
        self.assertNotIn("send_view_distance_to_player", source)
        self.assertNotIn("send_view_distance_to_all", source)
        self.assertNotIn("adjust_view_distance", source)
        self.assertNotIn(".send_packet(", source)

    def test_legacy_auto_view_distance_is_always_disabled(self) -> None:
        plugin = _new_plugin()
        plugin.default_config = {
            "auto_optimize": True,
            "optimization_interval": 900,
            "hourly_purge_interval": 3600,
            "tps_target": 19.0,
            "tps_warning": 16.0,
            "tps_critical": 13.0,
            "auto_view_distance": False,
            "base_view_distance": 12,
            "min_view_distance": 6,
            "max_view_distance": 32,
            "lag_alert_cooldown": 60,
            "afk_threshold": 180,
            "max_players_warning": 80,
            "max_players_critical": 100,
            "max_chunks_warning": 30000,
            "max_chunks_critical": 50000,
            "entity_limits": {"item": 900},
            "entity_whitelist": [],
        }

        plugin.apply_config({"auto_view_distance": True})

        self.assertFalse(plugin.auto_view_distance)

    def test_non_admin_join_schedules_no_task_or_packet(self) -> None:
        plugin = _new_plugin()
        plugin.player_last_move = {}
        plugin.server = types.SimpleNamespace(
            scheduler=types.SimpleNamespace(run_task=Mock())
        )
        player = types.SimpleNamespace(
            name="PlayerOne",
            is_op=False,
            has_permission=Mock(return_value=False),
        )

        plugin.on_player_join(types.SimpleNamespace(player=player))

        self.assertIn("PlayerOne", plugin.player_last_move)
        plugin.server.scheduler.run_task.assert_not_called()

    def test_write_updates_existing_property_atomically(self) -> None:
        plugin = _new_plugin()
        with tempfile.TemporaryDirectory() as temporary_directory:
            properties_path = Path(temporary_directory) / "server.properties"
            properties_path.write_bytes(
                b"# Bedrock settings\r\nserver-name=Test\r\nview-distance=12\r\n"
            )
            plugin.get_server_properties_path = lambda: properties_path

            success, _detail = plugin.write_server_view_distance(18)

            self.assertTrue(success)
            self.assertEqual(
                properties_path.read_bytes(),
                b"# Bedrock settings\r\nserver-name=Test\r\nview-distance=18\r\n",
            )
            self.assertEqual(plugin.read_server_view_distance(), 18)
            self.assertEqual(list(properties_path.parent.glob(".server.properties.*.tmp")), [])

    def test_write_appends_missing_property(self) -> None:
        plugin = _new_plugin()
        with tempfile.TemporaryDirectory() as temporary_directory:
            properties_path = Path(temporary_directory) / "server.properties"
            properties_path.write_text("server-name=Test\n", encoding="utf-8")
            plugin.get_server_properties_path = lambda: properties_path

            success, _detail = plugin.write_server_view_distance(10)

            self.assertTrue(success)
            self.assertEqual(
                properties_path.read_text(encoding="utf-8"),
                "server-name=Test\nview-distance=10\n",
            )

    def test_write_rejects_invalid_distance(self) -> None:
        plugin = _new_plugin()
        plugin.get_server_properties_path = Mock()

        success, detail = plugin.write_server_view_distance(4)

        self.assertFalse(success)
        self.assertIn("between 5 and 32", detail)
        plugin.get_server_properties_path.assert_not_called()

    def test_command_changes_file_only_and_requires_restart(self) -> None:
        plugin = _new_plugin()
        plugin.write_server_view_distance = Mock(return_value=(True, "server.properties"))
        plugin.save_config = Mock(return_value=True)
        plugin.current_view_distance = 12
        plugin.base_view_distance = 12
        plugin.auto_view_distance = True
        sender = types.SimpleNamespace(
            has_permission=Mock(return_value=True),
            send_message=Mock(),
            send_error_message=Mock(),
        )

        result = plugin.handle_viewdistance_command(sender, ["16"])

        self.assertTrue(result)
        plugin.write_server_view_distance.assert_called_once_with(16)
        self.assertEqual(plugin.current_view_distance, 16)
        self.assertEqual(plugin.base_view_distance, 16)
        self.assertFalse(plugin.auto_view_distance)
        messages = " ".join(call.args[0] for call in sender.send_message.call_args_list)
        self.assertIn("Restart", messages)
        self.assertIn("were not modified", messages)

    def test_auto_command_cannot_enable_runtime_adjustment(self) -> None:
        plugin = _new_plugin()
        plugin.auto_view_distance = True
        plugin.save_config = Mock(return_value=True)
        sender = types.SimpleNamespace(
            has_permission=Mock(return_value=True),
            send_message=Mock(),
            send_error_message=Mock(),
        )

        result = plugin.handle_viewdistance_command(sender, ["auto"])

        self.assertTrue(result)
        self.assertFalse(plugin.auto_view_distance)
        plugin.save_config.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
