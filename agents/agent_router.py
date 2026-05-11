import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.switch_agent import SwitchAgent
from agents.bmc_agent import BMCAgent

SWITCH_INTENTS = {
    "get_switch_config",
    "get_full_switch_config",
    "show_switch_info",
    "configure_port_vlan",
    "configure_port_lacp",
}


class AgentRouter:
    def route(self, intent: dict, session: dict) -> dict:
        intent_type = intent.get("intent", "general_question")
        clarification_needed = intent.get("clarification_needed", False)
        device_type = intent.get("device_type")

        if clarification_needed:
            question = intent.get("clarification_question") or "Could you clarify your request?"
            return {"reply": question, "step": "idle"}

        if intent_type in SWITCH_INTENTS:
            return self._handle_switch(intent, session)

        if intent_type == "capability_query":
            if device_type == "switch":
                return self._handle_switch(intent, session)
            if device_type == "bmc":
                return self._handle_bmc(intent, session)
            return self._handle_general(intent, session)

        if intent_type in ("get_server_health", "server_power_action"):
            return self._handle_bmc(intent, session)

        if intent_type == "run_diagnostic":
            return self._handle_diagnostic(intent, session)

        if intent_type == "os_deploy":
            return {
                "reply": "OS deployment coming soon. This feature is under construction.",
                "step": "idle",
            }

        if intent_type == "troubleshoot":
            return self._handle_troubleshoot(intent, session)

        if intent_type == "out_of_scope":
            return {
                "reply": (
                    "I can't help with that. Here's what I can do: "
                    "check server health, manage switch ports, run diagnostics, "
                    "show switch config, and control server power."
                ),
                "step": "idle",
            }

        return self._handle_general(intent, session)

    # ── Placeholder handlers ────────────────────────────────────────────

    def _handle_switch(self, intent: dict, session: dict) -> dict:
        agent = SwitchAgent()
        return agent.handle(intent, session)

    def _handle_bmc(self, intent: dict, session: dict) -> dict:
        agent = BMCAgent()
        return agent.handle(intent, session)

    def _handle_diagnostic(self, intent: dict, session: dict) -> dict:
        return {"reply": "DiagnosticAgent not built yet.", "step": "idle"}

    def _handle_troubleshoot(self, intent: dict, session: dict) -> dict:
        return {"reply": "TroubleshootAgent not built yet.", "step": "idle"}

    def _handle_general(self, intent: dict, session: dict) -> dict:
        from agents.general_agent import GeneralAgent
        agent = GeneralAgent()
        original_message = intent.get("parameters", {}).get("original_message", "")
        return agent.handle(intent, session, original_message)


if __name__ == "__main__":
    import json

    router = AgentRouter()
    session = {"session_id": "test-session"}

    tests = [
        {
            "intent": "configure_port_vlan",
            "device_type": "switch",
            "device_name": "H3C Switch",
            "parameters": {"port": "WGE1/0/5", "vlan": 99},
            "confidence": "high",
            "clarification_needed": False,
            "clarification_question": None,
        },
        {
            "intent": "general_question",
            "device_type": None,
            "device_name": None,
            "parameters": {"original_message": "What is LACP?"},
            "confidence": "high",
            "clarification_needed": False,
            "clarification_question": None,
        },
        {
            "intent": "out_of_scope",
            "device_type": None,
            "device_name": None,
            "parameters": {},
            "confidence": "high",
            "clarification_needed": False,
            "clarification_question": None,
        },
    ]

    labels = ["configure_port_vlan (high confidence)", "general_question", "out_of_scope"]
    for label, intent in zip(labels, tests):
        print(f"\nTest    : {label}")
        result = router.route(intent, session)
        print(json.dumps(result, indent=2))
