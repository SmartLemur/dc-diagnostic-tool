import os
import sys
import json
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hardware.switch_commands import get_command, get_supported_brands
from switch import (
    connect_switch,
    get_netmiko_device_type,
    get_full_interface_name,
)
from database import get_devices
from netmiko import NetmikoTimeoutException, NetmikoAuthenticationException


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


# Netmiko handles these automatically for all brands via send_config_set
_NETMIKO_AUTO_CMDS = frozenset({"system-view", "return", "end", "exit"})


def _extract_cli_commands(raw_text: str) -> list[str]:
    """Delimiter-based extraction. Falls back to last code block if delimiters absent."""
    def _filter(cmds):
        return [c for c in cmds if c.lower() not in _NETMIKO_AUTO_CMDS]

    # Primary: delimiter block
    if "---COMMANDS---" in raw_text:
        try:
            start = raw_text.index("---COMMANDS---") + len("---COMMANDS---")
            end = raw_text.index("---END---") if "---END---" in raw_text else len(raw_text)
            block = raw_text[start:end].strip()
            commands = _filter([l.strip() for l in block.splitlines() if l.strip() and not l.strip().startswith("```")])
            if commands:
                return commands
        except ValueError:
            pass

    # Fallback: last fenced code block
    if "```" in raw_text:
        try:
            blocks = raw_text.split("```")
            if len(blocks) >= 3:
                lines = blocks[-2].strip().splitlines()
                # drop language identifier line if present (e.g. "bash", "cli")
                if lines and lines[0].strip().replace("-", "").replace("_", "").isalpha():
                    lines = lines[1:]
                commands = _filter([l.strip() for l in lines if l.strip()])
                if commands:
                    return commands
        except Exception:
            pass

    return []


def _llm_generate_commands(brand: str, netmiko_type: str, task_description: str) -> list[str]:
    api_key, base_url, model = _load_env()
    if not api_key:
        raise RuntimeError("API key not configured")
    prompt = (
        "You are a network engineer. Generate CLI commands for the specified "
        "device and task.\n\n"
        "STRICT OUTPUT FORMAT — you MUST follow this exactly:\n"
        "1. Think silently (do not output reasoning)\n"
        "2. Output ONLY this structure:\n\n"
        "---COMMANDS---\n"
        "[one CLI command per line, nothing else]\n"
        "---END---\n\n"
        "No explanations. No reasoning. No markdown. No backticks. "
        "Only raw CLI commands between the delimiters.\n\n"
        f"Device brand: {brand}. "
        f"Netmiko device_type: {netmiko_type}. Task: {task_description}."
    )
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.0,
        },
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    commands = _extract_cli_commands(raw)
    if not commands:
        raise ValueError(
            "I could not generate commands for this request. "
            "Try rephrasing, or this brand may need to be added to the verified "
            "command map in hardware/switch_commands.py"
        )
    return commands


def _find_device(device_name: str, device_type: str = "switch"):
    devices = get_devices(device_type=device_type)
    if device_name:
        for d in devices:
            if d["name"].lower() == device_name.lower():
                return d
    # Fall back to first switch in DB if no name match
    return devices[0] if devices else None


def _execute_commands(device: dict, commands):
    """Connect and run commands. Returns raw output string."""
    ip = device["ip"]
    username = device["username"]
    password = device["password"]
    brand = device.get("brand", "unknown")

    conn = connect_switch(ip, username, password, brand)
    try:
        if isinstance(commands, list):
            output = conn.send_config_set(commands)
        else:
            output = conn.send_command(commands, read_timeout=30)
    finally:
        conn.disconnect()
    return output


BRAND_TO_NETMIKO = {
    "h3c": "hp_comware",
    "hp": "hp_comware",
    "hpe": "hp_comware",
    "cisco": "cisco_ios",
    "cisco ios": "cisco_ios",
    "cisco nxos": "cisco_nxos",
    "cisco nx-os": "cisco_nxos",
    "juniper": "juniper_junos",
    "junos": "juniper_junos",
    "arista": "arista_eos",
    "dell": "cisco_ios",
}


class SwitchAgent:
    def _validate_parameters(self, operation: str, parameters: dict, device_name) -> dict | None:
        if not device_name:
            return {"error": "No target device specified. Please name the switch."}

        # Strip whitespace from interface in-place
        for key in ("interface", "port"):
            if key in parameters and parameters[key]:
                parameters[key] = parameters[key].strip()

        interface = parameters.get("interface") or parameters.get("port") or ""

        if operation in ("set_vlan", "set_trunk"):
            if not interface:
                return {"error": "Invalid parameters: interface name is required for this operation."}

        if operation == "set_vlan":
            vlan_raw = parameters.get("vlan_id") or parameters.get("vlan")
            if vlan_raw is None:
                return {"error": "Invalid parameters: vlan_id is required."}
            try:
                vlan_id = int(vlan_raw)
            except (ValueError, TypeError):
                return {"error": f"Invalid parameters: '{vlan_raw}' is not a valid VLAN ID."}
            if not (1 <= vlan_id <= 4094):
                return {"error": f"Invalid parameters: VLAN ID {vlan_id} out of range (1–4094)."}

        if operation == "set_trunk":
            vlans_raw = parameters.get("vlans") or parameters.get("vlan_id") or parameters.get("vlan")
            if vlans_raw is None:
                return {"error": "Invalid parameters: vlans list is required for trunk configuration."}
            if isinstance(vlans_raw, str):
                parts = [v.strip() for v in vlans_raw.replace(" ", ",").split(",") if v.strip()]
            elif isinstance(vlans_raw, list):
                parts = [str(v) for v in vlans_raw]
            else:
                parts = [str(vlans_raw)]
            vlan_ints = []
            for part in parts:
                try:
                    v = int(part)
                except ValueError:
                    return {"error": f"Invalid parameters: '{part}' is not a valid VLAN ID."}
                if not (1 <= v <= 4094):
                    return {"error": f"Invalid parameters: VLAN ID {v} out of range (1–4094)."}
                vlan_ints.append(v)
            if not vlan_ints:
                return {"error": "Invalid parameters: no valid VLAN IDs provided for trunk."}
            parameters["vlans"] = vlan_ints

        return None

    def handle(self, intent: dict, session: dict) -> dict:
        intent_type = intent.get("intent", "general_question")
        device_name = intent.get("device_name")
        params = intent.get("parameters", {})

        # Normalise parameter key names from classifier
        if "vlan" in params and "vlan_id" not in params:
            params["vlan_id"] = params.pop("vlan")
        if "port" in params and "interface" not in params:
            params["interface"] = params.pop("port")
        if "vlans" not in params and "vlan_list" in params:
            params["vlans"] = params.pop("vlan_list")

        # ── Auto-resolve device if not specified ─────────────────────────
        if not device_name:
            all_switches = get_devices(device_type="switch")
            if not all_switches:
                return {"reply": "No switches registered. Add a switch in Settings first.", "step": "idle"}
            if len(all_switches) == 1:
                device_name = all_switches[0]["name"]
                intent["device_name"] = device_name
            else:
                names = ", ".join(d["name"] for d in all_switches)
                return {"reply": f"Multiple switches found: {names}. Which one? Please specify.", "step": "idle"}

        device = _find_device(device_name, device_type="switch")
        if not device:
            return {
                "reply": "No switches found in database. Add a switch via Settings.",
                "step": "idle",
            }

        ip = device["ip"]
        brand = device.get("brand", "unknown")
        netmiko_type = BRAND_TO_NETMIKO.get(
            brand.lower(),
            device.get("netmiko_type", brand.lower())
        )
        name = device["name"]

        # Normalise interface — intent_classifier may use "port" or "interface"
        interface_raw = params.get("interface") or params.get("port") or ""
        interface_full = get_full_interface_name(interface_raw, netmiko_type) if interface_raw else ""

        # ── Map intent to operation ───────────────────────────────────────
        if intent_type == "get_switch_config":
            operation = "show_interface"
        elif intent_type == "get_full_switch_config":
            operation = "show_full_config"
        elif intent_type == "configure_port_vlan":
            operation = "set_vlan"
        elif intent_type == "capability_query":
            return {
                "reply": (
                    f"I can do the following on {name} ({brand}):\n"
                    "• Show port config or full running config\n"
                    "• Configure port VLAN\n"
                    "• Show VLAN table, MAC table, LACP summary\n"
                    "• LLM-assisted commands for operations not in the verified map"
                ),
                "step": "idle",
            }
        else:
            operation = "show_full_config"

        # ── Validate parameters before any command building ───────────────
        validation = self._validate_parameters(operation, params, device_name)
        if validation:
            return {"reply": validation["error"], "step": "idle"}

        # ── Try verified command map ──────────────────────────────────────
        llm_generated = False
        commands = None
        task_description = None

        try:
            vlan_id = params.get("vlan_id") or params.get("vlan")
            cmd_kwargs = {}
            if interface_full:
                cmd_kwargs["interface"] = interface_full
            if vlan_id:
                cmd_kwargs["vlan_id"] = vlan_id
            commands = get_command(netmiko_type, operation, **cmd_kwargs)
        except KeyError:
            # Brand or operation not in verified map — fall through to LLM
            task_description = f"Operation: {operation}. Parameters: {params}"
            llm_generated = True

        if llm_generated:
            try:
                commands = _llm_generate_commands(brand, netmiko_type, task_description)
            except ValueError as e:
                return {"reply": str(e), "step": "idle"}
            except Exception as e:
                return {
                    "reply": f"AI is currently unavailable and {brand} is not in the verified command map. Cannot proceed.",
                    "step": "idle",
                }
            cmd_preview = "\n".join(commands) if isinstance(commands, list) else commands
            return {
                "reply": (
                    f"I'm using AI-generated commands for {brand} (not in verified map).\n\n"
                    f"Commands to send:\n{cmd_preview}\n\nConfirm? (yes/no)"
                ),
                "step": "confirm",
                "pending_action": {
                    "type": "switch_llm_exec",
                    "commands": commands,
                    "device": {k: v for k, v in device.items() if k != "password"},
                },
            }

        # ── Write ops → confirm before executing ─────────────────────────
        # list = send_config_set (write), str = send_command (read)
        if isinstance(commands, list):
            cmd_preview = "\n".join(commands)
            location = interface_raw or ip
            return {
                "reply": (
                    f"I will run these commands on {name} ({location}):\n\n"
                    f"{cmd_preview}\n\nConfirm? (yes/no)"
                ),
                "step": "confirm",
                "pending_action": {
                    "type": "switch_llm_exec",
                    "commands": commands,
                    "device": {k: v for k, v in device.items() if k != "password"},
                },
            }

        # ── Execute read commands immediately ─────────────────────────────
        try:
            output = _execute_commands(device, commands)
        except NetmikoAuthenticationException:
            return {
                "reply": f"Cannot connect to {name} ({ip}): authentication failed. Check credentials.",
                "step": "idle",
            }
        except NetmikoTimeoutException:
            return {
                "reply": f"Cannot connect to {name} ({ip}): connection timed out. Check SSH access.",
                "step": "idle",
            }
        except Exception as e:
            return {
                "reply": f"Cannot connect to {name} ({ip}). Check credentials and SSH access. ({e})",
                "step": "idle",
            }

        # ── Record successful read to memory ─────────────────────────────
        try:
            from memory import record_action
            record_action(
                category="switch_config",
                summary=f"{operation} on {interface_raw or name}",
                device_name=name,
                triggered_by=session.get("user", "unknown"),
            )
        except Exception:
            pass

        # ── Full config → open drawer ────────────────────────────────────
        if operation == "show_full_config":
            return {
                "reply": "Here is the full switch configuration.",
                "action": "show_full_config",
                "config": output.strip(),
                "step": "idle",
            }

        # ── VLAN change → verify ─────────────────────────────────────────
        verification = ""
        if operation == "set_vlan" and interface_full:
            try:
                verify_cmd = get_command(netmiko_type, "verify_vlan", interface=interface_full)
                verification = _execute_commands(device, verify_cmd)
            except Exception:
                verification = "(verification unavailable)"

            vlan_id = params.get("vlan_id") or params.get("vlan", "")
            return {
                "reply": (
                    f"Port {interface_raw} on {name} set to VLAN {vlan_id}."
                ),
                "raw_output": {
                    "commands_sent": commands if isinstance(commands, list) else [commands],
                    "device_response": output,
                    "verification": verification,
                },
                "step": "idle",
            }

        # ── Default show response ─────────────────────────────────────────
        return {
            "reply": f"Config for {interface_raw or name}:\n\n{output.strip()}",
            "raw_output": {
                "commands_sent": [commands] if isinstance(commands, str) else commands,
                "device_response": output,
                "verification": "",
            },
            "step": "idle",
        }


if __name__ == "__main__":
    from hardware.switch_commands import get_command, get_supported_brands

    print("=== Test 1: hp_comware set_vlan ===")
    try:
        cmds = get_command("hp_comware", "set_vlan", interface="Twenty-FiveGigE1/0/5", vlan_id=99)
        print("Commands:")
        for c in cmds:
            print(f"  {c}")
    except KeyError as e:
        print(f"KeyError: {e}")

    print("\n=== Test 2: unknown brand (expect KeyError) ===")
    try:
        cmds = get_command("aruba_os", "show_full_config")
        print(f"Commands: {cmds}")
    except KeyError as e:
        print(f"KeyError (expected): {e}")

    print(f"\nSupported brands: {get_supported_brands()}")
