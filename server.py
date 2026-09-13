#!/usr/bin/env python3
"""
Loxone LLM Bridge (OpenAI-compatible Agent for Home Assistant & Siri)
Enables voice control of Loxone Miniservers using LLMs (Google Gemini / OpenAI)
and MCP (Model Context Protocol). Supports English (default) and Polish.
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
        "You are an intelligent smart home assistant for a Loxone smart home named Home. "
        "You respond to the user and control the home using available Loxone tools. "
        "Rules: "
        "1. Always respond in English. "
        "2. Responses will be read aloud by a phone/watch text-to-speech engine, so keep them natural, brief, and concise. "
        "3. Do not use markdown formatting (no asterisks, bolding, tables, or numbered lists). "
        "4. When the user requests an action, ALWAYS call the appropriate tool first: "
        "   - turn on/off or dim lights: control_lights "
        "   - activate lighting mood or scene (e.g. 'evening in living room', 'night', 'dining', 'xbox', 'bright', 'relax'): activate_scene(scene=..., room=...) "
        "   - control blinds/shading/shutters: control_blinds "
        "   - control ventilation/airing/recuperation: control_ventilation(action=..., room=..., duration_minutes=..., speed=...) "
        "   - set target temperature: set_temperature "
        "5. When the user asks for airing or changing ventilation for a specific duration (e.g. 'for 20 minutes', 'for half an hour', 'for an hour', 'for 2 hours'), ALWAYS specify that in duration_minutes. "
        "6. Sensor, energy, window, and door information is available in get_sensor_readings. "
        "7. Always conclude with a single short spoken confirmation sentence (e.g. 'Turned on kitchen ventilation for 30 minutes.', 'Activated evening mood in the living room.')."
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
        "   - sterowanie roletami/zaluzjami: control_blinds "
        "   - sterowanie wentylacja/rekuperacja/wietrzeniem: control_ventilation(action=..., room=..., duration_minutes=..., speed=...) "
        "   - ustawienie temperatury: set_temperature "
        "5. Gdy uzytkownik prosi o wietrzenie lub zmiane wentylacji na okreslony czas (np. 'na 20 minut', 'na pol godziny', 'na godzine', 'na 2 godziny'), ZAWSZE podaj ten czas w duration_minutes. "
        "6. Informacje o stanie okien, drzwi, energii i czujnikach sa dostepne w get_sensor_readings. "
        "7. Zawsze na koniec odpowiedz krotkim zdaniem potwierdzajacym wykonanie akcji (np. 'Wlaczylem wietrzenie w kuchni na 30 minut.', 'Wlaczylem tryb wieczor w salonie.')."
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

# Optional external mood & ventilation mapping config (can be loaded from moods.json)
CONFIG_MOODS_FILE = os.getenv("MOODS_CONFIG_FILE", "moods.json")
LIGHT_MOODS: Dict[str, Any] = {}
VENTILATION_CONTROLS: Dict[str, str] = {}

if os.path.exists(CONFIG_MOODS_FILE):
    try:
        with open(CONFIG_MOODS_FILE) as f:
            cfg = json.load(f)
            LIGHT_MOODS = cfg.get("light_moods", {})
            VENTILATION_CONTROLS = cfg.get("ventilation_controls", {})
            logger.info(f"Loaded custom configuration from {CONFIG_MOODS_FILE}")
    except Exception as e:
        logger.warning(f"Failed to load {CONFIG_MOODS_FILE}: {e}")

def get_ventilation_tool_def(lang: str = "en") -> Dict[str, Any]:
    if lang == "pl":
        return {
            "type": "function",
            "function": {
                "name": "control_ventilation",
                "description": "Sterowanie wentylacją i rekuperacją w pomieszczeniach lub w całym domu z możliwością określenia czasu trwania. Obsługuje intensywne wietrzenie (boost/airing), spoczynek/wyłączenie (off/resting), powrót do trybu automatycznego (auto) oraz ustawienie konkretnej prędkości w procentach (set_speed).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "room": {
                            "type": "string",
                            "description": "Nazwa pomieszczenia (np. 'Gabinet', 'Kuchnia', 'Sypialnia') lub 'all' / brak dla wszystkich pomieszczeń"
                        },
                        "action": {
                            "type": "string",
                            "enum": ["boost", "airing", "off", "resting", "auto", "set_speed"],
                            "description": "Akcja wentylacji: boost/airing (wietrzenie na 100%), off/resting (wyłączenie), auto (tryb automatyczny), set_speed (prędkość w %)"
                        },
                        "duration_minutes": {
                            "type": "integer",
                            "description": "Czas trwania akcji w minutach (np. 15, 20, 30, 45, 60 dla 1h, 120 dla 2h). Domyślnie 15 minut dla wietrzenia, 60 minut dla set_speed, 120 minut dla off."
                        },
                        "speed": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 100,
                            "description": "Prędkość wentylatora w procentach (0-100) dla akcji set_speed"
                        }
                    },
                    "required": ["action"]
                }
            }
        }
    return {
        "type": "function",
        "function": {
            "name": "control_ventilation",
            "description": "Control ventilation and recuperation units by room or whole house with optional duration timer. Supports boost/airing, off/resting, auto/reset mode, and set_speed in percent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": "Room name (e.g. 'Office', 'Kitchen', 'Bedroom') or 'all' / omitted for all units"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["boost", "airing", "off", "resting", "auto", "set_speed"],
                        "description": "Ventilation action: boost/airing (100% boost), off/resting (turn off), auto (automatic mode), set_speed (custom percentage)"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Duration in minutes (e.g. 15, 20, 30, 45, 60 for 1h, 120 for 2h). Default is 15 min for airing, 60 min for speed, 120 min for off."
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
    }

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
        
        openai_tools = []
        for t in raw_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {"type": "object", "properties": {}})
                }
            })
        # Add custom ventilation tool
        openai_tools.append(get_ventilation_tool_def(lang))
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
    """Execute tool call with smart room/mood/ventilation handling and fallback."""
    # 1. Custom handling for ventilation with duration support
    if name == "control_ventilation":
        action = arguments.get("action", "auto")
        room = normalize_text(arguments.get("room", "") or "")
        speed = arguments.get("speed", 100)
        
        # Calculate duration in seconds
        dur_min = arguments.get("duration_minutes")
        if dur_min is not None:
            try:
                dur_sec = int(dur_min) * 60
            except Exception:
                dur_sec = None
        elif "duration" in arguments and arguments["duration"] is not None:
            try:
                dur_sec = int(arguments["duration"])
            except Exception:
                dur_sec = None
        else:
            dur_sec = None

        targets = []
        if room in VENTILATION_CONTROLS:
            targets = [(room, VENTILATION_CONTROLS[room])]
        elif VENTILATION_CONTROLS:
            targets = list(VENTILATION_CONTROLS.items())

        if action in ("boost", "airing"):
            dur = dur_sec if dur_sec else 900
            cmd = f"setTimer/{dur}/100/2/-1"
        elif action in ("off", "resting"):
            dur = dur_sec if dur_sec else 7200
            cmd = f"setTimer/{dur}/0/2/-1"
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

    # 2. Scene / Mood activation handling (Gen 2 LightControllerV2 ID mapping)
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

    # 3. Standard MCP tool execution
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
