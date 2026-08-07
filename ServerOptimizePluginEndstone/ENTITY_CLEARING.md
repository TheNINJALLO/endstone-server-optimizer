# How Server Optimizer Works 🚀

This plugin helps keep your Minecraft server running fast and smooth! Here's everything it does:

---

## 📊 Watching the Server Speed (TPS)

The plugin watches how fast your server is running. A healthy server runs at **20 TPS** (ticks per second).

- **Green (19-20):** Everything is great! ✅
- **Yellow (16-19):** Still good, but watching closely 👀
- **Orange (13-16):** Getting slow, time to help out ⚠️
- **Red (below 13):** Emergency mode! 🚨

---

## 👁️ Smart View Distance

View distance is how far you can see in the game. The plugin makes it bigger or smaller based on how the server is doing:

- **Server running great (19.5+ TPS)?** → You can see farther! 🔭
- **Server getting really slow (below 15 TPS)?** → See a bit less, but no lag!

This happens automatically and only adjusts during real performance issues, not normal gameplay.

---

## 🧹 Cleaning Up Stuff

### The Auto Cleanup (Every 15 Minutes)
The plugin counts things like items, arrows, XP orbs, and mobs. If there are way too many (900+ items, 600+ mobs), it removes the oldest ones. Normal gameplay won't hit these limits.

### The Big Cleanup (Every Hour)
Once an hour, the plugin does a deep clean and removes:
- ✅ All items on the ground
- ✅ All arrows
- ✅ All XP orbs
- ✅ All mobs

**But it NEVER removes:**
- 🚂 Minecarts
- 🚤 Boats
- 🏷️ **Anything with a name tag!**
- 📋 **Anything on the entity whitelist!**

Your named pets, villagers, and animals are always safe! 🛡️

### Entity Whitelist
You can add specific entity types to a whitelist so they are **never** removed during any cleanup. This is useful for custom entities like decorations, Easter eggs, or special mobs.

**Commands:**
| Command | What It Does |
|---------|--------------|
| `/soconfig whitelist` | View the current whitelist |
| `/soconfig whitelist add <entity_type>` | Add an entity type to the whitelist |
| `/soconfig whitelist remove <entity_type>` | Remove an entity type from the whitelist |
| `/soconfig whitelist clear` | Clear the entire whitelist |

**Example:** `/soconfig whitelist add ninjos:blue_easter_egg`

The whitelist is saved to `config.json` and persists across restarts. You can also edit the `entity_whitelist` array in the config file directly.

---

## 🧠 Memory Cleanup

Every 5 minutes, the plugin cleans up the server's memory (like clearing your computer's RAM). This helps prevent crashes!

---

## 🚨 Emergency Mode

If the server gets really slow (below 13 TPS), the plugin activates **Emergency Mode**:

1. Makes view distance as small as possible
2. Does an aggressive cleanup of everything
3. Frees up memory

After 5 minutes, things go back to normal. This saves your server from crashing!

---

## 😴 AFK Detection

The plugin notices when players are standing still and not doing anything (AFK = Away From Keyboard). This helps the server know how much work it really needs to do.

---

## 📈 Health Score

The plugin gives your server a **health score from 0-100**:
- **100:** Perfect health! 💚
- **75-99:** Doing great 💛
- **50-74:** Could be better 🧡
- **Below 50:** Needs help ❤️

---

## 🎮 Commands You Can Use

| Command | What It Does |
|---------|--------------|
| `/tps` | See how fast the server is running |
| `/lag` | Get a quick lag report |
| `/optimize status` | See detailed server health |
| `/optimize full` | Run a manual cleanup |
| `/optimize packets` | View packet throttle stats |
| `/soconfig` | View/edit configuration |

---

## Why Do We Need This?

Think of your server like a room:
- Too many toys (entities) = hard to walk around
- Too much stuff in your head (memory) = hard to think
- Trying to see everything at once (view distance) = overwhelming

This plugin cleans up, organizes, and helps your server work its best! 🎮✨
