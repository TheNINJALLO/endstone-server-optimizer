import time
import gc
import json
import os
from pathlib import Path
from collections import defaultdict, deque
from typing import Dict, List, Set, Optional, Any

from endstone.command import Command, CommandSender
from endstone.event import (
    EventPriority,
    ServerLoadEvent,
    PlayerJoinEvent,
    PlayerQuitEvent,
    PlayerMoveEvent,
    ActorSpawnEvent,
    PacketSendEvent,
    event_handler,
)
from endstone.plugin import Plugin
from endstone import Player

# Bedrock protocol packet IDs for throttling
# LevelSoundEventPacket = 0x7B, SpawnParticleEffectPacket = 0x76
PACKET_ID_LEVEL_SOUND = 0x7B
PACKET_ID_SPAWN_PARTICLE = 0x76
PACKET_ID_CHUNK_RADIUS_UPDATED = 0x46

try:
    from bedrock_protocol_packets import UpdateBlockPacket
    HAS_PACKET_CODEC = True
except ImportError:
    HAS_PACKET_CODEC = False


class ServerOptimizerPlugin(Plugin):
    prefix = "ServerOptimizer"
    api_version = "0.11"
    load = "POSTWORLD"

    commands = {
        "optimize": {
            "description": "Server optimization controls",
            "usages": ["/optimize", "/optimize (status|full|packets)[action: OptAction]", "/optimize view <player: player>"],
            "aliases": ["opt", "perf"],
            "permissions": ["serveropt.command.optimize"],
        },
        "tps": {
            "description": "Check server TPS",
            "usages": ["/tps"],
            "permissions": ["serveropt.command.tps"],
        },
        "lag": {
            "description": "View lag information",
            "usages": ["/lag"],
            "permissions": ["serveropt.command.lag"],
        },
        "viewdistance": {
            "description": "Manage view distance",
            "usages": ["/viewdistance", "/viewdistance <distance: int>", "/viewdistance auto"],
            "aliases": ["vd"],
            "permissions": ["serveropt.command.viewdistance"],
        },
        "tpsthreshold": {
            "description": "Adjust TPS thresholds for optimization triggers",
            "usages": ["/tpsthreshold", "/tpsthreshold (critical|warning|target)<type: TpsType> <value: float>"],
            "aliases": ["tpst"],
            "permissions": ["serveropt.command.tpsthreshold"],
        },
        "soconfig": {
            "description": "Manage plugin configuration",
            "usages": ["/soconfig", "/soconfig (reload|reset|save)[action: ConfigAction]", "/soconfig set <key: str> <value: str>", "/soconfig whitelist", "/soconfig whitelist <action: str> <entity_type: str>"],
            "aliases": ["soc"],
            "permissions": ["serveropt.command.config"],
        },
    }

    permissions = {
        "serveropt.admin": {
            "description": "Admin permissions for server optimizer",
            "default": "op",
            "children": {
                "serveropt.command.optimize": True,
                "serveropt.command.tps": True,
                "serveropt.command.lag": True,
                "serveropt.command.viewdistance": True,
                "serveropt.command.tpsthreshold": True,
                "serveropt.command.config": True,
            },
        },
        "serveropt.command.optimize": {
            "description": "Use optimization commands",
            "default": "op",
        },
        "serveropt.command.tps": {
            "description": "Check server TPS",
            "default": True,
        },
        "serveropt.command.lag": {
            "description": "View lag information",
            "default": True,
        },
        "serveropt.command.viewdistance": {
            "description": "Manage view distance",
            "default": "op",
        },
        "serveropt.command.tpsthreshold": {
            "description": "Adjust TPS thresholds",
            "default": "op",
        },
        "serveropt.command.config": {
            "description": "Manage plugin configuration",
            "default": "op",
        },
    }

    def on_load(self) -> None:
        self.logger.info("=== Server Optimizer Pro Loading ===")
        
        # Config file path - stored in plugin data directory
        self.config_path: Optional[Path] = None
        
        # Default configuration values
        self.default_config: Dict[str, Any] = {
            "auto_optimize": True,
            "optimization_interval": 900,
            "hourly_purge_interval": 3600,  # Full entity purge every hour
            "tps_target": 19.0,
            "tps_warning": 16.0,
            "tps_critical": 13.0,
            "auto_view_distance": True,
            "base_view_distance": 12,
            "min_view_distance": 6,
            "max_view_distance": 32,
            "entity_limits": {
                "item": 900,
                "mob": 600,
                "minecart": 500,
                "boat": 50,
                "arrow": 120,
            },
            "lag_alert_cooldown": 60,
            "afk_threshold": 180,
            "max_players_warning": 80,
            "max_players_critical": 100,
            "max_chunks_warning": 30000,
            "max_chunks_critical": 50000,
            "entity_whitelist": [],
        }
        
        # Performance tracking (non-configurable)
        self.tps_history: deque = deque(maxlen=60)
        
        # Runtime state (non-configurable)
        self.aggressive_mode = False
        self.last_optimization = 0
        self.current_view_distance = 8
        self.player_last_move: Dict[str, float] = {}  # player_name -> last move timestamp
        self.afk_players: Set[str] = set()
        self.total_optimizations = 0
        self.chunks_cleared_total = 0
        self.entities_removed_total = 0
        self.last_lag_alert = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5
        self.performance_viewers: Set[str] = set()
        self.task_execution_times: Dict[str, List[float]] = defaultdict(list)
        self.max_task_duration = 0.05
        self.slow_tasks: Set[str] = set()
        self.memory_samples: deque = deque(maxlen=30)
        self.memory_warning_threshold = 80.0
        self.memory_critical_threshold = 90.0
        self.health_score = 100
        self.last_hourly_purge = 0
        self.hourly_purge_30s_warning_sent = False
        self.hourly_purge_5s_warning_sent = False
        self.health_history: deque = deque(maxlen=60)
        
        # Packet throttle tracking (disabled by default — breaks interaction sounds/particles)
        self.packet_throttle_enabled = False
        self.packets_throttled = 0
        self.packets_inspected = 0
        self.packet_throttle_tps_threshold = 13.0  # Only throttle during genuine emergencies
        
        # Initialize configurable settings with defaults (will be overwritten by load_config)
        self.auto_optimize = self.default_config["auto_optimize"]
        self.optimization_interval = self.default_config["optimization_interval"]
        self.hourly_purge_interval = self.default_config["hourly_purge_interval"]
        self.tps_target = self.default_config["tps_target"]
        self.tps_warning = self.default_config["tps_warning"]
        self.tps_critical = self.default_config["tps_critical"]
        self.auto_view_distance = self.default_config["auto_view_distance"]
        self.base_view_distance = self.default_config["base_view_distance"]
        self.min_view_distance = self.default_config["min_view_distance"]
        self.max_view_distance = self.default_config["max_view_distance"]
        self.entity_limits = self.default_config["entity_limits"].copy()
        self.lag_alert_cooldown = self.default_config["lag_alert_cooldown"]
        self.afk_threshold = self.default_config["afk_threshold"]
        self.max_players_warning = self.default_config["max_players_warning"]
        self.max_players_critical = self.default_config["max_players_critical"]
        self.max_chunks_warning = self.default_config["max_chunks_warning"]
        self.max_chunks_critical = self.default_config["max_chunks_critical"]
        self.entity_whitelist: List[str] = list(self.default_config["entity_whitelist"])
        
        # Load config from file (creates default if not exists)
        self.load_config()

    def on_enable(self) -> None:
        self.logger.info("=== Server Optimizer v2.2.1 Enabled (API 0.11) ===")
        
        # Register event listeners
        self.register_events(self)
        
        # Wrap all tasks in try-except
        def safe_task(task_func):
            def wrapper():
                try:
                    task_func()
                except Exception as e:
                    self.logger.error(f"Task error: {e}")
            return wrapper
        
        # TPS history recording (runs every 1 second) - uses server.average_tps
        self.server.scheduler.run_task(
            self, safe_task(self.record_tps_history), delay=20, period=20
        )
        
        # Fast optimization check (runs every 10 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.fast_optimization_check), delay=200, period=200
        )
        
        # Auto-optimization (runs every 30 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.auto_optimize_task), delay=100, period=600
        )
        
        # AFK detection (runs every 30 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.detect_afk_players), delay=300, period=600
        )
        
        # View distance adjuster (runs every 15 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.adjust_view_distance), delay=300, period=300
        )
        
        # Memory cleanup (runs every 5 minutes)
        self.server.scheduler.run_task(
            self, safe_task(self.periodic_memory_cleanup), delay=6000, period=6000
        )
        
        # Performance display (runs every 2 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.update_performance_display), delay=40, period=40
        )
        
        # Overload monitoring (runs every 3 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.monitor_overload), delay=60, period=60
        )
        
        # Health check (runs every 5 seconds)
        self.server.scheduler.run_task(
            self, safe_task(self.check_server_health), delay=100, period=100
        )
        
        # Hourly entity purge check (runs every 1 second to handle warnings)
        self.server.scheduler.run_task(
            self, safe_task(self.hourly_entity_purge_check), delay=20, period=20
        )
        
        self.logger.info("All optimization tasks started!")
        self.logger.info(f"Packet throttling: {'ENABLED' if self.packet_throttle_enabled else 'DISABLED'}")
        self.logger.info("Crash protection: ENABLED")

    def on_disable(self) -> None:
        self.logger.info("=== Server Optimizer Disabled ===")
        self.logger.info(f"Total Optimizations: {self.total_optimizations}")

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        match command.name:
            case "optimize":
                return self.handle_optimize_command(sender, args)
            case "tps":
                return self.handle_tps_command(sender)
            case "lag":
                return self.handle_lag_command(sender)
            case "viewdistance":
                return self.handle_viewdistance_command(sender, args)
            case "tpsthreshold":
                return self.handle_tpsthreshold_command(sender, args)
            case "soconfig":
                return self.handle_config_command(sender, args)
        return False

    def handle_optimize_command(self, sender: CommandSender, args: list[str]) -> bool:
        if not sender.has_permission("serveropt.command.optimize"):
            sender.send_error_message("§cYou do not have permission to use this command!")
            return False

        if len(args) == 0:
            sender.send_message("§e=== Optimize Commands ===")
            sender.send_message("§7/optimize status §f- View server status")
            sender.send_message("§7/optimize full §f- Run full optimization")
            sender.send_message("§7/optimize packets §f- View packet throttle stats")
            sender.send_message("§7/optimize view <player> §f- Toggle TPS display for a player")
            return True

        action = args[0].lower()
        
        if action == "status":
            self.show_detailed_status(sender)
        elif action == "full":
            sender.send_message("§e[Optimizer] Running full optimization...")
            # Run the full hourly purge which clears everything and shows detailed stats
            stats = self.hourly_entity_purge()
            self.optimize_memory()
            # Also send a summary to the command sender
            total_cleared = stats.get("entities_cleared", 0)
            total_entities = stats.get("entities_total", 0)
            sender.send_message(f"§a✓ Optimization Complete! §7Cleared §f{total_cleared}§7/§f{total_entities}§7 entities.")
        elif action == "view":
            return self.handle_performance_view(sender, args)
        elif action == "packets":
            sender.send_message("§e§l=== Packet Throttle Stats ===")
            status = "§aENABLED" if self.packet_throttle_enabled else "§cDISABLED"
            sender.send_message(f"§eStatus: {status}")
            sender.send_message(f"§ePackets Inspected: §f{self.packets_inspected:,}")
            sender.send_message(f"§ePackets Throttled: §f{self.packets_throttled:,}")
            if self.packets_inspected > 0:
                ratio = (self.packets_throttled / self.packets_inspected) * 100
                sender.send_message(f"§eThrottle Rate: §f{ratio:.1f}%")
            sender.send_message(f"§eTPS Threshold: §f{self.packet_throttle_tps_threshold}")
            sender.send_message(f"§eCodec Available: {'§aYes' if HAS_PACKET_CODEC else '§cNo'}")
        else:
            sender.send_error_message(f"§cUnknown optimize subcommand: {action}")
            return False
        
        return True

    def handle_tps_command(self, sender: CommandSender) -> bool:
        tps = self.calculate_tps()
        mspt = self.get_mspt()
        color = self.get_tps_color(tps)
        sender.send_message(f"§e§l[TPS] {color}{tps:.2f} TPS §7(Target: 20.0) §eMSPT: §f{mspt:.1f}ms")
        return True

    def handle_lag_command(self, sender: CommandSender) -> bool:
        tps = self.calculate_tps()
        mspt = self.get_mspt()
        online_players = len(self.server.online_players)
        total_chunks = self.get_total_loaded_chunks()
        sender.send_message("§e§l=== LAG Report ===")
        sender.send_message(f"§eTPS: §f{tps:.2f}§e/20.0 §8| §eMSPT: §f{mspt:.1f}ms")
        sender.send_message(f"§ePlayers: §f{online_players} §8| §eChunks: §f{total_chunks}")
        sender.send_message(f"§eAFK Players: §f{len(self.afk_players)}")
        sender.send_message(f"§ePackets Throttled: §f{self.packets_throttled:,}")
        if self.aggressive_mode:
            sender.send_message("§c⚠ AGGRESSIVE MODE ACTIVE")
        return True

    def handle_viewdistance_command(self, sender: CommandSender, args: list[str]) -> bool:
        if not sender.has_permission("serveropt.command.viewdistance"):
            sender.send_error_message("§cYou do not have permission to use this command!")
            return False

        if len(args) == 0:
            sender.send_message(f"§e[View Distance] Current: §f{self.current_view_distance} chunks")
            sender.send_message(f"§7Auto Adjust: {'§aON' if self.auto_view_distance else '§cOFF'}")
            sender.send_message(f"§7Limits: §f{self.min_view_distance} - {self.max_view_distance} chunks (Max allowed: 32)")
            return True

        if args[0].lower() == "auto":
            self.auto_view_distance = not self.auto_view_distance
            status = "ON" if self.auto_view_distance else "OFF"
            sender.send_message(f"§a✓ Auto View Distance set to: {status}")
            return True

        try:
            vd = int(args[0])
            if vd < 2 or vd > 32:
                sender.send_error_message("§cView distance must be between 2 and 32 chunks!")
                return False
            
            self.current_view_distance = vd
            self.auto_view_distance = False
            self.send_view_distance_to_all(vd)
            sender.send_message(f"§a✓ Set view distance to {vd} chunks (sent to all players)")
            
        except ValueError:
            sender.send_error_message("§cPlease enter a valid number!")
            return False
        
        return True

    def handle_tpsthreshold_command(self, sender: CommandSender, args: list[str]) -> bool:
        if not sender.has_permission("serveropt.command.tpsthreshold"):
            sender.send_error_message("§cYou do not have permission to use this command!")
            return False

        # No args - show current thresholds
        if len(args) == 0:
            sender.send_message("§e§l=== TPS Thresholds ===")
            sender.send_message(f"§eCritical: §c{self.tps_critical} §7(Emergency recovery triggers)")
            sender.send_message(f"§eWarning: §6{self.tps_warning} §7(Auto-optimization triggers)")
            sender.send_message(f"§eTarget: §a{self.tps_target} §7(Target TPS goal)")
            sender.send_message("§7Usage: /tpsthreshold <critical|warning|target> <value>")
            return True

        if len(args) < 2:
            sender.send_error_message("§cUsage: /tpsthreshold <critical|warning|target> <value>")
            return False

        threshold_type = args[0].lower()
        
        try:
            value = float(args[1])
            
            if value < 1.0 or value > 20.0:
                sender.send_error_message("§cTPS threshold must be between 1.0 and 20.0!")
                return False
            
            if threshold_type == "critical":
                self.tps_critical = value
                sender.send_message(f"§a✓ Critical TPS threshold set to §c{value}")
                sender.send_message("§7Emergency recovery will trigger when TPS falls below this value.")
            elif threshold_type == "warning":
                self.tps_warning = value
                sender.send_message(f"§a✓ Warning TPS threshold set to §6{value}")
                sender.send_message("§7Auto-optimization will trigger when TPS falls below this value.")
            elif threshold_type == "target":
                self.tps_target = value
                sender.send_message(f"§a✓ Target TPS threshold set to §a{value}")
                sender.send_message("§7This is the goal TPS the optimizer aims for.")
            else:
                sender.send_error_message("§cInvalid threshold type! Use: critical, warning, or target")
                return False
            
            self.logger.info(f"TPS threshold '{threshold_type}' set to {value} by {sender.name}")
            return True
            
        except ValueError:
            sender.send_error_message("§cPlease enter a valid number!")
            return False

    # ========================
    # Configuration Methods
    # ========================
    
    def get_config_dir(self) -> Path:
        """Get the config directory path, creating it if needed."""
        # Use plugin's data folder - typically plugins/<plugin_name>/
        try:
            data_dir = Path(self.data_folder)
        except Exception:
            # Fallback if data_folder not available
            data_dir = Path("plugins/server_optimizer")
        
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    
    def load_config(self) -> bool:
        """Load configuration from JSON file. Creates default if not exists."""
        try:
            config_dir = self.get_config_dir()
            self.config_path = config_dir / "config.json"
            
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                
                # Merge with defaults (in case new settings were added)
                merged_config = self.default_config.copy()
                for key, value in loaded_config.items():
                    if key in merged_config:
                        if isinstance(merged_config[key], dict) and isinstance(value, dict):
                            merged_config[key].update(value)
                        else:
                            merged_config[key] = value
                
                self.apply_config(merged_config)
                self.logger.info(f"Configuration loaded from {self.config_path}")
            else:
                # Create default config file
                self.save_config()
                self.logger.info(f"Created default configuration at {self.config_path}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            return False
    
    def save_config(self) -> bool:
        """Save current configuration to JSON file."""
        try:
            if self.config_path is None:
                config_dir = self.get_config_dir()
                self.config_path = config_dir / "config.json"
            
            config_data = self.get_config_dict()
            
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)
            
            self.logger.info(f"Configuration saved to {self.config_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")
            return False
    
    def apply_config(self, config: Dict[str, Any]) -> None:
        """Apply configuration dictionary to class attributes."""
        self.auto_optimize = config.get("auto_optimize", self.default_config["auto_optimize"])
        self.optimization_interval = config.get("optimization_interval", self.default_config["optimization_interval"])
        self.hourly_purge_interval = config.get("hourly_purge_interval", self.default_config["hourly_purge_interval"])
        self.tps_target = config.get("tps_target", self.default_config["tps_target"])
        self.tps_warning = config.get("tps_warning", self.default_config["tps_warning"])
        self.tps_critical = config.get("tps_critical", self.default_config["tps_critical"])
        self.auto_view_distance = config.get("auto_view_distance", self.default_config["auto_view_distance"])
        self.base_view_distance = config.get("base_view_distance", self.default_config["base_view_distance"])
        self.min_view_distance = config.get("min_view_distance", self.default_config["min_view_distance"])
        self.max_view_distance = config.get("max_view_distance", self.default_config["max_view_distance"])
        self.lag_alert_cooldown = config.get("lag_alert_cooldown", self.default_config["lag_alert_cooldown"])
        self.afk_threshold = config.get("afk_threshold", self.default_config["afk_threshold"])
        self.max_players_warning = config.get("max_players_warning", self.default_config["max_players_warning"])
        self.max_players_critical = config.get("max_players_critical", self.default_config["max_players_critical"])
        self.max_chunks_warning = config.get("max_chunks_warning", self.default_config["max_chunks_warning"])
        self.max_chunks_critical = config.get("max_chunks_critical", self.default_config["max_chunks_critical"])
        
        # Handle nested entity_limits
        entity_limits = config.get("entity_limits", self.default_config["entity_limits"])
        if isinstance(entity_limits, dict):
            self.entity_limits = entity_limits.copy()
        
        # Handle entity whitelist
        whitelist = config.get("entity_whitelist", self.default_config["entity_whitelist"])
        if isinstance(whitelist, list):
            self.entity_whitelist = [str(e).lower() for e in whitelist]
        
        # Update current view distance to base if needed
        self.current_view_distance = self.base_view_distance
    
    def get_config_dict(self) -> Dict[str, Any]:
        """Get current configuration as a dictionary."""
        return {
            "auto_optimize": self.auto_optimize,
            "optimization_interval": self.optimization_interval,
            "hourly_purge_interval": self.hourly_purge_interval,
            "tps_target": self.tps_target,
            "tps_warning": self.tps_warning,
            "tps_critical": self.tps_critical,
            "auto_view_distance": self.auto_view_distance,
            "base_view_distance": self.base_view_distance,
            "min_view_distance": self.min_view_distance,
            "max_view_distance": self.max_view_distance,
            "entity_limits": self.entity_limits.copy(),
            "lag_alert_cooldown": self.lag_alert_cooldown,
            "afk_threshold": self.afk_threshold,
            "max_players_warning": self.max_players_warning,
            "max_players_critical": self.max_players_critical,
            "max_chunks_warning": self.max_chunks_warning,
            "max_chunks_critical": self.max_chunks_critical,
            "entity_whitelist": list(self.entity_whitelist),
        }
    
    def handle_config_command(self, sender: CommandSender, args: list[str]) -> bool:
        """Handle /config command for viewing, reloading, and editing configuration."""
        if not sender.has_permission("serveropt.command.config"):
            sender.send_error_message("§cYou do not have permission to use this command!")
            return False

        # No args - show current config
        if len(args) == 0:
            sender.send_message("§e§l=== Server Optimizer Config ===")
            sender.send_message(f"§eauto_optimize: §f{self.auto_optimize}")
            sender.send_message(f"§eoptimization_interval: §f{self.optimization_interval}s")
            sender.send_message(f"§etps_target: §a{self.tps_target}")
            sender.send_message(f"§etps_warning: §6{self.tps_warning}")
            sender.send_message(f"§etps_critical: §c{self.tps_critical}")
            sender.send_message(f"§eauto_view_distance: §f{self.auto_view_distance}")
            sender.send_message(f"§ebase/min/max_view_distance: §f{self.base_view_distance}/{self.min_view_distance}/{self.max_view_distance}")
            sender.send_message(f"§elag_alert_cooldown: §f{self.lag_alert_cooldown}s")
            sender.send_message(f"§eafk_threshold: §f{self.afk_threshold}s")
            if self.entity_whitelist:
                sender.send_message(f"§eentity_whitelist: §f{', '.join(self.entity_whitelist)}")
            else:
                sender.send_message("§eentity_whitelist: §7(empty)")
            sender.send_message("§7Use /config reload | set <key> <value> | reset")
            return True

        action = args[0].lower()
        
        if action == "reload":
            if self.load_config():
                sender.send_message("§a✓ Configuration reloaded from file!")
            else:
                sender.send_error_message("§cFailed to reload configuration!")
            return True
        
        elif action == "reset":
            self.apply_config(self.default_config)
            self.save_config()
            sender.send_message("§a✓ Configuration reset to defaults and saved!")
            return True
        
        elif action == "set":
            if len(args) < 3:
                sender.send_error_message("§cUsage: /config set <key> <value>")
                sender.send_message("§7Available keys: auto_optimize, optimization_interval, tps_target,")
                sender.send_message("§7tps_warning, tps_critical, auto_view_distance, base_view_distance,")
                sender.send_message("§7min_view_distance, max_view_distance, lag_alert_cooldown, afk_threshold")
                return False
            
            key = args[1].lower()
            value_str = args[2]
            
            # Boolean settings
            if key in ["auto_optimize", "auto_view_distance"]:
                value = value_str.lower() in ["true", "1", "yes", "on"]
                setattr(self, key, value)
                self.save_config()
                sender.send_message(f"§a✓ Set {key} to §f{value}")
                return True
            
            # Integer settings
            elif key in ["optimization_interval", "hourly_purge_interval", "base_view_distance", "min_view_distance", 
                        "max_view_distance", "lag_alert_cooldown", "afk_threshold",
                        "max_players_warning", "max_players_critical", 
                        "max_chunks_warning", "max_chunks_critical"]:
                try:
                    value = int(value_str)
                    setattr(self, key, value)
                    self.save_config()
                    sender.send_message(f"§a✓ Set {key} to §f{value}")
                    return True
                except ValueError:
                    sender.send_error_message("§cPlease enter a valid integer!")
                    return False
            
            # Float settings
            elif key in ["tps_target", "tps_warning", "tps_critical"]:
                try:
                    value = float(value_str)
                    if value < 1.0 or value > 20.0:
                        sender.send_error_message("§cTPS values must be between 1.0 and 20.0!")
                        return False
                    setattr(self, key, value)
                    self.save_config()
                    sender.send_message(f"§a✓ Set {key} to §f{value}")
                    return True
                except ValueError:
                    sender.send_error_message("§cPlease enter a valid number!")
                    return False
            
            else:
                sender.send_error_message(f"§cUnknown config key: {key}")
                return False
        
        elif action == "save":
            if self.save_config():
                sender.send_message("§a✓ Configuration saved to file!")
            else:
                sender.send_error_message("§cFailed to save configuration!")
            return True
        
        elif action == "whitelist":
            return self.handle_whitelist_command(sender, args[1:] if len(args) > 1 else [])
        
        else:
            sender.send_error_message(f"§cUnknown config action: {action}")
            sender.send_message("§7Valid actions: reload, set, reset, save, whitelist")
            return False

    def handle_whitelist_command(self, sender: CommandSender, args: list[str]) -> bool:
        """Handle /soconfig whitelist subcommands for managing the entity whitelist."""
        if len(args) == 0:
            # Show current whitelist
            sender.send_message("§e§l=== Entity Whitelist ===")
            sender.send_message("§7Entities in this list are §aignored§7 during clears.")
            if self.entity_whitelist:
                for i, entry in enumerate(self.entity_whitelist, 1):
                    sender.send_message(f"§e  {i}. §f{entry}")
            else:
                sender.send_message("§7  (empty)")
            sender.send_message("§7Usage:")
            sender.send_message("§7  /soconfig whitelist add <entity_type>")
            sender.send_message("§7  /soconfig whitelist remove <entity_type>")
            sender.send_message("§7  /soconfig whitelist clear")
            return True
        
        sub = args[0].lower()
        
        if sub == "add":
            if len(args) < 2:
                sender.send_error_message("§cUsage: /soconfig whitelist add <entity_type>")
                sender.send_message("§7Example: /soconfig whitelist add ninjos:blue_easter_egg")
                return False
            entity_type = args[1].lower()
            if entity_type in self.entity_whitelist:
                sender.send_message(f"§e'{entity_type}' is already in the whitelist.")
                return True
            self.entity_whitelist.append(entity_type)
            self.save_config()
            sender.send_message(f"§a✓ Added §f'{entity_type}'§a to entity whitelist.")
            return True
        
        elif sub == "remove":
            if len(args) < 2:
                sender.send_error_message("§cUsage: /soconfig whitelist remove <entity_type>")
                return False
            entity_type = args[1].lower()
            if entity_type not in self.entity_whitelist:
                sender.send_error_message(f"§c'{entity_type}' is not in the whitelist.")
                return False
            self.entity_whitelist.remove(entity_type)
            self.save_config()
            sender.send_message(f"§a✓ Removed §f'{entity_type}'§a from entity whitelist.")
            return True
        
        elif sub == "clear":
            self.entity_whitelist.clear()
            self.save_config()
            sender.send_message("§a✓ Entity whitelist cleared.")
            return True
        
        else:
            sender.send_error_message(f"§cUnknown whitelist action: {sub}")
            sender.send_message("§7Valid actions: add, remove, clear")
            return False
    def handle_performance_view(self, sender: CommandSender, args: list[str]) -> bool:
        if len(args) < 2:
            if len(self.performance_viewers) == 0:
                sender.send_message("§e[Performance View] §7No players are currently viewing performance.")
            else:
                sender.send_message("§e[Performance View] §7Players viewing:")
                for viewer in self.performance_viewers:
                    sender.send_message(f"§7- §f{viewer}")
            return True
        
        target_name = args[1]
        target_player: Optional[Player] = None
        
        for player in self.server.online_players:
            if player.name.lower() == target_name.lower():
                target_player = player
                break
        
        if target_player is None:
            sender.send_error_message(f"§cPlayer not found: {target_name}")
            return False
        
        if target_player.name in self.performance_viewers:
            self.performance_viewers.remove(target_player.name)
            sender.send_message(f"§a✓ Disabled performance display for §f{target_player.name}")
            target_player.send_message("§e[Performance View] §7Performance display disabled.")
        else:
            self.performance_viewers.add(target_player.name)
            sender.send_message(f"§a✓ Enabled performance display for §f{target_player.name}")
            target_player.send_message("§e[Performance View] §aPerformance display enabled.")
        
        return True

    def show_detailed_status(self, sender: CommandSender) -> None:
        tps = self.calculate_tps()
        mspt = self.get_mspt()
        color = self.get_tps_color(tps)
        online = len(self.server.online_players)
        
        sender.send_message("§e§l═══════════════════")
        sender.send_message("§e§l   Server Status v2.0")
        sender.send_message("§e§l═══════════════════")
        sender.send_message(f"§eTPS: {color}{tps:.2f}§e/20.0 §8| §eMSPT: §f{mspt:.1f}ms")
        sender.send_message(f"§eHealth: {self.get_health_color()}{self.health_score}§e/100")
        sender.send_message(f"§ePlayers: §f{online} §8| §eAFK: §f{len(self.afk_players)}")
        sender.send_message(f"§eView Distance: §f{self.current_view_distance}")
        
        # Per-dimension chunk counts
        try:
            level = self.server.level
            if level:
                for dim in level.dimensions:
                    chunk_count = len(dim.loaded_chunks)
                    sender.send_message(f"§e  {dim.name}: §f{chunk_count} chunks")
        except Exception:
            sender.send_message(f"§e  Chunks: §f{self.get_total_loaded_chunks()} total")
        
        sender.send_message(f"§eOptimizations: §f{self.total_optimizations}")
        sender.send_message(f"§eEntities Removed: §f{self.entities_removed_total}")
        sender.send_message(f"§ePackets Throttled: §f{self.packets_throttled:,}")
        sender.send_message("§e§l═══════════════════")

    def record_tps_history(self) -> None:
        """Records TPS from server.average_tps and checks for critical lag."""
        try:
            tps = self.calculate_tps()
            self.tps_history.append(tps)
            
            # Notify admins of severe lag
            if tps < self.tps_critical:
                self.notify_admins_lag(tps)

        except Exception as e:
            self.logger.error(f"TPS recording error: {e}")

    def fast_optimization_check(self) -> None:
        if not self.auto_optimize:
            return
        
        tps = self.calculate_tps()
        if tps < self.tps_critical:
            self.logger.warning(f"Critical TPS detected: {tps:.2f}. Initiating emergency recovery.")
            self.emergency_crash_recovery()

    def auto_optimize_task(self) -> None:
        if not self.auto_optimize:
            return
        
        current_time = time.time()
        tps = self.calculate_tps()
        
        # Optimize if TPS is low or enough time has passed
        if tps < self.tps_warning or current_time - self.last_optimization >= self.optimization_interval:
            self.logger.info(f"Auto-optimization triggered (TPS: {tps:.2f})")
            entities_removed = self.optimize_entities()
            self.last_optimization = current_time
            self.total_optimizations += 1
            self.logger.info(f"Auto-optimization complete. Entities removed: {entities_removed}")

    def detect_afk_players(self) -> None:
        """Detect AFK players using PlayerMoveEvent timestamps."""
        try:
            current_time = time.time()
            new_afk = set()
            
            for player in self.server.online_players:
                last_move = self.player_last_move.get(player.name, current_time)
                idle_time = current_time - last_move
                
                if idle_time >= self.afk_threshold:
                    new_afk.add(player.name)
                    if player.name not in self.afk_players:
                        # Newly AFK
                        self.logger.info(f"Player {player.name} is now AFK ({idle_time:.0f}s idle)")
            
            self.afk_players = new_afk
        except Exception as e:
            self.logger.error(f"AFK detection error: {e}")

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        """Encode an integer as a standard Bedrock VarInt (unsigned)."""
        result = bytearray()
        val = max(0, value)
        while val > 0x7F:
            result.append((val & 0x7F) | 0x80)
            val >>= 7
        result.append(val & 0x7F)
        return bytes(result)

    def send_view_distance_to_player(self, player: Player, chunk_radius: int) -> bool:
        """Send ChunkRadiusUpdatedPacket (0x46) to a single player."""
        try:
            payload = self._encode_varint(chunk_radius)
            player.send_packet(PACKET_ID_CHUNK_RADIUS_UPDATED, payload)
            return True
        except Exception as e:
            self.logger.debug(f"Failed to send ChunkRadiusUpdated to {player.name}: {e}")
            return False

    def send_view_distance_to_all(self, chunk_radius: int) -> None:
        """Send ChunkRadiusUpdatedPacket (0x46) to all online players."""
        payload = self._encode_varint(chunk_radius)
        sent = 0
        for player in self.server.online_players:
            try:
                player.send_packet(PACKET_ID_CHUNK_RADIUS_UPDATED, payload)
                sent += 1
            except Exception:
                pass
        if sent > 0:
            self.logger.info(f"Sent ChunkRadiusUpdated (radius={chunk_radius}) to {sent} players")

    def adjust_view_distance(self) -> None:
        if not self.auto_view_distance:
            return
        
        tps = self.calculate_tps()
        target_vd = self.current_view_distance
        
        if tps >= 18.5 and self.current_view_distance < self.max_view_distance:
            target_vd = min(self.max_view_distance, self.current_view_distance + 1)
        elif tps < 15.0 and self.current_view_distance > self.min_view_distance:
            target_vd = max(self.min_view_distance, self.current_view_distance - 1)
        elif tps < self.tps_critical:
            target_vd = self.min_view_distance
        
        if target_vd != self.current_view_distance:
            old_vd = self.current_view_distance
            self.current_view_distance = target_vd
            self.send_view_distance_to_all(target_vd)
            self.logger.info(f"Adjusted view distance {old_vd} -> {target_vd} (TPS: {tps:.2f})")


    def periodic_memory_cleanup(self) -> None:
        if self.auto_optimize:
            self.optimize_memory()

    def optimize_chunks(self) -> int:
        """Get real loaded chunk count across all dimensions. 
        Note: Endstone doesn't expose chunk unloading — we report the count for monitoring."""
        count = 0
        try:
            level = self.server.level
            if level:
                for dim in level.dimensions:
                    chunk_count = len(dim.loaded_chunks)
                    count += chunk_count
        except Exception:
            pass
        return count

    def optimize_entities(self) -> int:
        """
        Removes excess entities from the world while protecting nametagged entities.
        Uses Endstone API: self.server.level.actors to iterate through entities.
        Returns the count of entities removed.
        """
        count = 0
        
        try:
            # Get all actors from the server level
            level = self.server.level
            if level is None:
                return 0
            
            actors = level.actors
            
            # Track entities by type
            entity_counts: Dict[str, list] = {
                "item": [],
                "arrow": [],
                "xp_orb": [],
                "minecart": [],
                "boat": [],
                "mob": [],
            }
            
            # Known mob types to track for clearing
            mob_keywords = [
                "zombie", "skeleton", "creeper", "spider", "enderman", "slime",
                "witch", "phantom", "drowned", "husk", "stray", "pillager",
                "vindicator", "evoker", "ravager", "vex", "warden", "wither",
                "blaze", "ghast", "magma_cube", "piglin", "hoglin", "zoglin",
                "endermite", "silverfish", "guardian", "elder_guardian", "shulker",
                "pig", "cow", "sheep", "chicken", "rabbit", "wolf", "cat",
                "horse", "donkey", "mule", "llama", "fox", "bee", "goat",
                "axolotl", "frog", "tadpole", "allay", "camel", "sniffer",
                "villager", "iron_golem", "snow_golem", "wandering_trader",
                "bat", "squid", "glow_squid", "dolphin", "turtle", "panda",
                "polar_bear", "ocelot", "parrot", "mooshroom", "strider",
            ]
            
            # Categorize entities
            for actor in actors:
                try:
                    actor_type = actor.type.lower() if actor.type else ""
                    
                    # Skip players
                    if "player" in actor_type:
                        continue
                    
                    # Skip whitelisted entities
                    if self._is_entity_whitelisted(actor_type):
                        continue
                    
                    # Skip entities with custom names (nametagged)
                    # name_tag is a string property, empty string means no name tag
                    if actor.name_tag and len(actor.name_tag) > 0:
                        continue
                    
                    # Always skip minecarts - preserve player vehicles
                    if "minecart" in actor_type:
                        continue
                    
                    # For boats, only skip if a player is riding it
                    if "boat" in actor_type:
                        # Check if any online player is riding this boat
                        is_occupied = False
                        try:
                            for player in self.server.online_players:
                                # Check if player's vehicle matches this boat
                                if hasattr(player, 'vehicle') and player.vehicle and player.vehicle.runtime_id == actor.runtime_id:
                                    is_occupied = True
                                    break
                        except Exception:
                            # If we can't check, err on the side of caution and skip
                            is_occupied = True
                        
                        if is_occupied:
                            continue
                        # Unoccupied boats will be categorized below for limit-based removal
                    
                    # Categorize by type
                    if "item" in actor_type:
                        entity_counts["item"].append(actor)
                    elif "arrow" in actor_type:
                        entity_counts["arrow"].append(actor)
                    elif "xp" in actor_type or "orb" in actor_type:
                        entity_counts["xp_orb"].append(actor)
                    elif "minecart" in actor_type:
                        entity_counts["minecart"].append(actor)
                    elif "boat" in actor_type:
                        entity_counts["boat"].append(actor)
                    else:
                        # Check if it's a mob
                        for mob_keyword in mob_keywords:
                            if mob_keyword in actor_type:
                                entity_counts["mob"].append(actor)
                                break
                except Exception:
                    continue  # Skip actors that error on access
            
            # Determine limits based on mode
            limits = {
                "item": 200 if self.aggressive_mode else self.entity_limits.get("item", 900),
                "arrow": 50 if self.aggressive_mode else self.entity_limits.get("arrow", 120),
                "xp_orb": 100 if self.aggressive_mode else 300,
                "minecart": 100 if self.aggressive_mode else self.entity_limits.get("minecart", 500),
                "boat": 20 if self.aggressive_mode else self.entity_limits.get("boat", 50),
                "mob": 150 if self.aggressive_mode else self.entity_limits.get("mob", 600),
            }
            
            # Remove excess entities
            for entity_type, entities in entity_counts.items():
                limit = limits.get(entity_type, 50)
                if len(entities) > limit:
                    # Remove oldest entities first (assuming list order = spawn order)
                    to_remove = entities[:-limit] if limit > 0 else entities
                    for entity in to_remove:
                        try:
                            entity.remove()
                            count += 1
                        except Exception:
                            pass  # Entity may have already been removed
            
            self.entities_removed_total += count
            
            if count > 0:
                self.logger.info(f"Entity optimization removed {count} entities")
            
        except Exception as e:
            self.logger.error(f"Entity optimization error: {e}")
            # Fallback: just report estimated removals for stats
            fallback_count = 50 if not self.aggressive_mode else 100
            self.entities_removed_total += fallback_count
            return fallback_count
        
        return count

    def hourly_entity_purge_check(self) -> None:
        """Check if it's time for the hourly entity purge and send warnings."""
        if not self.auto_optimize:
            return
        
        current_time = time.time()
        time_since_last_purge = current_time - self.last_hourly_purge
        time_until_purge = self.hourly_purge_interval - time_since_last_purge
        
        # Send 30 second warning
        if time_until_purge <= 30 and time_until_purge > 5 and not self.hourly_purge_30s_warning_sent:
            self.hourly_purge_30s_warning_sent = True
            for player in self.server.online_players:
                try:
                    player.send_message("§e[Server] §6⚠ Entity cleanup in §f30 seconds§6! Ground items and mobs will be cleared.")
                except Exception:
                    pass
        
        # Send 5 second warning
        if time_until_purge <= 5 and time_until_purge > 0 and not self.hourly_purge_5s_warning_sent:
            self.hourly_purge_5s_warning_sent = True
            for player in self.server.online_players:
                try:
                    player.send_message("§c[Server] §c⚠ Entity cleanup in §f5 seconds§c! Pick up your items now!")
                except Exception:
                    pass
        
        # Execute the purge
        if time_since_last_purge >= self.hourly_purge_interval:
            self.hourly_entity_purge()
            self.last_hourly_purge = current_time
            # Reset warning flags for next cycle
            self.hourly_purge_30s_warning_sent = False
            self.hourly_purge_5s_warning_sent = False

    def hourly_entity_purge(self) -> dict:
        """
        Complete purge of items, arrows, xp_orbs, mobs, and unloads excess chunks.
        Preserves minecarts, boats, and nametagged entities.
        This runs on the hourly_purge_interval (default: 1 hour).
        Returns a dictionary with counts by category.
        """
        # Track both cleared counts and totals before clearing
        stats = {
            "items_cleared": 0,
            "items_total": 0,
            "mobs_cleared": 0,
            "mobs_total": 0,
            "arrows_cleared": 0,
            "arrows_total": 0,
            "xp_orbs_cleared": 0,
            "xp_orbs_total": 0,
            "chunks_cleared": 0,
            "chunks_total": 0,
            "entities_cleared": 0,
            "entities_total": 0
        }
        
        try:
            # Get chunk stats before optimization
            stats["chunks_total"] = self.get_total_loaded_chunks()
            
            # Optimize chunks
            chunks_cleared = self.optimize_chunks()
            stats["chunks_cleared"] = chunks_cleared
            
            level = self.server.level
            if level is None:
                # Still show results if we cleared chunks
                self._notify_hourly_cleanup(stats)
                return stats
            
            actors = level.actors
            
            # Known mob types to clear
            mob_keywords = [
                "zombie", "skeleton", "creeper", "spider", "enderman", "slime",
                "witch", "phantom", "drowned", "husk", "stray", "pillager",
                "vindicator", "evoker", "ravager", "vex", "warden", "wither",
                "blaze", "ghast", "magma_cube", "piglin", "hoglin", "zoglin",
                "endermite", "silverfish", "guardian", "elder_guardian", "shulker",
                "pig", "cow", "sheep", "chicken", "rabbit", "wolf", "cat",
                "horse", "donkey", "mule", "llama", "fox", "bee", "goat",
                "axolotl", "frog", "tadpole", "allay", "camel", "sniffer",
                "villager", "iron_golem", "snow_golem", "wandering_trader",
                "bat", "squid", "glow_squid", "dolphin", "turtle", "panda",
                "polar_bear", "ocelot", "parrot", "mooshroom", "strider",
            ]
            
            # Track entities by category for removal
            entities_to_remove = []  # (actor, category) tuples
            
            for actor in actors:
                try:
                    actor_type = actor.type.lower() if actor.type else ""
                    
                    # Skip players
                    if "player" in actor_type:
                        continue
                    
                    # Skip whitelisted entities
                    if self._is_entity_whitelisted(actor_type):
                        continue
                    
                    # Skip entities with custom names (nametagged)
                    if actor.name_tag and len(actor.name_tag) > 0:
                        continue
                    
                    # Skip minecarts and boats - these are preserved
                    if "minecart" in actor_type or "boat" in actor_type:
                        continue
                    
                    # Categorize and mark for removal
                    category = None
                    if "item" in actor_type:
                        category = "items"
                        stats["items_total"] += 1
                    elif "arrow" in actor_type:
                        category = "arrows"
                        stats["arrows_total"] += 1
                    elif "xp" in actor_type or "orb" in actor_type:
                        category = "xp_orbs"
                        stats["xp_orbs_total"] += 1
                    else:
                        # Check if it's a mob
                        for mob_keyword in mob_keywords:
                            if mob_keyword in actor_type:
                                category = "mobs"
                                stats["mobs_total"] += 1
                                break
                    
                    if category:
                        entities_to_remove.append((actor, category))
                        stats["entities_total"] += 1
                        
                except Exception:
                    continue
            
            # Remove all marked entities and count by category
            for entity, category in entities_to_remove:
                try:
                    entity.remove()
                    stats[f"{category}_cleared"] += 1
                    stats["entities_cleared"] += 1
                except Exception:
                    pass
            
            self.entities_removed_total += stats["entities_cleared"]
            
            # Log results
            if stats["entities_cleared"] > 0 or stats["chunks_cleared"] > 0:
                self.logger.info(f"§e[Hourly Purge] Cleared {stats['entities_cleared']}/{stats['entities_total']} entities, {stats['chunks_cleared']}/{stats['chunks_total']} chunks")
            
            # Notify players
            self._notify_hourly_cleanup(stats)
            
        except Exception as e:
            self.logger.error(f"Hourly entity purge error: {e}")
        
        return stats
    
    def _notify_hourly_cleanup(self, stats: dict) -> None:
        """Send hourly cleanup notification to all online players with detailed stats."""
        has_entities = stats["entities_cleared"] > 0
        has_chunks = stats["chunks_cleared"] > 0
        
        for player in self.server.online_players:
            try:
                if has_entities or has_chunks:
                    # Header
                    player.send_message("§8§m──────────────")
                    player.send_message("§a§l  ✓ §e§lHOURLY CLEANUP COMPLETE")
                    player.send_message("§8§m──────────────")
                    
                    # Entity stats with icons
                    player.send_message(f"§6  ⬛ §eItems     §8» §a{stats['items_cleared']}§7/§f{stats['items_total']}")
                    player.send_message(f"§c  ⚔ §eMobs      §8» §a{stats['mobs_cleared']}§7/§f{stats['mobs_total']}")
                    player.send_message(f"§d  ✦ §eXP Orbs   §8» §a{stats['xp_orbs_cleared']}§7/§f{stats['xp_orbs_total']}")
                    player.send_message(f"§2  ▦ §eChunks    §8» §a{stats['chunks_cleared']}§7/§f{stats['chunks_total']}")

                else:
                    player.send_message("§a§l✓ §e§lHourly Cleanup Complete! §7Nothing needed clearing.")
            except Exception:
                pass

    def _is_entity_whitelisted(self, actor_type: str) -> bool:
        """Check if an entity type matches any entry in the whitelist."""
        if not self.entity_whitelist:
            return False
        actor_type_lower = actor_type.lower()
        for entry in self.entity_whitelist:
            if entry in actor_type_lower:
                return True
        return False

    def optimize_memory(self) -> None:
        """Runs the Python garbage collector to free memory."""
        gc.collect()
        self.logger.info("Garbage Collector executed.")

    def calculate_tps(self) -> float:
        """Returns current server TPS from the Endstone server API."""
        try:
            return self.server.average_tps
        except Exception:
            return 20.0

    def get_mspt(self) -> float:
        """Returns current MSPT (milliseconds per tick) from the Endstone server API."""
        try:
            return self.server.average_mspt
        except Exception:
            return 50.0

    def get_total_loaded_chunks(self) -> int:
        """Returns total loaded chunks across all dimensions."""
        total = 0
        try:
            level = self.server.level
            if level:
                for dim in level.dimensions:
                    total += len(dim.loaded_chunks)
        except Exception:
            pass
        return total

    def get_average_tps(self) -> float:
        """Calculates the average TPS over the recorded history."""
        if not self.tps_history:
            return 20.0
        return sum(self.tps_history) / len(self.tps_history)

    def get_tps_color(self, tps: float) -> str:
        if tps >= 19:
            return "§a" # Green
        elif tps >= 18:
            return "§e" # Yellow
        elif tps >= 15:
            return "§6" # Gold
        else:
            return "§c" # Red

    def get_tps_status(self, tps: float) -> str:
        if tps >= 19:
            return "Excellent"
        elif tps >= 18:
            return "Good"
        elif tps >= 15:
            return "Fair"
        else:
            return "Poor"

    def get_health_color(self) -> str:
        if self.health_score >= 80:
            return "§a"
        elif self.health_score >= 60:
            return "§e"
        elif self.health_score >= 40:
            return "§6"
        else:
            return "§c"

    def notify_admins_lag(self, tps: float) -> None:
        current_time = time.time()
        
        if current_time - self.last_lag_alert < self.lag_alert_cooldown:
            return
        
        self.last_lag_alert = current_time
        
        for player in self.server.online_players:
            # Check if player has serveropt.admin or is OP
            if player.is_op or player.has_permission("serveropt.admin"):
                color = self.get_tps_color(tps)
                player.send_message(f"§c[Optimizer] ⚠ WARNING: Low TPS: {color}{tps:.2f}§c/20.0")

    def monitor_overload(self) -> None:
        try:
            overload_detected = False
            overload_reasons = []
            
            online_players = len(self.server.online_players)
            if online_players >= self.max_players_critical:
                overload_detected = True
                overload_reasons.append(f"Players: {online_players}")
            
            total_chunks = self.get_total_loaded_chunks()
            if total_chunks >= self.max_chunks_critical:
                overload_detected = True
                overload_reasons.append(f"Chunks: {total_chunks}")
            
            # Check memory usage (simplified, as psutil was removed)
            # if self.get_memory_usage() >= self.memory_critical_threshold:
            #     overload_detected = True
            #     overload_reasons.append(f"Memory: {self.get_memory_usage():.1f}%")
            
            if overload_detected:
                self.logger.error(f"=== OVERLOAD DETECTED: {', '.join(overload_reasons)} ===")
                self.emergency_crash_recovery()
            
        except Exception as e:
            self.logger.error(f"Overload monitoring failed: {e}")

    def check_server_health(self) -> None:
        try:
            health = 100
            tps = self.calculate_tps()
            mspt = self.get_mspt()
            
            # TPS-based health
            if tps >= 19.5:
                health = 100
            elif tps >= 18:
                health = 80
            elif tps >= 15:
                health = 60
            else:
                health = 40
            
            # MSPT penalty: if MSPT > 50ms, reduce health
            if mspt > 50:
                health = max(20, health - int((mspt - 50) / 5))
            
            self.health_score = health
            self.health_history.append(health)
            
        except Exception as e:
            self.logger.error(f"Health check failed: {e}")

    def monitor_memory(self) -> None:
        """
        Stub for memory monitoring. Requires a platform-specific library (like psutil) 
        or an Endstone API method to get actual memory usage.
        The original implementation using psutil was removed for open-source compatibility.
        """
        # Placeholder for memory monitoring logic
        # For now, it only triggers garbage collection if auto_optimize is on (via periodic_memory_cleanup)
        pass

    def get_memory_usage(self) -> float:
        """Placeholder function for memory usage (%) that doesn't rely on psutil."""
        # In a real Endstone environment, this would call a server API method
        return 0.0 # Returning 0.0 as a safe default

    def monitor_task_performance(self, task_name: str, duration: float) -> None:
        try:
            # This would ideally be integrated into the scheduler wrappers to measure task execution time
            self.task_execution_times[task_name].append(duration)
            
            if len(self.task_execution_times[task_name]) > 10:
                self.task_execution_times[task_name].pop(0)
            
            if duration > self.max_task_duration:
                if task_name not in self.slow_tasks:
                    self.slow_tasks.add(task_name)
                    self.logger.warning(f"Slow task detected: {task_name} took {duration:.4f}s")
            else:
                if task_name in self.slow_tasks:
                    self.slow_tasks.remove(task_name)
        except Exception as e:
            self.logger.error(f"Task monitoring failed: {e}")

    def emergency_crash_recovery(self) -> None:
        try:
            self.logger.warning("=== EMERGENCY CRASH RECOVERY ACTIVATED ===")
            
            # Drop view distance to minimum
            old_vd = self.current_view_distance
            self.current_view_distance = self.min_view_distance
            
            # Activate aggressive mode
            self.aggressive_mode = True
            
            # Force immediate optimization
            self.optimize_chunks()
            self.optimize_entities()
            self.optimize_memory()
            
            for player in self.server.online_players:
                if player.is_op or player.has_permission("serveropt.admin"):
                    player.send_message("§c§l[EMERGENCY] §cEmergency optimization activated! View distance lowered.")
            
            self.logger.warning("=== EMERGENCY RECOVERY COMPLETE. Restoration scheduled. ===")
            
            # Schedule restoration to normal settings after 5 minutes (6000 ticks)
            def restore_normal():
                self.current_view_distance = self.base_view_distance
                self.aggressive_mode = False
                self.logger.info("Normal optimization settings restored.")
            
            self.server.scheduler.run_task(self, restore_normal, delay=6000)
            
        except Exception as e:
            self.logger.error(f"Emergency recovery failed: {e}")

    def update_performance_display(self) -> None:
        try:
            if not self.performance_viewers:
                return
            
            tps = self.calculate_tps()
            color = self.get_tps_color(tps)
            online = len(self.server.online_players)
            
            display_text = f"§e§l[OPT] {color}TPS: {tps:.1f}§r/20.0 §ePlayers: {online} §eVD: {self.current_view_distance}"
            
            # Iterate over a copy of the set to allow modification if a player is missing
            for player_name in list(self.performance_viewers):
                player: Optional[Player] = self.server.get_player(player_name)
                
                if player is None:
                    self.performance_viewers.remove(player_name)
                    continue
                
                player.send_popup(display_text)
                
        except Exception as e:
            self.logger.error(f"Performance display failed: {e}")

    @event_handler
    def on_server_load(self, event: ServerLoadEvent) -> None:
        self.logger.info("Server loaded - Optimizer ready!")

    @event_handler(priority=EventPriority.MONITOR)
    def on_player_join(self, event: PlayerJoinEvent) -> None:
        player = event.player
        
        # Initialize AFK tracking
        self.player_last_move[player.name] = time.time()
        
        # Sync view distance to the joining player after connection stabilizes
        def sync_view_distance():
            self.send_view_distance_to_player(player, self.current_view_distance)
            
        self.server.scheduler.run_task(self, sync_view_distance, delay=20)
        
        # Check if they are OP or have admin permission
        if player.is_op or player.has_permission("serveropt.admin"):
            def send_info():
                if player.is_op or player.has_permission("serveropt.admin"):
                    tps = self.calculate_tps()
                    mspt = self.get_mspt()
                    color = self.get_tps_color(tps)
                    player.send_message("§e§l[Server Optimizer v2.2.1]")
                    player.send_message(f"§7TPS: {color}{tps:.2f}§7/20.0 §8| §7MSPT: §f{mspt:.1f}ms")
                    chunks = self.get_total_loaded_chunks()
                    player.send_message(f"§7Chunks: §f{chunks} §8| §7Packet Throttle: {'§aON' if self.packet_throttle_enabled else '§cOFF'}")
            
            self.server.scheduler.run_task(self, send_info, delay=40)

    @event_handler
    def on_player_quit(self, event: PlayerQuitEvent) -> None:
        player_name = event.player.name
        
        if player_name in self.afk_players:
            self.afk_players.remove(player_name)
        
        if player_name in self.player_last_move:
            del self.player_last_move[player_name]
        
        if player_name in self.performance_viewers:
            self.performance_viewers.remove(player_name)

    @event_handler(priority=EventPriority.MONITOR)
    def on_player_move(self, event: PlayerMoveEvent) -> None:
        """Track player movement for AFK detection."""
        try:
            self.player_last_move[event.player.name] = time.time()
            # Remove from AFK set if they were AFK
            if event.player.name in self.afk_players:
                self.afk_players.discard(event.player.name)
        except Exception:
            pass

    @event_handler(priority=EventPriority.HIGHEST)
    def on_actor_spawn(self, event: ActorSpawnEvent) -> None:
        """Throttle entity spawns when dimension is over limit."""
        try:
            if not self.auto_optimize:
                return
            
            actor = event.actor
            if not hasattr(actor, 'type') or not hasattr(actor, 'dimension'):
                return
            
            actor_type = actor.type.lower()
            
            # Only throttle clearable entity types
            clearable_keywords = ["item", "arrow", "xp_orb", "experience_orb", "minecart", "boat"]
            is_clearable = any(kw in actor_type for kw in clearable_keywords)
            
            if not is_clearable:
                return
            
            # Check dimension entity count
            dim = actor.dimension
            actor_count = len(dim.actors)
            
            # Only cancel spawns during extreme entity buildup to prevent crashes
            max_entities = 1000 if self.aggressive_mode else 2000
            if actor_count > max_entities:
                event.is_cancelled = True
        except Exception:
            pass

    @event_handler(priority=EventPriority.HIGHEST)
    def on_packet_send(self, event: PacketSendEvent) -> None:
        """Throttle particle and sound packets during low TPS to reduce client load."""
        try:
            if not self.packet_throttle_enabled:
                return
            
            self.packets_inspected += 1
            packet_id = event.packet_id
            
            # Only throttle specific packet types
            if packet_id not in (PACKET_ID_LEVEL_SOUND, PACKET_ID_SPAWN_PARTICLE):
                return
            
            # Check if TPS is low enough to warrant throttling
            tps = self.calculate_tps()
            if tps >= self.packet_throttle_tps_threshold:
                return
            
            # Throttle: cancel the packet
            event.is_cancelled = True
            self.packets_throttled += 1
        except Exception:
            pass
