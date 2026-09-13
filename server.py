#!/usr/bin/env python3
"""
Loxone LLM Bridge (OpenAI-compatible Agent for Home Assistant & Siri)
Enables voice and text control of Loxone Miniservers using LLMs (Google Gemini / OpenAI)
and MCP (Model Context Protocol). Supports English (default) and Polish.
Includes full support for:
- Lighting & Gen 2 Moods (LightControllerV2)
- Multiroom Audio (AudioZone: play, pause, volume, mute)
- Air Conditioning (AcControl: on/off, cooling, heating, target temperature)
- Timed Ventilation & Recuperation (Ventilation with setTimer)
- Blinds & Shading (Jalousie)
- Robot Vacuums / Cleaning (Pushbutton triggers for Mietek, Mopek, Kitchen, Hallway)
- Garden & Balcony Irrigation (Irrigation, Pushbutton, Switch)
- Automated Roof / Façade Windows (Open / Close pushbuttons)
- Smart Home Switches & Modes (Away mode, Snow block, Hot/Cold water)
- Entrance, Intercom & Gates (Intercom pulse outputs)
- Sensors, Burglar Alarm & Power Meters
"""

import os
import json
import time
import uuid
import logging
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Request
from openai import AsyncOpenAI
import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("loxone-agent")

# Configuration from Environment
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:3001/mcp")
PORT = int(os.getenv("PORT", "8000"))
DEFAULT_LANGUAGE = os.getenv("LANGUAGE", "en").lower().strip()

LOX_HOST = os.getenv("LOXONE_HOST", "127.0.0.1")
LOX_USER = os.getenv("LOXONE_USER", "")
LOX_PASS = os.getenv("LOXONE_PASS", "")

# Check optional fallback env files if present
for env_file in ["/etc/loxone-agent/agent.env", "/etc/mcp-loxone/mcp-loxone.env"]:
    if os.path.exists(env_file):
        try:
            with open(env_file) as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        k, v = line.strip().split("=", 1)
                        clean_v = v.strip("'\"")
                        if k == "GEMINI_API_KEY" and not GEMINI_API_KEY: GEMINI_API_KEY = clean_v
                        elif k == "GEMINI_MODEL" and not os.getenv("GEMINI_MODEL"): GEMINI_MODEL = clean_v
                        elif k == "MCP_URL" and not os.getenv("MCP_URL"): MCP_URL = clean_v
                        elif k == "LOXONE_HOST" and not os.getenv("LOXONE_HOST"): LOX_HOST = clean_v
                        elif k == "LOXONE_USER" and not LOX_USER: LOX_USER = clean_v
                        elif k == "LOXONE_PASS" and not os.getenv("LOXONE_PASS"): LOX_PASS = clean_v
                        elif k == "LANGUAGE" and not os.getenv("LANGUAGE"): DEFAULT_LANGUAGE = clean_v.lower().strip()
        except Exception as e:
            logger.warning(f"Could not read {env_file}: {e}")

app = FastAPI(title="Loxone LLM Agent Bridge")
gemini_client: Optional[AsyncOpenAI] = None

# System Instructions by language
SYSTEM_INSTRUCTIONS = {
    "en": (
        "You are an intelligent voice assistant for a Loxone smart home named Home. "
        "You respond to the user and control the home using available Loxone tools. "
        "Rules: "
        "1. Always respond in English. "
        "2. Responses will be read aloud by a phone/watch text-to-speech engine, so keep them natural, brief, and concise. "
        "3. Do not use markdown formatting (no asterisks, bolding, tables, or numbered lists). "
        "4. When the user requests an action, ALWAYS call the appropriate tool first: "
        "   - turn on/off or dim lights: control_lights "
        "   - activate lighting mood or scene (e.g. 'evening in living room', 'night', 'dining', 'xbox', 'bright'): activate_scene(scene=..., room=...) "
        "   - control multiroom music/audio: control_audio(room=..., action='play'|'pause'|'stop'|'volume'|'volumedown'|'volumeup'|'next'|'prev', volume=...) "
        "   - control air conditioning: control_ac(room=..., action='on'|'off'|'cool'|'heat'|'auto', target_temp=...) "
        "   - control ventilation/recuperation/airing: control_ventilation(action=..., room=..., duration_minutes=..., speed=...) "
        "   - control blinds/shading/shutters: control_blinds "
        "   - robot vacuum cleaners / cleaning: control_cleaning(target='mietek'|'mopek'|'kuchnia'|'korytarz'|'all', action='start'|'stop') "
        "   - garden/balcony watering: control_irrigation(target='balkon'|'zbiornik'|'kran'|'all', action='on'|'off'|'pulse') "
        "   - open/close automated roof/facade windows: control_windows(action='open'|'close') "
        "   - intercom, gate and door opener: control_intercom(action='open_gate'|'open_wicket') "
        "   - smart home switches & modes: control_switch(switch_name='poza domem'|'ciepla woda'|'blokada sniegowa'|'okap', state='on'|'off'|'toggle') "
        "   - set target room temperature for heating: set_temperature "
        "5. When the user asks for airing or changing ventilation for a specific duration (e.g. 'for 20 minutes', 'for half an hour', 'for an hour', 'for 2 hours'), ALWAYS specify that in duration_minutes. "
        "6. Sensor, energy, power meters, window, door, and burglar alarm information is available in get_sensor_readings. "
        "7. Always conclude with a single short spoken confirmation sentence (e.g. 'Turned on music in the kitchen.', 'Started kitchen cleaning with the robot.', 'Activated evening mood in the living room.')."
    ),
    "pl": (
        "Jestes inteligentnym asystentem inteligentnego domu Loxone o nazwie Dom. "
        "Odpowiadasz uzytkownikowi i sterujesz domem za pomoca dostepnych narzedzi Loxone. "
        "Zasady: "
        "1. Odpowiadaj zawsze w jezyku polskim. "
        "2. Odpowiedz bedzie czytana na glos przez syntezator mowy w telefonie, wiec powinna byc naturalna, zwiezla i konkretna. "
        "3. Nie uzywaj formatowania markdown (gwiazdek, pogrubien, tabel, list). "
        "4. Gdy uzytkownik prosi o akcje, ZAWSZE najpierw wywolaj odpowiednie narzedzie: "
        "   - wlaczenie/wylaczenie/sciemnienie swiatla: control_lights "
        "   - wlaczenie nastroju/trybu swiatla (np. 'wieczor w salonie', 'noc', 'jedzenie', 'xbox', 'jasno', 'zasypianie', 'pobudka'): activate_scene(scene=..., room=...) "
        "   - sterowanie muzyka / multiroom audio: control_audio(room=..., action='play'|'pause'|'stop'|'volume'|'volumedown'|'volumeup'|'next'|'prev', volume=...) "
        "   - sterowanie klimatyzacja (AC): control_ac(room=..., action='on'|'off'|'cool'|'heat'|'auto', target_temp=...) "
        "   - sterowanie wentylacja/rekuperacja/wietrzeniem: control_ventilation(action=..., room=..., duration_minutes=..., speed=...) "
        "   - sterowanie roletami/zaluzjami: control_blinds "
        "   - roboty sprzatajace / odkurzanie (Mietek, Mopek, sprzatanie kuchni, korytarza): control_cleaning(target='mietek'|'mopek'|'kuchnia'|'korytarz'|'all', action='start'|'stop') "
        "   - podlewanie ogrodu i balkonu: control_irrigation(target='balkon'|'zbiornik'|'kran'|'all', action='on'|'off'|'pulse') "
        "   - otwieranie / zamykanie okien dachowych i fasadowych: control_windows(action='open'|'close') "
        "   - domofon, otwieranie furtki lub bramy: control_intercom(action='open_gate'|'open_wicket') "
        "   - przelaczniki i tryby domowe (np. poza domem, ciepla woda, blokada sniegowa, okap): control_switch(switch_name=..., state='on'|'off'|'toggle') "
        "   - ustawienie temperatury kaloryferow / ogrzewania: set_temperature "
        "5. Gdy uzytkownik prosi o wietrzenie lub zmiane wentylacji na okreslony czas (np. 'na 20 minut', 'na pol godziny', 'na godzine', 'na 2 godziny'), ZAWSZE podaj ten czas w duration_minutes. "
        "6. Informacje o stanie okien, drzwi, energii, alarmu i czujnikach sa dostepne w get_sensor_readings. "
        "7. Zawsze na koniec odpowiedz krotkim zdaniem potwierdzajacym wykonanie akcji (np. 'Wlaczylem muzyke w kuchni.', 'Wyslalem Mietka do sprzatania kuchni.', 'Wlaczylem tryb wieczor w salonie.')."
    )
}

DEFAULT_FALLBACK_TEXT = {
    "en": "Command executed.",
    "pl": "Wykonano polecenie."
}

def normalize_text(text: str) -> str:
    """Normalize text by converting to lowercase and stripping Polish diacritics."""
    replacements = {
        "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
        "ó": "o", "ś": "s", "ź": "z", "ż": "z",
        "Ą": "a", "Ć": "c", "Ę": "e", "Ł": "l", "Ń": "n",
        "Ó": "o", "Ś": "s", "Ź": "z", "Ż": "z"
    }
    s = text.lower().strip()
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s

# Optional external configuration from moods.json / config file
CONFIG_MOODS_FILE = os.getenv("MOODS_CONFIG_FILE", "moods.json")
LIGHT_MOODS: Dict[str, Any] = {}
VENTILATION_CONTROLS: Dict[str, str] = {}
AUDIO_ZONES: Dict[str, str] = {}
AC_UNITS: Dict[str, str] = {}
CLEANING_COMMANDS: Dict[str, str] = {}
IRRIGATION_CONTROLS: Dict[str, str] = {}
SWITCHES: Dict[str, str] = {}
WINDOWS_CONTROLS: Dict[str, str] = {}
INTERCOM_CONTROLS: Dict[str, str] = {}
ALARM_CONTROLS: Dict[str, str] = {}

def load_custom_config():
    global LIGHT_MOODS, VENTILATION_CONTROLS, AUDIO_ZONES, AC_UNITS, CLEANING_COMMANDS
    global IRRIGATION_CONTROLS, SWITCHES, WINDOWS_CONTROLS, INTERCOM_CONTROLS, ALARM_CONTROLS

    # Try specific config file or fallback to moods.json in same dir
    paths = [CONFIG_MOODS_FILE, "moods.json", "/etc/loxone-agent/moods.json"]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    cfg = json.load(f)
                    LIGHT_MOODS = cfg.get("light_moods", {})
                    VENTILATION_CONTROLS = cfg.get("ventilation_controls", {})
                    AUDIO_ZONES = cfg.get("audio_zones", {})
                    AC_UNITS = cfg.get("ac_units", {})
                    CLEANING_COMMANDS = cfg.get("cleaning_commands", {})
                    IRRIGATION_CONTROLS = cfg.get("irrigation", {})
                    SWITCHES = cfg.get("switches", {})
                    WINDOWS_CONTROLS = cfg.get("windows_control", {})
                    INTERCOM_CONTROLS = cfg.get("intercom", {})
                    ALARM_CONTROLS = cfg.get("alarm", {})
                    logger.info(f"Loaded hardware configuration from {p}")
                    return
            except Exception as e:
                logger.warning(f"Failed to load {p}: {e}")

DEFAULT_LIGHT_CONTROLLERS: Dict[str, str] = {
    "filip": "1db945ce-00fe-d71c-ffffba1e5675f352",
    "gabinet": "1dbd1f79-031e-9ee5-ffffba1e5675f352",
    "garderoba": "1db9e18d-007c-3e37-ffffba1e5675f352",
    "korytarz dol": "1c6685f6-02a4-3bc0-ffffba1e5675f352",
    "korytarz gora": "1dbd2d05-0227-ed46-ffffba1e5675f352",
    "kuchnia": "1db9e174-0358-a125-ffffba1e5675f352",
    "maciek": "1db7ed2e-03d9-9ec8-ffffba1e5675f352",
    "salon": "1dbbcc92-01ab-5571-ffffba1e5675f352",
    "stol": "1e144db2-0312-a5ad-ffffba1e5675f352",
    "sypialnia": "1db9e05e-000c-e8aa-ffffba1e5675f352",
    "toaleta dol": "1c6a8743-026d-df46-ffffba1e5675f352",
    "toaleta gora": "1c7660b0-0005-0109-ffffba1e5675f352",
    "lazienka gora": "1db8e327-02e5-e977-ffffba1e5675f352"
}

load_custom_config()

def get_custom_tools_def(lang: str = "en") -> List[Dict[str, Any]]:
    is_pl = (lang == "pl")
    return [
        # 0. Lighting control with whole-house & exclusion support
        {
            "type": "function",
            "function": {
                "name": "control_lights",
                "description": (
                    "Włączanie lub wyłączanie oświetlenia w jednym pomieszczeniu, wielu pomieszczeniach lub w całym domu, z opcją wykluczenia wybranych pokoi (np. 'wyłącz światła w całym domu oprócz korytarza')."
                    if is_pl else
                    "Turn on or off lighting in a specific room, multiple rooms, or whole house, with optional excluded rooms."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "room": {
                            "type": "string",
                            "description": "Room name (e.g. 'salon', 'kuchnia', 'gabinet') or 'all' / 'caly dom' for the whole house"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["on", "off", "bright"],
                            "description": "Action: 'off' (turn off), 'on' or 'bright' (turn on)"
                        },
                        "exclude": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Rooms to skip/exclude (e.g. ['korytarz', 'korytarz dol'])"
                        }
                    },
                    "required": ["action"]
                }
            }
        },
        # 1. Ventilation with duration
        {
            "type": "function",
            "function": {
                "name": "control_ventilation",
                "description": (
                    "Sterowanie wentylacją i rekuperacją z możliwością określenia czasu trwania w minutach."
                    if is_pl else
                    "Control ventilation and recuperation units by room or whole house with optional duration timer."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "room": {
                            "type": "string",
                            "description": "Room name (e.g. 'Gabinet', 'Kuchnia', 'Sypialnia', 'Office', 'Kitchen') or 'all'"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["boost", "airing", "off", "resting", "auto", "set_speed"],
                            "description": "Action: boost/airing (100% boost), off/resting, auto (automatic mode), set_speed"
                        },
                        "duration_minutes": {
                            "type": "integer",
                            "description": "Duration in minutes (e.g. 15, 20, 30, 45, 60, 120)."
                        },
                        "speed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Fan speed percentage (0-100) for set_speed action"
                        }
                    },
                    "required": ["action"]
                }
            }
        },
        # 2. Multiroom Audio
        {
            "type": "function",
            "function": {
                "name": "control_audio",
                "description": (
                    "Sterowanie strefami audio i odtwarzaniem muzyki w pomieszczeniach (Salon, Kuchnia, Sypialnia, Gabinet, Korytarz, Łazienka)."
                    if is_pl else
                    "Control multiroom audio zones and music playback (Living room, Kitchen, Bedroom, Office, Hallway, Bathroom)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "room": {
                            "type": "string",
                            "description": "Room name (e.g. 'salon', 'kuchnia', 'sypialnia', 'gabinet', 'all')"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["play", "pause", "stop", "toggle", "next", "prev", "volume", "volumeup", "volumedown", "mute", "unmute"],
                            "description": "Audio action to perform"
                        },
                        "volume": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Volume percentage (0-100) when action is 'volume'"
                        }
                    },
                    "required": ["action"]
                }
            }
        },
        # 3. Air Conditioning (AC)
        {
            "type": "function",
            "function": {
                "name": "control_ac",
                "description": (
                    "Sterowanie klimatyzacją (AC) w pokojach (Salon, Sypialnia, Gabinet, Filip, Maciek): włączanie, wyłączanie, chłodzenie, grzanie, temperatura docelowa."
                    if is_pl else
                    "Control air conditioning (AC) units in rooms (Salon, Sypialnia, Gabinet, Filip, Maciek): power on/off, cooling, heating, target temperature."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "room": {
                            "type": "string",
                            "description": "Room or unit name (e.g. 'salon', 'sypialnia', 'gabinet', 'filip', 'maciek', 'all')"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["on", "off", "cool", "heat", "auto", "set_temp"],
                            "description": "AC mode or power action"
                        },
                        "target_temp": {
                            "type": "number",
                            "description": "Target cooling/heating temperature in Celsius (e.g. 21.0, 22.5)"
                        }
                    },
                    "required": ["action"]
                }
            }
        },
        # 4. Robot Vacuums & Cleaning
        {
            "type": "function",
            "function": {
                "name": "control_cleaning",
                "description": (
                    "Uruchamianie sprzątania robotami (Mietek odkurzacz, Mopek mopowanie) lub sprzątanie konkretnych stref (kuchnia, korytarz na dole)."
                    if is_pl else
                    "Trigger robot vacuum and mop cleaning (Mietek vacuum, Mopek mop, or room cleaning for kitchen and hallway)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": ["mietek", "mopek", "kuchnia", "korytarz", "all"],
                            "description": "Target robot or zone to clean: mietek (vacuum), mopek (mop), kuchnia (kitchen), korytarz (hallway)"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["start", "stop", "pulse"],
                            "description": "Cleaning action (default is 'start')"
                        }
                    },
                    "required": ["target"]
                }
            }
        },
        # 5. Garden & Balcony Irrigation
        {
            "type": "function",
            "function": {
                "name": "control_irrigation",
                "description": (
                    "Sterowanie podlewaniem ogrodu i balkonu: podlewanie balkonowe, podlewanie ze zbiornika na deszczówkę lub z kranu."
                    if is_pl else
                    "Control garden and balcony irrigation: balcony watering, rainwater tank irrigation, or tap watering."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "enum": ["balkon", "zbiornik", "kran", "blokada", "all"],
                            "description": "Watering target: balkon (balcony), zbiornik (rainwater tank), kran (tap), blokada (disable/enable irrigation)"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["on", "off", "pulse"],
                            "description": "Action (default is 'pulse' for pushbutton, or 'on'/'off')"
                        }
                    },
                    "required": ["target"]
                }
            }
        },
        # 6. Roof & Façade Windows
        {
            "type": "function",
            "function": {
                "name": "control_windows",
                "description": (
                    "Automatyczne otwieranie lub zamykanie okien w domu."
                    if is_pl else
                    "Open or close automated house windows."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["open", "close"],
                            "description": "open (otwórz okna) or close (zamknij okna)"
                        }
                    },
                    "required": ["action"]
                }
            }
        },
        # 7. Intercom, Gate & Wicket
        {
            "type": "function",
            "function": {
                "name": "control_intercom",
                "description": (
                    "Sterowanie domofonem, otwieranie furtki lub bramy wjazdowej."
                    if is_pl else
                    "Control intercom system to open the gate or entrance wicket door."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["open_wicket", "open_gate"],
                            "description": "open_wicket (otwórz furtkę), open_gate (otwórz bramę)"
                        }
                    },
                    "required": ["action"]
                }
            }
        },
        # 8. Switches and System Modes
        {
            "type": "function",
            "function": {
                "name": "control_switch",
                "description": (
                    "Sterowanie przełącznikami i trybami domu: 'poza domem' (away mode), 'ciepla woda', 'zimna woda', 'blokada sniegowa', 'okap', 'otwarty balkon'."
                    if is_pl else
                    "Control smart home switches and operating modes: away mode ('poza domem'), hot water ('ciepla woda'), snow block, cooker hood, etc."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "switch_name": {
                            "type": "string",
                            "description": "Name of switch or mode (e.g. 'poza domem', 'ciepla woda', 'blokada sniegowa', 'okap')"
                        },
                        "state": {
                            "type": "string",
                            "enum": ["on", "off", "toggle"],
                            "description": "Target state (on, off, toggle)"
                        }
                    },
                    "required": ["switch_name", "state"]
                }
            }
        }
    ]

def get_client() -> AsyncOpenAI:
    global gemini_client
    key = os.getenv("GEMINI_API_KEY", GEMINI_API_KEY)
    if not key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not set")
    if gemini_client is None:
        gemini_client = AsyncOpenAI(
            api_key=key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
    return gemini_client

async def fetch_mcp_tools(lang: str = "en") -> List[Dict[str, Any]]:
    """Fetch available tools from the local Loxone MCP server."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = await client.post(MCP_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_tools = data.get("result", {}).get("tools", [])
        
        custom_defs = get_custom_tools_def(lang)
        custom_names = {c["function"]["name"] for c in custom_defs}

        openai_tools = []
        for t in raw_tools:
            # Avoid duplicate definitions if we provide a higher-level custom handler
            if t["name"] not in custom_names:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("inputSchema", {"type": "object", "properties": {}})
                    }
                })
        openai_tools.extend(custom_defs)
        return openai_tools

async def call_mcp_raw(name: str, arguments: Dict[str, Any]) -> Any:
    """Send JSON-RPC tool call to MCP server."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time()),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        }
        resp = await client.post(MCP_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return {"error": data["error"]}
        return data.get("result", {})

async def direct_miniserver_cmd(target_uuid: str, command: str) -> Dict[str, Any]:
    """Execute direct HTTP GET command on Loxone Miniserver."""
    url = f"http://{LOX_USER}:{LOX_PASS}@{LOX_HOST}/jdev/sps/io/{target_uuid}/{command}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        logger.info(f"Direct Loxone call {target_uuid}/{command} -> status {r.status_code}")
        if r.status_code == 200:
            return {"success": True, "status": 200, "response": r.json()}
        return {"success": False, "status": r.status_code, "error": r.text}

async def execute_mcp_tool(name: str, arguments: Dict[str, Any]) -> Any:
    """Execute tool call with smart room/mood/hardware handling and fallback."""
    # 0. Lighting control with whole-house & exclusion support
    if name == "control_lights":
        room = normalize_text(arguments.get("room", "") or "")
        action = str(arguments.get("action", "off")).lower()
        exclude_raw = arguments.get("exclude") or []
        if isinstance(exclude_raw, str):
            exclude_raw = [exclude_raw]
        exclude = [normalize_text(e) for e in exclude_raw if e]

        mood_id = 778 if action in ("off", "wylacz", "zgas") else 777

        all_controllers = dict(DEFAULT_LIGHT_CONTROLLERS)
        for r_k, r_cfg in LIGHT_MOODS.items():
            if isinstance(r_cfg, dict) and "uuid" in r_cfg:
                all_controllers[r_k] = r_cfg["uuid"]

        is_all = not room or room in ("all", "caly dom", "dom", "wszystko", "wszystkie", "caly", "domu")

        targets = []
        for r_name, u in all_controllers.items():
            norm_r = normalize_text(r_name)
            # Check exclusions
            if any(ex in norm_r or norm_r in ex for ex in exclude):
                logger.info(f"Skipping room '{r_name}' due to exclusion ({exclude})")
                continue
            if is_all or room in norm_r or norm_r in room:
                targets.append((r_name, u))

        results = []
        for r_name, u in targets:
            res = await direct_miniserver_cmd(u, f"changeTo/{mood_id}")
            results.append({"room": r_name, "command": f"changeTo/{mood_id}", "result": res})

        return {
            "success": True,
            "action": action,
            "mood_id": mood_id,
            "affected_rooms": [r[0] for r in targets],
            "results": results
        }

    # 1. Custom handling for ventilation with duration support
    if name == "control_ventilation":
        action = arguments.get("action", "auto")
        room = normalize_text(arguments.get("room", "") or "")
        speed = arguments.get("speed", 100)
        
        dur_min = arguments.get("duration_minutes")
        if dur_min is not None:
            try: dur_sec = int(dur_min) * 60
            except Exception: dur_sec = None
        elif "duration" in arguments and arguments["duration"] is not None:
            try: dur_sec = int(arguments["duration"])
            except Exception: dur_sec = None
        else:
            dur_sec = None

        targets = []
        if room in VENTILATION_CONTROLS:
            targets = [(room, VENTILATION_CONTROLS[room])]
        elif VENTILATION_CONTROLS:
            targets = list(VENTILATION_CONTROLS.items())

        if action in ("boost", "airing"):
            dur = dur_sec if dur_sec else 900
            cmd = f"setTimer/{dur}/100/2/1"
        elif action in ("off", "resting"):
            dur = dur_sec if dur_sec else 7200
            cmd = f"setTimer/{dur}/0/2/0"
        elif action in ("auto", "reset"):
            cmd = "setTimer/reset"
        elif action == "set_speed":
            dur = dur_sec if dur_sec else 3600
            cmd = f"setTimer/{dur}/{speed}/2/-1"
        else:
            cmd = "setTimer/reset"

        results = []
        for r_name, u in targets:
            res = await direct_miniserver_cmd(u, cmd)
            results.append({"room": r_name, "command": cmd, "result": res})
        return {"success": True, "action": action, "duration_minutes": dur // 60 if "dur" in locals() else None, "results": results}

    # 2. Multiroom Audio control
    if name == "control_audio":
        action = arguments.get("action", "play")
        room = normalize_text(arguments.get("room", "") or "")
        vol = arguments.get("volume")

        targets = []
        for z_name, u in AUDIO_ZONES.items():
            if not room or room in ("all", "wszystkie", "caly dom", "dom") or room in z_name or z_name in room:
                targets.append((z_name, u))

        if not targets and AUDIO_ZONES:
            targets = list(AUDIO_ZONES.items())

        cmd = "play"
        if action == "pause": cmd = "pause"
        elif action == "stop": cmd = "pause"
        elif action == "toggle": cmd = "pause"
        elif action == "next": cmd = "next"
        elif action == "prev": cmd = "prev"
        elif action == "volumeup": cmd = "volumeup"
        elif action == "volumedown": cmd = "volumedown"
        elif action == "mute": cmd = "mute"
        elif action == "unmute": cmd = "unmute"
        elif action == "volume" and vol is not None:
            cmd = f"volume/{vol}"

        results = []
        for z_name, u in targets:
            res = await direct_miniserver_cmd(u, cmd)
            results.append({"zone": z_name, "command": cmd, "result": res})
        return {"success": True, "action": action, "results": results}

    # 3. Air Conditioning (AC) control
    if name == "control_ac":
        action = arguments.get("action", "on")
        room = normalize_text(arguments.get("room", "") or "")
        target_temp = arguments.get("target_temp")

        targets = []
        for ac_name, u in AC_UNITS.items():
            if not room or room in ("all", "caly dom", "wszystkie") or room in ac_name or ac_name in room:
                targets.append((ac_name, u))

        if not targets and AC_UNITS:
            targets = list(AC_UNITS.items())

        cmd = "on"
        if action == "off": cmd = "off"
        elif action == "cool": cmd = "mode/2" # Loxone AC mode cooling
        elif action == "heat": cmd = "mode/1" # Loxone AC mode heating
        elif action == "auto": cmd = "mode/0"
        elif action == "set_temp" and target_temp is not None:
            cmd = f"setTargetTemp/{target_temp}"

        results = []
        for ac_name, u in targets:
            res = await direct_miniserver_cmd(u, cmd)
            if target_temp is not None and action != "set_temp":
                await direct_miniserver_cmd(u, f"setTargetTemp/{target_temp}")
            results.append({"unit": ac_name, "command": cmd, "result": res})
        return {"success": True, "action": action, "results": results}

    # 4. Robot Vacuums & Cleaning control
    if name == "control_cleaning":
        target = normalize_text(arguments.get("target", "all"))
        action = arguments.get("action", "start")
        cmd = "pulse" if action in ("start", "pulse") else "off"

        targets = []
        for c_name, u in CLEANING_COMMANDS.items():
            if target in c_name or c_name in target or target == "all":
                targets.append((c_name, u))

        results = []
        for c_name, u in targets:
            res = await direct_miniserver_cmd(u, cmd)
            results.append({"cleaning_target": c_name, "command": cmd, "result": res})
        return {"success": True, "results": results}

    # 5. Garden & Balcony Irrigation control
    if name == "control_irrigation":
        target = normalize_text(arguments.get("target", "balkon"))
        action = arguments.get("action", "pulse")
        cmd = "pulse" if action in ("start", "pulse") else action

        targets = []
        for i_name, u in IRRIGATION_CONTROLS.items():
            if target in i_name or i_name in target or target == "all":
                targets.append((i_name, u))

        results = []
        for i_name, u in targets:
            res = await direct_miniserver_cmd(u, cmd)
            results.append({"target": i_name, "command": cmd, "result": res})
        return {"success": True, "results": results}

    # 6. Roof & Façade Windows control
    if name == "control_windows":
        action = arguments.get("action", "open")
        target_uuid = WINDOWS_CONTROLS.get(action)
        if not target_uuid:
            target_uuid = WINDOWS_CONTROLS.get("open" if action == "open" else "close")
        if target_uuid:
            res = await direct_miniserver_cmd(target_uuid, "pulse")
            return {"success": True, "action": action, "result": res}
        return {"success": False, "error": f"No window control configured for action '{action}'"}

    # 7. Intercom, Gate & Wicket control
    if name == "control_intercom":
        action = arguments.get("action", "open_wicket")
        intercom_uuid = INTERCOM_CONTROLS.get("domofon")
        if not intercom_uuid and INTERCOM_CONTROLS:
            intercom_uuid = list(INTERCOM_CONTROLS.values())[0]

        if intercom_uuid:
            # Loxone Intercom: output 0 is wicket, output 1 is gate
            output_num = 1 if "gate" in action or "bram" in action else 0
            res = await direct_miniserver_cmd(intercom_uuid, f"pulse/{output_num}")
            return {"success": True, "action": action, "output": output_num, "result": res}
        return {"success": False, "error": "Intercom not configured in moods.json"}

    # 8. Switches and System Modes control
    if name == "control_switch":
        switch_name = normalize_text(arguments.get("switch_name", ""))
        state = arguments.get("state", "toggle")

        matched_uuid = None
        for s_key, u in SWITCHES.items():
            if s_key in switch_name or switch_name in s_key:
                matched_uuid = u
                break

        if matched_uuid:
            cmd = "pulse" if state == "toggle" else state
            res = await direct_miniserver_cmd(matched_uuid, cmd)
            return {"success": True, "switch": switch_name, "command": cmd, "result": res}
        return {"success": False, "error": f"Switch '{switch_name}' not found"}

    # 9. Scene / Mood activation handling (Gen 2 LightControllerV2 ID mapping)
    if name == "activate_scene":
        scene_raw = arguments.get("scene", "")
        room_raw = arguments.get("room", "")
        norm_scene = normalize_text(scene_raw)
        norm_room = normalize_text(room_raw)

        # Check if room is in LIGHT_MOODS
        matched_room_key = None
        for r_key in LIGHT_MOODS:
            if r_key in norm_room or norm_room in r_key:
                matched_room_key = r_key
                break

        if matched_room_key:
            controller = LIGHT_MOODS[matched_room_key]
            ctrl_uuid = controller.get("uuid")
            moods = controller.get("moods", {})

            # Find matching mood
            mood_id = None
            for m_name, m_id in moods.items():
                if m_name in norm_scene or norm_scene in m_name:
                    mood_id = m_id
                    break

            if mood_id is not None and ctrl_uuid:
                logger.info(f"Resolved scene '{scene_raw}' in room '{matched_room_key}' to mood ID {mood_id}")
                res = await direct_miniserver_cmd(ctrl_uuid, f"changeTo/{mood_id}")
                return {
                    "success": True,
                    "room": matched_room_key,
                    "scene": scene_raw,
                    "mood_id": mood_id,
                    "controller_uuid": ctrl_uuid,
                    "miniserver_response": res
                }

    # 10. Standard MCP tool execution
    result = await call_mcp_raw(name, arguments)
    is_err = isinstance(result, dict) and ("error" in result or result.get("isError"))
    if not is_err:
        return result
        
    logger.warning(f"MCP tool {name} returned error: {result}. Attempting direct Miniserver execution.")
    target = arguments.get("target") or arguments.get("uuid") or arguments.get("device")
    action = arguments.get("action")
    if action is None and "position" in arguments:
        action = str(arguments["position"])
    elif action is None and "brightness" in arguments:
        action = str(arguments["brightness"])
    elif action is None and "temperature" in arguments:
        action = str(arguments["temperature"])

    if target and action:
        res = await direct_miniserver_cmd(target, action)
        if res.get("success"):
            return {"success": True, "executed_command": action, "target": target}

    return result

@app.get("/v1/tools")
async def list_available_tools(lang: str = "pl"):
    """List all available tools formatted for LLM engines or Home Assistant."""
    return await fetch_mcp_tools(lang=lang)

@app.post("/v1/tools/execute")
async def execute_tool_endpoint(req: Request):
    """Direct execution endpoint for Home Assistant LLM / REST actions."""
    data = await req.json()
    name = data.get("name") or data.get("tool") or data.get("function")
    arguments = data.get("arguments") or data.get("params") or data.get("args") or {}
    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name' of tool to execute")
    logger.info(f"Direct HA tool execution: {name} with args {arguments}")
    result = await execute_mcp_tool(name, arguments)
    return {"success": True, "tool": name, "result": result}

@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible models listing."""
    return {
        "object": "list",
        "data": [
            {"id": "gemini-3.6-flash", "object": "model", "owned_by": "google"},
            {"id": "gemini-flash-latest", "object": "model", "owned_by": "google"},
            {"id": "gemini-2.5-flash", "object": "model", "owned_by": "google"},
            {"id": "loxone-ai", "object": "model", "owned_by": "loxone"}
        ]
    }

@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    """OpenAI-compatible chat completions endpoint with automatic tool calling loop."""
    body = await req.json()
    model = body.get("model", GEMINI_MODEL)
    if model in ("loxone-ai", "default", "gemini-2.5-flash", None, ""):
        model = "gemini-3.6-flash"
        
    # Check language override in request or fallback to server default
    lang = body.get("language", DEFAULT_LANGUAGE)
    if lang not in SYSTEM_INSTRUCTIONS:
        lang = "en"

    sys_instruction = SYSTEM_INSTRUCTIONS.get(lang, SYSTEM_INSTRUCTIONS["en"])

    messages = body.get("messages", [])
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": sys_instruction})
    else:
        messages[0]["content"] = sys_instruction + "\n" + messages[0]["content"]

    client = get_client()
    tools = await fetch_mcp_tools(lang=lang)
    
    current_messages = list(messages)
    max_turns = 6
    turn = 0
    final_content = ""

    while turn < max_turns:
        turn += 1
        logger.info(f"Calling Gemini (model={model}, turn={turn})")
        response = await client.chat.completions.create(
            model=model,
            messages=current_messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None
        )
        
        choice = response.choices[0]
        message = choice.message
        
        if message.tool_calls:
            logger.info(f"Gemini requested {len(message.tool_calls)} tool call(s)")
            current_messages.append(message)
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except Exception:
                    fn_args = {}
                logger.info(f"Executing tool {fn_name} with args {fn_args}")
                tool_result = await execute_mcp_tool(fn_name, fn_args)
                logger.info(f"Tool {fn_name} result: {tool_result}")
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })
        else:
            final_content = message.content or ""
            break

    if not final_content:
        logger.info("Generating final summary response text")
        summary_resp = await client.chat.completions.create(
            model=model,
            messages=current_messages
        )
        default_msg = DEFAULT_FALLBACK_TEXT.get(lang, DEFAULT_FALLBACK_TEXT["en"])
        final_content = summary_resp.choices[0].message.content or default_msg

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": final_content
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
