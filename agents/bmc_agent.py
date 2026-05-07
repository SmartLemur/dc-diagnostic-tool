import os
import sys
import time
import requests
import urllib3
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import get_devices

SUPPORTED_OPERATIONS = [
    "get_server_health   — fetch power state, temperatures, fans, PSUs",
    "server_power_action — On / GracefulShutdown / GracefulRestart / ForceOff",
    "capability_query    — answer questions about what this agent can do",
]

ACTIONS_REQUIRING_CONFIRM = {"GracefulShutdown", "ForceOff", "GracefulRestart"}
ALLOWED_ACTIONS = {"On", "GracefulShutdown", "GracefulRestart", "ForceOff", "ForceRestart"}


# ── Env / config ─────────────────────────────────────────────────────────────

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


# ── DB lookup ─────────────────────────────────────────────────────────────────

def _find_device(device_name: str):
    devices = get_devices(device_type="bmc")
    if device_name:
        for d in devices:
            if d["name"].lower() == device_name.lower():
                return d
    return devices[0] if devices else None


# ── Redfish helpers ───────────────────────────────────────────────────────────

def _make_session(username: str, password: str) -> requests.Session:
    s = requests.Session()
    s.auth = HTTPBasicAuth(username, password)
    s.verify = False
    return s


def _collection_path(session: requests.Session, ip: str, collection: str, tracker: dict) -> str | None:
    """GET /redfish/v1/{collection}/ → first Member @odata.id. Returns None on failure."""
    url = f"https://{ip}/redfish/v1/{collection}/"
    tracker["requests_sent"].append({"method": "GET", "url": url, "payload": None})
    try:
        r = session.get(url, timeout=10)
        tracker["responses"].append({"url": url, "status_code": r.status_code, "body": r.json() if r.content else {}})
        if r.status_code == 200:
            members = r.json().get("Members", [])
            if members:
                return members[0].get("@odata.id")
        return None
    except Exception:
        tracker["responses"].append({"url": url, "status_code": None, "body": {}})
        return None


def _get(session: requests.Session, ip: str, path: str, tracker: dict) -> dict | None:
    url = f"https://{ip}{path}"
    tracker["requests_sent"].append({"method": "GET", "url": url, "payload": None})
    try:
        r = session.get(url, timeout=10)
        body = r.json() if r.content else {}
        tracker["responses"].append({"url": url, "status_code": r.status_code, "body": body})
        if r.status_code == 401:
            raise PermissionError("401")
        if r.status_code == 404:
            raise LookupError("404")
        if r.status_code == 200:
            return body
        return None
    except (PermissionError, LookupError):
        raise
    except requests.exceptions.Timeout:
        raise TimeoutError()
    except requests.exceptions.ConnectionError:
        raise ConnectionError()


def _post(session: requests.Session, ip: str, path: str, payload: dict, tracker: dict) -> tuple[int, dict]:
    url = f"https://{ip}{path}"
    tracker["requests_sent"].append({"method": "POST", "url": url, "payload": payload})
    r = session.post(url, json=payload, timeout=10)
    body = r.json() if r.content else {}
    tracker["responses"].append({"url": url, "status_code": r.status_code, "body": body})
    return r.status_code, body


# ── LLM helper ────────────────────────────────────────────────────────────────

def _ask_llm(prompt: str) -> str:
    api_key, base_url, model = _load_env()
    if not api_key:
        return "AI unavailable — API key not configured."
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.1,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        return f"AI error {resp.status_code}."
    except Exception:
        return "AI is currently unavailable. Please try again shortly."


# ── BMCAgent ──────────────────────────────────────────────────────────────────

class BMCAgent:
    def handle(self, intent: dict, session: dict) -> dict:
        intent_type = intent.get("intent", "get_server_health")
        device_name = intent.get("device_name")
        params = intent.get("parameters", {})

        # ── Auto-resolve device if not specified ─────────────────────────
        if not device_name:
            all_bmcs = get_devices(device_type="bmc")
            if not all_bmcs:
                return {"reply": "No servers registered. Add a server in Settings first.", "step": "idle"}
            if len(all_bmcs) == 1:
                device_name = all_bmcs[0]["name"]
                intent["device_name"] = device_name
            else:
                names = ", ".join(d["name"] for d in all_bmcs)
                return {"reply": f"Multiple servers found: {names}. Which one? Please specify.", "step": "idle"}

        device = _find_device(device_name)
        if not device:
            return {"reply": "No BMC devices found in database. Add a server via Settings.", "step": "idle"}

        ip = device["ip"]
        username = device["username"]
        password = device["password"]
        name = device["name"]
        brand = device.get("brand", "Unknown")
        model_name = device.get("model", "Unknown")

        if intent_type == "capability_query":
            return self._capability_query(name, brand, model_name, params)

        if intent_type == "server_power_action":
            result = self._power_action(ip, username, password, name, params)
            if result.get("step") == "idle":
                try:
                    from memory import record_action
                    action = params.get("action") or params.get("reset_type") or intent_type
                    record_action(
                        category="server_action",
                        summary=f"{action} on {name}",
                        device_name=name,
                        triggered_by=session.get("user", "unknown"),
                    )
                except Exception:
                    pass
            return result

        result = self._get_health(ip, username, password, name)
        if result.get("step") == "idle" and "failed" not in result.get("reply", "").lower() and "error" not in result.get("reply", "").lower():
            try:
                from memory import record_action
                record_action(
                    category="server_health",
                    summary=f"get_server_health on {name}",
                    device_name=name,
                    triggered_by=session.get("user", "unknown"),
                )
            except Exception:
                pass
        return result

    # ── get_server_health ─────────────────────────────────────────────────────

    def _get_health(self, ip: str, username: str, password: str, name: str) -> dict:
        tracker = {"requests_sent": [], "responses": [], "verification": {}}
        sess = _make_session(username, password)

        try:
            systems_path = _collection_path(sess, ip, "Systems", tracker)
            chassis_path = _collection_path(sess, ip, "Chassis", tracker)

            if not systems_path:
                return {"reply": f"Could not discover Redfish Systems path on {name} ({ip}). Check BMC connectivity.", "step": "idle"}

            systems_data = _get(sess, ip, systems_path, tracker) or {}
            thermal_data = {}
            power_data = {}

            if chassis_path:
                thermal_data = _get(sess, ip, f"{chassis_path}/Thermal", tracker) or {}
                power_data = _get(sess, ip, f"{chassis_path}/Power", tracker) or {}

            # ── Build summary ─────────────────────────────────────────────
            power_state = systems_data.get("PowerState", "Unknown")
            health = systems_data.get("Status", {}).get("Health", "Unknown")
            state = systems_data.get("Status", {}).get("State", "Unknown")
            model_str = systems_data.get("Model", "Unknown")
            ram_gib = systems_data.get("MemorySummary", {}).get("TotalSystemMemoryGiB", "Unknown")
            cpu_model = systems_data.get("ProcessorSummary", {}).get("Model", "Unknown")
            cpu_count = systems_data.get("ProcessorSummary", {}).get("Count", "Unknown")

            temps = thermal_data.get("Temperatures", [])
            fans = thermal_data.get("Fans", [])
            psus = power_data.get("PowerSupplies", [])

            temp_lines = [
                f"  {t.get('Name', '?')}: {t.get('ReadingCelsius', '?')}°C "
                f"(warn {t.get('UpperThresholdNonCritical', '?')}°C / "
                f"crit {t.get('UpperThresholdCritical', '?')}°C)"
                for t in temps if t.get("ReadingCelsius") is not None
            ]
            fan_lines = [
                f"  {f.get('Name', '?')}: {f.get('Reading', '?')} {f.get('ReadingUnits', 'RPM')} "
                f"— {f.get('Status', {}).get('Health', 'Unknown')}"
                for f in fans
            ]
            psu_lines = [
                f"  {p.get('Name', '?')}: {p.get('PowerInputWatts', '?')}W in / "
                f"{p.get('PowerOutputWatts', '?')}W out — {p.get('Status', {}).get('Health', 'Unknown')}"
                for p in psus
            ]

            summary_parts = [
                f"**{name}** — {model_str}",
                f"Power: {power_state} | Health: {health} | State: {state}",
                f"RAM: {ram_gib} GiB | CPU: {cpu_model} x{cpu_count}",
            ]
            if temp_lines:
                summary_parts.append("Temperatures:\n" + "\n".join(temp_lines))
            if fan_lines:
                summary_parts.append("Fans:\n" + "\n".join(fan_lines))
            if psu_lines:
                summary_parts.append("Power Supplies:\n" + "\n".join(psu_lines))

            return {
                "reply": "\n".join(summary_parts),
                "raw_output": tracker,
                "step": "idle",
            }

        except PermissionError:
            return {"reply": f"BMC authentication failed for {name} ({ip}). Check username and password.", "step": "idle"}
        except LookupError:
            return {"reply": f"This BMC does not support standard Redfish paths.", "step": "idle"}
        except TimeoutError:
            return {"reply": f"BMC connection timed out ({ip}).", "step": "idle"}
        except ConnectionError:
            return {"reply": f"Cannot connect to BMC at {ip}. Check network and credentials.", "step": "idle"}
        except Exception as e:
            return {"reply": f"Unexpected error querying {name}: {e}", "step": "idle"}

    # ── server_power_action ───────────────────────────────────────────────────

    def _power_action(self, ip: str, username: str, password: str, name: str, params: dict) -> dict:
        action = (params.get("action") or params.get("reset_type") or "").strip()

        if not action:
            return {
                "reply": "Specify a power action: On, GracefulShutdown, GracefulRestart, or ForceOff.",
                "step": "idle",
            }

        if action not in ALLOWED_ACTIONS:
            return {
                "reply": f"'{action}' is not a valid power action. Allowed: {', '.join(sorted(ALLOWED_ACTIONS))}.",
                "step": "idle",
            }

        if action in ACTIONS_REQUIRING_CONFIRM:
            return {
                "reply": (
                    f"Confirm: send **{action}** to **{name}** ({ip})?\n"
                    "This will affect the running server. Reply yes/no."
                ),
                "step": "confirm",
                "pending_action": {
                    "type": "bmc_power",
                    "action": action,
                    "device_name": name,
                    "ip": ip,
                },
            }

        return self._execute_power_action(ip, username, password, name, action)

    def _execute_power_action(self, ip: str, username: str, password: str, name: str, action: str) -> dict:
        tracker = {"requests_sent": [], "responses": [], "verification": {"before": None, "after": None}}
        sess = _make_session(username, password)

        try:
            systems_path = _collection_path(sess, ip, "Systems", tracker)
            if not systems_path:
                return {"reply": f"Could not discover Redfish Systems path on {name} ({ip}).", "step": "idle"}

            # Before state
            before_data = _get(sess, ip, systems_path, tracker) or {}
            before_state = before_data.get("PowerState", "Unknown")
            tracker["verification"]["before"] = before_state

            # Execute
            status_code, body = _post(
                sess, ip,
                f"{systems_path}/Actions/ComputerSystem.Reset",
                {"ResetType": action},
                tracker,
            )

            if status_code == 401:
                return {"reply": f"BMC authentication failed for {name} ({ip}). Check username and password.", "step": "idle"}
            if status_code not in (200, 202, 204):
                return {"reply": f"BMC rejected {action} action (HTTP {status_code}).", "step": "idle"}

            # Wait then verify
            time.sleep(3)
            after_data = _get(sess, ip, systems_path, tracker) or {}
            after_state = after_data.get("PowerState", "Unknown")
            tracker["verification"]["after"] = after_state

            return {
                "reply": (
                    f"**{action}** sent to **{name}**.\n"
                    f"Power state: {before_state} → {after_state}"
                ),
                "raw_output": tracker,
                "step": "idle",
            }

        except PermissionError:
            return {"reply": f"BMC authentication failed for {name} ({ip}). Check username and password.", "step": "idle"}
        except LookupError:
            return {"reply": "This BMC does not support standard Redfish paths.", "step": "idle"}
        except TimeoutError:
            return {"reply": f"BMC connection timed out ({ip}).", "step": "idle"}
        except ConnectionError:
            return {"reply": f"Cannot connect to BMC at {ip}. Check network and credentials.", "step": "idle"}
        except Exception as e:
            return {"reply": f"Unexpected error during power action on {name}: {e}", "step": "idle"}

    # ── capability_query ──────────────────────────────────────────────────────

    def _capability_query(self, name: str, brand: str, model_name: str, params: dict) -> dict:
        original = params.get("original_message", "what can you do")
        prompt = (
            f"You are an AI assistant for a data centre management platform. "
            f"The engineer is asking about BMC/server capabilities.\n"
            f"Device: {name} | Brand: {brand} | Model: {model_name}\n"
            f"Question: {original}\n"
            f"Answer concisely based on standard Redfish API capabilities for this hardware."
        )
        reply = _ask_llm(prompt)
        return {"reply": reply, "step": "idle"}


if __name__ == "__main__":
    print("BMCAgent initialized — Redfish dynamic path discovery ready")
    print()
    print("Supported operations:")
    for op in SUPPORTED_OPERATIONS:
        print(f"  • {op}")
    print()
    print("Path discovery sequence (no hardcoded paths):")
    print("  GET /redfish/v1/Systems/  → Members[0]['@odata.id'] → systems_path")
    print("  GET /redfish/v1/Chassis/  → Members[0]['@odata.id'] → chassis_path")
    print("  GET /redfish/v1/Managers/ → Members[0]['@odata.id'] → managers_path")
    print()
    print("Actions requiring confirmation before execution:")
    for a in sorted(ACTIONS_REQUIRING_CONFIRM):
        print(f"  • {a}")
    print()
    print("Actions executed immediately:")
    immediate = ALLOWED_ACTIONS - ACTIONS_REQUIRING_CONFIRM
    for a in sorted(immediate):
        print(f"  • {a}")
