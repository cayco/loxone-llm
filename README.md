# Loxone LLM Bridge 🏠🤖

> OpenAI-compatible AI Agent bridge for Loxone Miniserver, enabling natural voice and text control in English and Polish via Home Assistant, Siri, iOS Shortcuts, and Apple Watch.

[English (EN)](README.md) | [Polski (PL)](README_PL.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://www.python.org/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-LiteLLM-blue)](https://www.home-assistant.io/integrations/litellm)
[![Loxone](https://img.shields.io/badge/Loxone-Miniserver%20Gen%201%2F2-brightgreen)](https://www.loxone.com/)

---

## 📐 Architecture

```mermaid
flowchart TD
    subgraph AppleEcosystem["Apple Ecosystem"]
        Siri["Siri Voice / Shortcuts"]
        Watch["Apple Watch (Assist Complication)"]
        ActionButton["Action Button / Back Tap"]
    end

    subgraph HomeAssistant["Home Assistant"]
        HACore["Home Assistant Core"]
        VoiceAssist["Voice Assist Pipeline (English or Polish)"]
        LiteLLM["LiteLLM Conversation Integration"]
    end

    subgraph BridgeHost["LXC Container / Linux Host"]
        Bridge["Loxone LLM Bridge\n(server.py / port 8000)"]
        MCP["Loxone MCP Server\n(avrabe/mcp-loxone / port 3001)"]
    end

    subgraph Cloud["Cloud LLM Provider"]
        Gemini["Google Gemini\n(gemini-3.6-flash)\nFunction Calling Loop"]
    end

    subgraph SmartHome["Smart Home Hardware"]
        Miniserver["Loxone Miniserver\n(LightControllerV2, Ventilation,\nJalousie, Sensors, HVAC)"]
    end

    Siri -->|Voice / Text| VoiceAssist
    Watch -->|Native Audio| VoiceAssist
    ActionButton -->|Direct Shortcut| VoiceAssist
    VoiceAssist --> LiteLLM
    LiteLLM -->|OpenAI HTTP API /v1| Bridge
    Bridge <-->|Chat + Tools Schema| Gemini
    Bridge <-->|MCP Tools JSON-RPC| MCP
    MCP -->|HTTP / WebSocket API| Miniserver
    Bridge -.->|Direct HTTP Fallback / setTimer| Miniserver
```

---

## ✨ Features

- **🌐 Multi-Language Support**:
  - English is the default (`LANGUAGE=en`).
  - Native Polish support (`LANGUAGE=pl`).
  - Response formatting tuned specifically for voice synthesizers (clean conversational text without markdown asterisks, hashes, or tables).
- **💡 Smart Lighting & Gen 2 Moods**:
  - Room-level control: *"Turn on lights in the living room"*, *"Turn off bedroom lights"*.
  - Full support for Loxone Gen 2 (`LightControllerV2`) predefined scenes and moods: *"Evening in the living room"*, *"Night mode in the bedroom"*, *"Dining mode"*, *"Xbox mode"*, *"Bright lights in kitchen"*.
- **🍃 Ventilation & Recuperation with Timers**:
  - Control room units or whole-house ventilation: Office, Kitchen, Bedroom, Entire house.
  - Native Loxone timers with duration: *"Air out the kitchen for 30 minutes"*, *"Set ventilation to 60% for 1 hour"*, *"Turn off ventilation for 2 hours"*.
  - Automatic fallback to safe auto mode after the timer expires.
- **🪟 Blinds & Shading**:
  - Full support for Jalousie blocks: *"Close living room blinds"*, *"Open office blinds"*.
- **📊 Real-time Sensor & Energy Inquiries**:
  - Digital window/door contact sensors: *"Which windows are open?"*.
  - Live power meters: *"How much power are we using right now?"*.
  - Temperatures, humidity, and room controllers: *"What is the temperature in the office?"*.
- **🔒 Zero Leakage Security**:
  - Miniserver credentials stay strictly on your local network.
  - Fully configurable via environment variables (`.env`). No usernames or passwords are hardcoded.

---

## 🛠️ Prerequisites

- **Loxone Miniserver** (Gen 1 or Gen 2) with local network access.
- **Home Assistant** (2024.1+) with the official [LiteLLM integration](https://www.home-assistant.io/integrations/litellm).
- **Google Gemini API Key** (from [Google AI Studio](https://aistudio.google.com/)).
- **Linux host or Proxmox LXC Container** (Debian 12/13 or Ubuntu).

---

## 🚀 Installation & Setup

### 1. Set up the Loxone MCP Server (`mcp-loxone`)

Clone and build the Loxone MCP Server (using the Gen 2 patch from [PR #41](https://github.com/avrabe/mcp-loxone/pull/41)):

```bash
git clone https://github.com/cayco/mcp-loxone.git /opt/mcp-loxone
cd /opt/mcp-loxone
cargo build --release
cp target/release/loxone-mcp-server /usr/local/bin/
```

Create `/etc/mcp-loxone/mcp-loxone.env`:
```ini
LOXONE_HOST=192.168.1.100
LOXONE_USER=your_loxone_username
LOXONE_PASS=your_loxone_password
```

Install and start the systemd service:
```bash
cp systemd/mcp-loxone.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mcp-loxone.service
```

### 2. Set up the Loxone LLM Bridge

Clone this repository to `/opt/loxone-agent`:

```bash
git clone https://github.com/cayco/loxone-llm.git /opt/loxone-agent
cd /opt/loxone-agent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create configuration file `/etc/loxone-agent/agent.env`:
```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.6-flash
LANGUAGE=en
PORT=8000
MCP_URL=http://127.0.0.1:3001/mcp
LOXONE_HOST=192.168.1.100
LOXONE_USER=your_loxone_username
LOXONE_PASS=your_loxone_password
```

Install and start the service:
```bash
cp systemd/loxone-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now loxone-agent.service
```

Verify service health:
```bash
curl http://127.0.0.1:8000/v1/models
```

---

## 🔍 How to Find Loxone Mood IDs and Control UUIDs

In Loxone Gen 2 installations, the `LightControllerV2` block only accepts integer mood IDs via `changeTo/<id>` (e.g. `changeTo/1` for Evening, `changeTo/2` for Dining). Special moods built into Loxone have standard IDs:
- **`777`**: Bright / Full on (`jasno` / `bright`)
- **`778`**: Off (`wyłącz` / `off`)

To discover the exact IDs and UUIDs for your setup:

### Method 1: Query Miniserver Structure JSON via Web Browser
Open the following URL in your browser (substituting your credentials and Miniserver IP):
```
http://<LOXONE_USER>:<LOXONE_PASS>@<LOXONE_HOST>/data/LoxAPP3.json
```
1. Search the JSON for `"type": "LightControllerV2"`.
2. Locate the controller for your room (e.g. Living Room / Salon).
3. The block's UUID is under the `"uuidAction"` field (e.g. `1dbbcc92-01ab-5571-ffffba1e5675f352`).
4. Look under the `"details"` object:
   - `"moods"` contains an array of mood objects with `"id"` (e.g. `1`, `2`, `3`) and `"name"` (e.g. `"Wieczór"`, `"Jedzenie"`, `"Xbox"`).

### Method 2: Inspect via Loxone Config
1. Open your project file in **Loxone Config**.
2. Click on the **Light Controller V2** block for the desired room.
3. In the block properties or when double-clicking to configure moods, each mood entry displays its sequence index/ID.
4. The control's UUID is displayed in the lower properties pane under **Identification**.

### Configure `moods.json`
Copy [`moods.example.json`](moods.example.json) to `moods.json` and fill in your discovered UUIDs and IDs:
```json
{
  "light_moods": {
    "living room": {
      "uuid": "YOUR_LIGHT_CONTROLLER_UUID",
      "moods": {
        "bright": 777,
        "off": 778,
        "evening": 1,
        "dining": 2,
        "night": 3,
        "xbox": 4
      }
    }
  },
  "ventilation_controls": {
    "office": "YOUR_VENTILATION_UUID",
    "kitchen": "YOUR_VENTILATION_UUID"
  }
}
```

---

## 🏡 Home Assistant Configuration

1. In Home Assistant, navigate to **Settings** $\rightarrow$ **Devices & Services** $\rightarrow$ **Add Integration**.
2. Search for **LiteLLM**.
3. Fill in the connection settings:
   - **API Base**: `http://<YOUR_BRIDGE_IP>:8000/v1`
   - **API Key**: `dummy` (or any string)
4. Navigate to **Settings** $\rightarrow$ **Voice Assistants** $\rightarrow$ **Home Assistant**:
   - Set **Conversation Agent** to **LiteLLM**.
   - Set **Language** to your preferred language (e.g. **English** or **Polish**).

---

## 📱 iOS, Siri & Apple Watch Setup

### Option A: Apple Watch Assist Complication (Recommended for Apple Watch)
1. Install the **Home Assistant** app on Apple Watch.
2. Add the **Assist** complication to your watch face.
3. Tapping the complication opens the native microphone listening in your chosen language and routes directly to the agent.

### Option B: iOS Action Button / Back Tap
1. On iPhone 15 Pro / 16: Go to **Settings** $\rightarrow$ **Action Button** $\rightarrow$ assign a Shortcut running Home Assistant Assist.
2. On any iPhone: Go to **Settings** $\rightarrow$ **Accessibility** $\rightarrow$ **Touch** $\rightarrow$ **Back Tap** (double or triple tap).

---

## 🗣️ Example Voice Commands

| Category | Example Command | Action / Description |
|---|---|---|
| **Lighting** | *"Turn on lights in the office"* | Turns on lighting in the specified room |
| **Moods / Scenes** | *"Evening mode in the living room"* | Activates Evening mood (ID 1) on LightControllerV2 |
| **Moods / Scenes** | *"Dining mode in living room"* | Activates Dining mood (ID 2) |
| **Moods / Scenes** | *"Night in bedroom"* | Activates Night scene in the bedroom |
| **Ventilation** | *"Air out the office"* | Runs 100% boost speed for default 15 minutes |
| **Ventilation with Timer** | *"Air out kitchen for 30 minutes"* | Sets airing timer for 30 minutes |
| **Ventilation** | *"Set ventilation to 60% for an hour"* | Sets speed to 60% for 60 minutes |
| **Ventilation** | *"Set ventilation to automatic"* | Resets ventilation units back to automatic mode |
| **Blinds / Shading** | *"Close living room blinds"* | Lowers living room blinds (`FullDown`) |
| **Sensors** | *"Which windows are open?"* | Checks open door/window reed sensors |
| **Energy** | *"How much power are we using right now?"* | Queries live power draw from main energy meter |

---

## 📄 License

This project is open-source under the [MIT License](LICENSE).
