<!-- endstone-professional-header:start -->
<p align="center">
  <img src="docs/assets/banner.svg" width="100%" alt="Endstone Server Optimizer &mdash; Server optimization plugin for Endstone Minecraft Bedrock">
</p>

<p align="center">
  <a href="https://github.com/TheNINJALLO/endstone-server-optimizer/actions/workflows/wheel-release.yml"><img alt="Build" src="https://img.shields.io/github/actions/workflow/status/TheNINJALLO/endstone-server-optimizer/wheel-release.yml?branch=main&amp;style=for-the-badge&amp;logo=githubactions&amp;logoColor=white&amp;label=Build"></a>
  <a href="https://github.com/TheNINJALLO/endstone-server-optimizer/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/TheNINJALLO/endstone-server-optimizer?display_name=tag&amp;style=for-the-badge&amp;label=Release"></a>
</p>

<p align="center">
  <img alt="Endstone 0.11.8" src="https://img.shields.io/badge/Endstone-0.11.8-52b7a8?style=flat-square">
  <img alt="API 0.11" src="https://img.shields.io/badge/API-0.11-63b8ff?style=flat-square">
  <img alt="BDS 1.26.40" src="https://img.shields.io/badge/BDS-1.26.40-8b7dff?style=flat-square">
  <img alt="Python >=3.10" src="https://img.shields.io/badge/Python-%3E=3.10-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white">
</p>

<p align="center">
  <strong>Server optimization plugin for Endstone Minecraft Bedrock.</strong>
</p>

<p align="center">
  <a href="#what-it-does">What it does</a> &bull;
  <a href="#how-to-use">How to use</a> &bull;
  <a href="#commands-and-permissions">Commands</a> &bull;
  <a href="#install">Install</a> &bull;
  <a href="https://github.com/TheNINJALLO/endstone-server-optimizer/releases">Releases</a>
</p>

## Overview

Server optimization plugin for Endstone Minecraft Bedrock. This release is aligned with Endstone 0.11.8 and Minecraft Bedrock Dedicated Server 1.26.40, and is distributed as a Python wheel for direct installation in an Endstone server.

## What it does

- Monitors TPS and lag indicators and applies configurable cleanup and load-reduction actions.
- Controls entity cleanup, packet-related optimizations, player view distance, and automatic thresholds.
- Provides read-only status commands for players and guarded configuration commands for operators.

## How to use

1. Start once, back up the generated configuration, and review every cleanup whitelist before enabling aggressive actions.
2. Use `/tps`, `/lag`, and `/optimize status` to establish a baseline under normal load.
3. Tune view distance and TPS thresholds gradually, then verify gameplay before enabling full or packet optimizations.
4. Use `/soconfig save` after validated changes and `/soconfig reload` after manual edits.

## Commands and permissions

| Command / usage | What it does | Access |
|---|---|---|
| `/optimize`<br>`/optimize (status\|full\|packets)[action: OptAction]`<br>`/optimize view <player: player>`<br><sub>Aliases: `/opt`, `/perf`</sub> | Server optimization controls | `serveropt.command.optimize` |
| `/tps` | Check server TPS | `serveropt.command.tps` |
| `/lag` | View lag information | `serveropt.command.lag` |
| `/viewdistance`<br>`/viewdistance <distance: int>`<br>`/viewdistance auto`<br><sub>Aliases: `/vd`</sub> | Manage view distance | `serveropt.command.viewdistance` |
| `/tpsthreshold`<br>`/tpsthreshold (critical\|warning\|target)<type: TpsType> <value: float>`<br><sub>Aliases: `/tpst`</sub> | Adjust TPS thresholds for optimization triggers | `serveropt.command.tpsthreshold` |
| `/soconfig`<br>`/soconfig (reload\|reset\|save)[action: ConfigAction]`<br>`/soconfig set <key: str> <value: str>`<br>`/soconfig whitelist`<br>`/soconfig whitelist <action: str> <entity_type: str>`<br><sub>Aliases: `/soc`</sub> | Manage plugin configuration | `serveropt.command.config` |

## Compatibility

| Component | Supported version |
|---|---|
| Endstone | `0.11.8` |
| Endstone API | `0.11` |
| Bedrock Dedicated Server | `1.26.40` |
| Python | `>=3.10` |
| Plugin release | `v2.2.2` |

## Install

Download the wheel from the matching GitHub release:

```bash
gh release download v2.2.2 --repo TheNINJALLO/endstone-server-optimizer --pattern "*.whl"
```

Copy the downloaded wheel into the server's `plugins/` directory, remove any older wheel for the same plugin, and restart Endstone.

> [!IMPORTANT]
> Use Endstone `0.11.8` with BDS `1.26.40`. Back up worlds and plugin data before upgrading a production server.

## Configuration and secrets

Runtime databases, logs, local `.env` files, server directories, and root `config.toml` files are excluded from source releases. When an example configuration is provided, copy it locally and keep live tokens, passwords, webhook URLs, and server identifiers out of Git.

## Release automation

Every `v*` tag runs [the wheel release workflow](.github/workflows/wheel-release.yml), builds the package in a clean GitHub runner, stores the wheel as a workflow artifact, and attaches it to the matching GitHub release.
<!-- endstone-professional-header:end -->
