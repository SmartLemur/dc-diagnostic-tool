import os
import json
import re
import requests

INTENT_TYPES = [
    "get_switch_config",
    "get_full_switch_config",
    "configure_port_vlan",
    "configure_port_lacp",
    "get_server_health",
    "server_power_action",
    "run_diagnostic",
    "capability_query",
    "os_deploy",
    "general_question",
    "troubleshoot",
    "out_of_scope",
]

FALLBACK = {
    "intent": "general_question",
    "device_type": None,
    "device_name": None,
    "parameters": {},
    "confidence": "low",
    "clarification_needed": False,
    "clarification_question": None,
}

SYSTEM_PROMPT_TEMPLATE = """\
You are an intent classifier for a data centre management platform.

YOUR RESPONSE FORMAT: The VERY FIRST CHARACTER you output must be {{. Do not write any thinking, reasoning, explanation, or analysis before the JSON. Jump directly to the JSON object. No text before {{, no text after }}.

Classify the engineer's message and return ONLY valid JSON.

Possible intent types:
- get_switch_config         : view config or status of a specific port
- get_full_switch_config    : view full switch running configuration
- configure_port_vlan       : change or assign a VLAN on a port
- configure_port_lacp       : configure LACP/port-channel/bonding on a port
- get_server_health         : check server hardware health, sensors, power state
- server_power_action       : power on/off/reboot/reset a server
- run_diagnostic            : run full network/connectivity diagnostic for a new server deployment
- capability_query          : asking what the system can do
- os_deploy                 : install or deploy an OS to a server
- general_question          : general data-centre or networking question
- troubleshoot              : open-ended problem investigation
- out_of_scope              : completely unrelated to data centre management

Registered devices in this environment:
{devices_json}

Return exactly this JSON structure:
{{
  "intent": "<one of the intent types above>",
  "device_type": "<switch|bmc|null>",
  "device_name": "<exact device name from the registered devices list, or null>",
  "parameters": {{}},
  "confidence": "<high|medium|low>",
  "clarification_needed": <true|false>,
  "clarification_question": "<question string, or null>"
}}

Rules:
- device_name must exactly match a name from the registered devices list, or be null
- Use parameters to capture port numbers, VLAN IDs, power actions, IP addresses, etc.
- If the message clearly relates to network or server management even without specifying a device name, classify it with high confidence and leave device_name as null. The router will handle device resolution.
- Only set clarification_needed to true if the message is genuinely ambiguous and cannot be classified at all. Do not ask for clarification just because a device name is missing.
- Set confidence=low only when the intent could reasonably be two completely different types
- Never nest additional keys inside the JSON — return only the exact structure above

EXAMPLES (always classify these correctly regardless of phrasing):
- "show full config" / "show switch config" / "display running config" / "full config" → get_full_switch_config, high confidence
- "show server health" / "server status" / "how is the server" / "is server ok" → get_server_health, high confidence
- "set port X to vlan Y" / "change port X vlan Y" / "configure port X vlan Y" → configure_port_vlan, high confidence
- "restart server" / "reboot server" / "power off server" → server_power_action, high confidence
- "show port config" / "port details" / "what is on port X" → get_switch_config, high confidence
- "is it possible to configure X" / "can the switch do X" / "does this support X" → capability_query, high confidence
- "something is wrong" / "not working" / "unreachable" / "cannot connect" → troubleshoot, high confidence
"""


def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
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


class IntentClassifier:
    def classify(self, message: str, devices: list) -> dict:
        api_key, base_url, model = _load_env()
        if not api_key:
            return {**FALLBACK, "clarification_question": "API key not configured."}

        device_summary = [
            {"name": d.get("name"), "type": d.get("type"), "ip": d.get("ip"), "brand": d.get("brand", "")}
            for d in devices
        ]
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            devices_json=json.dumps(device_summary, indent=2)
        )

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": message},
                    ],
                    "max_tokens": 1500,
                    "temperature": 0.0,
                },
                timeout=30,
            )

            if response.status_code != 200:
                return {**FALLBACK}

            raw = response.json()["choices"][0]["message"]["content"].strip()
            # Provider runs model in chain-of-thought mode — extract the JSON
            # object from the end of the reasoning text if present
            match = re.search(r'\{[^{}]*"intent"[^{}]*\}', raw, re.DOTALL)
            json_str = match.group(0) if match else raw
            result = json.loads(json_str)

            required_keys = {"intent", "device_type", "device_name", "parameters",
                             "confidence", "clarification_needed", "clarification_question"}
            if not required_keys.issubset(result.keys()):
                return {**FALLBACK}
            if result["intent"] not in INTENT_TYPES:
                result["intent"] = "general_question"

            return result

        except (json.JSONDecodeError, KeyError, ValueError):
            return {**FALLBACK}
        except requests.Timeout:
            return {**FALLBACK}
        except Exception:
            return {**FALLBACK}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from database import get_devices

    classifier = IntentClassifier()
    devices = get_devices()

    test_messages = [
        "show me the switch config",
        "set port WGE1/0/5 to vlan 99",
        "is the server healthy",
    ]

    for msg in test_messages:
        print(f"\nMessage : {msg!r}")
        result = classifier.classify(msg, devices)
        print(json.dumps(result, indent=2))
