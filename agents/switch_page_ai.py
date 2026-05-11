import os
import re
import requests


class SwitchPageAI:
    """
    Dedicated AI for the Switch Intelligence Page.
    No IntentClassifier. No AgentRouter. No GeneralAgent.
    One class. One purpose. Always uses shared SSH session.
    """

    GREETINGS = {
        "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
        "huh", "wtf", "nice", "good", "cool", "great", "perfect", "done"
    }

    WRITE_KEYWORDS = [
        "configure", "set port", "set vlan", "change vlan",
        "create vlan", "delete vlan", "trunk", "add vlan",
        "remove vlan", "shutdown", "no shutdown", "lacp",
        "aggregation", "port link-type", "port access",
        "port trunk", "description", "speed", "duplex"
    ]

    def handle(self, message, conn, switch, session, user):
        """
        Main entry point. Returns:
        {
          "reply": str,        # right panel — plain English
          "raw_output": str,   # left panel — exact terminal output
          "step": str,         # idle or confirm
          "pending_action": dict or None
        }
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # Step 1 — Handle greetings locally, no LLM, no switch query
        if msg_lower in self.GREETINGS or (
            len(msg.split()) <= 2 and not any(
                k in msg_lower for k in ["port", "vlan", "show", "config", "display", "interface"]
            )
        ):
            return {
                "reply": f"I'm your dedicated assistant for {switch['name']}. Ask me anything about this switch or tell me to configure something.",
                "raw_output": "",
                "step": "idle",
                "pending_action": None
            }

        # Step 2 — Determine if this is a write or read operation
        is_write = any(kw in msg_lower for kw in self.WRITE_KEYWORDS)

        # Step 3 — Ask LLM what command(s) to run
        commands = self._plan_commands(msg, switch, is_write)

        if not commands:
            return {
                "reply": "I couldn't determine the right command for that. Could you rephrase?",
                "raw_output": "",
                "step": "idle",
                "pending_action": None
            }

        # Step 4 — Write ops need confirmation
        if is_write:
            commands_display = "\n".join(commands)
            return {
                "reply": f"I will run these commands on {switch['name']}:\n\n{commands_display}\n\nConfirm? (yes/no)",
                "raw_output": "",
                "step": "confirm",
                "pending_action": {
                    "type": "switch_page_exec",
                    "commands": commands,
                    "switch": {"name": switch["name"], "ip": switch["ip"]}
                }
            }

        # Step 5 — Read ops execute immediately on shared conn
        raw_output = self._execute_read(commands, conn, switch)

        # Step 6 — Summarize for right panel
        summary = self._summarize(msg, raw_output, switch)

        return {
            "reply": summary,
            "raw_output": raw_output,
            "step": "idle",
            "pending_action": None
        }

    def execute_confirmed(self, commands, conn, switch, user):
        """Execute write commands after engineer confirms. Returns raw output."""
        try:
            # Strip auto-handled commands
            clean = [
                c for c in commands
                if c.strip().lower() not in {"system-view", "return", "end", "exit", "quit"}
            ]
            output = conn.send_config_set(clean)

            # Verify by re-reading first interface mentioned
            verify_output = ""
            iface_match = re.search(r'interface\s+(\S+)', "\n".join(commands), re.IGNORECASE)
            if iface_match:
                iface = iface_match.group(1)
                try:
                    verify_output = conn.send_command(
                        f"display current-configuration interface {iface}"
                    )
                except Exception:
                    pass

            raw = f"[Executing {len(clean)} commands...]\n{output}"
            if verify_output:
                raw += f"\n\n[Verification]\n{verify_output}"

            return {"success": True, "raw_output": raw}
        except Exception as e:
            return {"success": False, "raw_output": f"Error: {str(e)}"}

    def _plan_commands(self, message, switch, is_write):
        """Ask LLM what exact CLI commands to run. Returns list of commands."""
        env = self._load_env()
        if not env.get("api_key"):
            return []

        brand = switch.get("brand", "H3C")

        if is_write:
            system_prompt = f"""You are a network engineer expert in {brand} switches.
Generate the exact CLI commands to complete this task.

STRICT FORMAT:
---COMMANDS---
[one command per line]
---END---

Rules:
- Do NOT include system-view, return, end, exit — these are handled automatically
- Only include the actual config commands
- Use correct {brand} Comware syntax
- No explanations"""
        else:
            system_prompt = f"""You are a network engineer expert in {brand} switches.
Return the single best CLI command to answer this query.

STRICT FORMAT:
---COMMANDS---
[single show/display command]
---END---

Rules:
- One command only
- Use correct {brand} Comware syntax
- No explanations"""

        try:
            r = requests.post(
                env["base_url"] + "/chat/completions",
                headers={"Authorization": f"Bearer {env['api_key']}"},
                json={
                    "model": env["model"],
                    "temperature": 0.0,
                    "max_tokens": 300,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Switch: {brand}. Task: {message}"}
                    ]
                },
                timeout=15
            )
            raw = r.json()["choices"][0]["message"]["content"]
            return self._extract_commands(raw)
        except Exception:
            return []

    def _execute_read(self, commands, conn, switch):
        """Execute read commands on shared SSH connection."""
        outputs = []
        for cmd in commands:
            try:
                out = conn.send_command(cmd, read_timeout=15)
                outputs.append(f"<{switch.get('name', 'H3C')}> {cmd}\n{out}")
            except Exception as e:
                outputs.append(f"<{switch.get('name', 'H3C')}> {cmd}\nError: {str(e)}")
        return "\n\n".join(outputs)

    def _summarize(self, question, raw_output, switch):
        """Ask LLM to summarize raw output in plain English."""
        env = self._load_env()
        if not env.get("api_key") or not raw_output.strip():
            return "Done."
        try:
            r = requests.post(
                env["base_url"] + "/chat/completions",
                headers={"Authorization": f"Bearer {env['api_key']}"},
                json={
                    "model": env["model"],
                    "temperature": 0.0,
                    "max_tokens": 200,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a senior network engineer. Summarize this switch output in 1-3 plain English sentences. Be direct and specific. No markdown."
                        },
                        {
                            "role": "user",
                            "content": f"Question: {question}\nOutput: {raw_output[:600]}"
                        }
                    ]
                },
                timeout=15
            )
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return raw_output[:200]

    def _extract_commands(self, raw_text):
        """Extract CLI commands from LLM response using delimiter."""
        if "---COMMANDS---" in raw_text:
            try:
                start = raw_text.index("---COMMANDS---") + len("---COMMANDS---")
                end = raw_text.index("---END---") if "---END---" in raw_text else len(raw_text)
                block = raw_text[start:end].strip()
                return [
                    l.strip() for l in block.split("\n")
                    if l.strip() and not l.strip().startswith("#")
                ]
            except Exception:
                pass
        return []

    def _load_env(self):
        env = {}
        try:
            env_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
            )
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("LLM_API_KEY"):
                        env["api_key"] = line.split("=", 1)[1].strip()
                    elif line.startswith("LLM_BASE_URL"):
                        env["base_url"] = line.split("=", 1)[1].strip()
                    elif line.startswith("LLM_MODEL"):
                        env["model"] = line.split("=", 1)[1].strip()
        except Exception:
            pass
        return env
