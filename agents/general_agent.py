import os
import sys
import sqlite3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import get_devices, DB_PATH

# ── UI guidance map ───────────────────────────────────────────────────────────
# Each entry: (keywords_any_match, response)
_UI_GUIDANCE = [
    (
        ["what can you do", "capabilities", "help me", "how can you help"],
        """\
I can help you with:
• Server health — check BMC status, power state, hardware health
• Switch management — show port config, change VLANs, show full config
• Network diagnostics — check any IP for connectivity, conflicts, OS detection
• Power control — restart or shutdown servers (with confirmation)
• Event log — show recent system events
• Topology — explain network diagram
• General questions — networking concepts, protocols, hardware

Commands you can type:
• set port [PORT] to vlan [VLAN]
• show full config
• check [IP]
• restart [server name]
• show server health""",
    ),
    (
        ["add server", "add bmc", "add device"],
        "Go to Settings page → click Add Device → select type BMC → fill in IP, username, password, brand.",
    ),
    (
        ["add switch"],
        "Go to Settings page → click Add Device → select type Switch → fill in IP, credentials, brand.",
    ),
    (
        ["change vlan", "configure port", "set vlan", "port vlan"],
        "Go to Switches page → click any port → use the Change VLAN field in the drawer. "
        "Or type: set port [PORT] to vlan [NUMBER] in chat.",
    ),
    (
        ["show config", "switch config", "full config", "running config"],
        "Go to Switches page → click Full Switch Config button, or type: show full config in chat.",
    ),
    (
        ["server health", "server status", "bmc health", "bmc status"],
        "Go to Servers page to see live BMC health. Or ask me: show server health.",
    ),
    (
        ["topology", "network diagram", "network map"],
        "Go to Topology page to see your network diagram. Devices are auto-discovered via ARP scan.",
    ),
    (
        ["event log", "audit log", "audit trail", "system events"],
        "Go to Event Log page to see all system events and actions.",
    ),
    (
        ["diagnostic", "check ip", "run diagnostic", "connectivity check"],
        "Type: check [IP ADDRESS] in chat to run a full network diagnostic.",
    ),
]

_DC_SYSTEM_PROMPT = (
    "You are a senior data centre engineer with 15 years of experience. "
    "Answer questions about networking, servers, storage, and infrastructure. "
    "Be concise and practical. Maximum 4 sentences unless a list is needed. "
    "If asked about commands, always specify which vendor/brand the command is for."
)

_OUT_OF_SCOPE_NOTE = (
    "\n\nNote: I'm specialized for data centre management. "
    "For other topics, I may not be the best resource."
)

_OUT_OF_SCOPE_KEYWORDS = [
    "recipe", "weather", "sport", "movie", "music", "cook", "travel",
    "celebrity", "fashion", "stock market", "crypto", "relationship",
    "poem", "write a story", "creative writing",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    api_key = ""
    base_url = "https://api.deepseek.com/v1"
    model = "deepseek-chat"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY"):
                    api_key = line.split("=", 1)[-1].strip().strip('"').strip("'")
                elif line.startswith("DEEPSEEK_BASE_URL"):
                    base_url = line.split("=", 1)[-1].strip().strip('"').strip("'")
                elif line.startswith("DEEPSEEK_MODEL"):
                    model = line.split("=", 1)[-1].strip().strip('"').strip("'")
    return api_key, base_url, model


def _normalize(text: str) -> str:
    """Lowercase and strip common filler words so 'add a server' matches 'add server'."""
    drop = {"a", "an", "the", "my", "your", "our", "me", "i", "do", "how",
            "can", "could", "please", "to", "is", "are", "what", "where",
            "show", "get", "see", "find", "check", "tell", "about", "for"}
    tokens = [t for t in text.lower().split() if t not in drop]
    return " ".join(tokens)


def _detect_ui_guidance(message: str) -> str | None:
    msg_raw = message.lower()
    msg_norm = _normalize(message)
    for keywords, response in _UI_GUIDANCE:
        for kw in keywords:
            # Match on raw substring OR normalized substring
            if kw in msg_raw or kw in msg_norm:
                return response
    return None


def _load_memories(limit: int = 5) -> tuple[list[dict], int]:
    """Return (recent_memories, total_count). Returns ([], 0) if table missing."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT category, device_name, summary, timestamp FROM system_memory "
            "ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = c.fetchall()
        c.execute("SELECT COUNT(*) FROM system_memory")
        total = c.fetchone()[0]
        conn.close()
        memories = [
            {"category": r[0], "device_name": r[1], "summary": r[2], "timestamp": r[3]}
            for r in rows
        ]
        return memories, total
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return [], 0
    except Exception:
        return [], 0


def _build_llm_context(original_message: str, devices: list, memories: list) -> str:
    parts = [f"Engineer question: {original_message}\n"]

    if devices:
        dev_lines = [
            f"  - {d['name']} ({d['type']}, {d['brand']}, {d['ip']})"
            for d in devices
        ]
        parts.append("Registered devices in this environment:\n" + "\n".join(dev_lines))

    if memories:
        mem_lines = [
            f"  [{m['timestamp']}] {m['category']} / {m['device_name']}: {m['summary']}"
            for m in memories
        ]
        parts.append("Recent infrastructure memory:\n" + "\n".join(mem_lines))

    return "\n\n".join(parts)


def _call_llm(user_content: str) -> str:
    api_key, base_url, model = _load_env()
    if not api_key:
        return "I'm having trouble connecting to the AI. For UI help, try the Settings page. Type 'help' to see what I can do."
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _DC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 500,
                "temperature": 0.1,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        return "I'm having trouble connecting to the AI. For UI help, try the Settings page. Type 'help' to see what I can do."
    except Exception:
        return "I'm having trouble connecting to the AI. For UI help, try the Settings page. Type 'help' to see what I can do."


def _looks_out_of_scope(message: str) -> bool:
    msg = message.lower()
    return any(kw in msg for kw in _OUT_OF_SCOPE_KEYWORDS)


# ── GeneralAgent ──────────────────────────────────────────────────────────────

class GeneralAgent:
    def handle(self, intent: dict, session: dict, original_message: str) -> dict:
        msg = original_message.strip()

        # ── UI guidance — no LLM needed ───────────────────────────────────
        guidance = _detect_ui_guidance(msg)
        if guidance:
            return {"reply": guidance, "step": "idle"}

        # ── Memory + session first-message banner ─────────────────────────
        memories, memory_count = _load_memories()
        prefix = ""

        if not session.get("memory_shown"):
            session["memory_shown"] = True
            if memory_count > 0:
                prefix = f"I have {memory_count} memories about your infrastructure from previous sessions.\n\n"
            else:
                prefix = "Fresh start — I have no memory of this infrastructure yet. I'll learn as we work together.\n\n"

        # ── LLM for general DC knowledge ──────────────────────────────────
        devices = get_devices()
        user_content = _build_llm_context(msg, devices, memories)
        reply = _call_llm(user_content)

        # ── Out-of-scope note ─────────────────────────────────────────────
        if _looks_out_of_scope(msg):
            reply += _OUT_OF_SCOPE_NOTE

        return {"reply": prefix + reply, "step": "idle"}


if __name__ == "__main__":
    agent = GeneralAgent()
    session = {}

    print("=== Test 1: UI guidance — 'how do I add a server' ===")
    result = agent.handle(
        intent={"intent": "general_question"},
        session=session,
        original_message="how do I add a server",
    )
    print(result["reply"])

    print("\n=== Test 2: Capability list — 'what can you do' ===")
    result = agent.handle(
        intent={"intent": "capability_query"},
        session=session,
        original_message="what can you do",
    )
    print(result["reply"])

    print("\n=== Test 3: UI guidance — 'event log' ===")
    result = agent.handle(
        intent={"intent": "general_question"},
        session=session,
        original_message="where is the event log",
    )
    print(result["reply"])
