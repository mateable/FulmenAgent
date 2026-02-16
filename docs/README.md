# Agent Network Documentation

Setup guides and reference docs for all plugins and integrations.

---

## Dashboard Features

| Guide | What It Covers |
|-------|---------------|
| [Features Guide](features-guide.md) | Agent Templates, Webhooks, Notifications, Broadcast, Scheduled Tasks |

## Plugin Guides

| Guide | What It Covers |
|-------|---------------|
| [Voice & Phone Setup](voice-phone-setup.md) | Twilio phone calls, SMS, inbound voice, Edge TTS |
| [GibberLink Protocol](gibberlink-setup.md) | AI-to-AI detection, compressed messaging, token savings |
| [Beads Task Memory](beads-task-memory-setup.md) | Persistent tasks, dependencies, multi-agent coordination |

## Built-in Plugins (No Setup Needed)

| Plugin | Description |
|--------|-------------|
| **My Weather Plugin** | Weather data via Open-Meteo API (temperature, rain, UV, forecasts) |
| **Exa Search Plugin** | AI-powered web search via Exa.ai (requires EXA_API_KEY in Admin Settings) |
| **GibberLink Protocol** | Auto-loads, no config needed for text mode. Phone mode needs Twilio |
| **Beads Task Memory** | Requires `npm install -g @beads/bd` and `bd init` in project root |

## Quick Links

- **Dashboard**: `http://127.0.0.1:5000`
- **Admin Settings**: Dashboard → Admin Settings tab
- **Plugin Management**: Dashboard → Plugins tab
- **Hub Logs**: `agent_network/agent_debug.log`
- **Environment Config**: `/root/agent/.env`
