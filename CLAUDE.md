# CLAUDE.md — NexDeploy AI

## Project
NexDeploy AI — open-source plug-and-play data centre management platform.
GitHub: https://github.com/SmartLemur/dc-diagnostic-tool
Developer: Harris (Cloudify Asia intern)
Run: `cd ~/diagnostic-tool && python3 app.py` → http://192.168.99.142:8000

**Full architecture:** See NEXDEPLOY_ARCHITECTURE.md in repo root.

---

## Stack
- Backend: Python 3.12, FastAPI, Uvicorn
- Frontend: Vanilla HTML/CSS/JS, Jinja2 templates
- AI: DeepSeek V3.2 via direct API (config in .env)
- Servers: Redfish REST API — brand agnostic
- Switches: Netmiko — 80+ brands
- DB: SQLite + Fernet AES-256 encryption
- Auth: bcrypt + session tokens

---

## File Structure
app.py          ← FastAPI routes ONLY — no business logic
bmc.py          ← Redfish server management
switch.py       ← Netmiko switch management
topology.py     ← Network topology builder
discovery.py    ← ARP scan + device classification
database.py     ← SQLite + encryption
auth.py         ← Login + session + roles
events.py       ← Real time event detection (daemon thread)
memory.py       ← System memory (TO BUILD)
setup.py        ← First time setup wizard
agents/         ← Agent classes (TO BUILD)
hardware/       ← Command maps + Redfish client (TO BUILD)
static/style.css
static/app.js
templates/base.html
templates/login.html
templates/report.html
.env            ← LLM config — NEVER commit
nexdeploy.db    ← SQLite DB — NEVER commit
.secret_key     ← Fernet key — NEVER commit

---

## Critical Rules
- NEVER hardcode IPs or credentials — always read from DB
- NEVER put HTML inside app.py — routes only
- NEVER use device type "ilo" — it is "bmc"
- NEVER commit .env, nexdeploy.db, .secret_key
- ALWAYS read from DB dynamically — no assumptions about environment
- bmc.py uses dynamic Redfish path discovery — never hardcode /redfish/v1/Systems/1
- DeepSeek called in ask_ai() in app.py — Hermes NOT used for chatbot
- Port drawer in base.html reused for port details AND full switch config
- events.py runs as daemon thread — starts automatically with app
- nexdeploy.db and .secret_key must travel together

---

## Database
users        (id, username, password_hash, role, created_at)
devices      (id, name, type, ip, username, password_encrypted, brand, model, added_by, created_at)
audit_log    (id, username, action, details, timestamp)
sessions     (id, username, token, expires_at, created_at)
system_memory (id, category, device_name, summary, timestamp, triggered_by)
Device types: `bmc`, `switch`, `router`

---

## Lab Environment
| Device | IP | Details |
|--------|----|---------|
| HarrisPlayground | 192.168.99.142 | Ubuntu 24.04, runs NexDeploy |
| S2D-Node1 | 192.168.99.104 | HPE ProLiant DL380 Gen10, iLO5 |
| H3C Switch | 192.168.99.5 | hp_comware, 54 ports |
| Cisco Catalyst 3550 | 192.168.99.144 | No credentials yet |
| Gateway | 192.168.99.1 | |

---

## DeepSeek Config (.env)
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://500.tokenvisor.ai/api/v1
DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V3.2

---

## Common Commands
```bash
# Start
cd ~/diagnostic-tool && python3 app.py

# Restart
pkill -f "python3 app.py" && sleep 1 && python3 app.py

# Test BMC
python3 -c "from bmc import get_all_servers_status; import json; print(json.dumps(get_all_servers_status(), indent=2))"

# Test switch
python3 -c "from switch import get_switch_summary; import json; print(json.dumps(get_switch_summary(), indent=2))"

# Fix ilo→bmc in DB
python3 -c "import sqlite3; conn = sqlite3.connect('/home/harris/diagnostic-tool/nexdeploy.db'); conn.execute(\"UPDATE devices SET type='bmc' WHERE type='ilo'\"); conn.commit(); conn.close(); print('Done')"

# Push to GitHub
cd ~/diagnostic-tool && git add . && git commit -m 'update' && git push origin main
```
## Session Update — Latest Progress

### Newly Built and Working
- Agent architecture (IntentClassifier → AgentRouter → SwitchAgent/BMCAgent/GeneralAgent)
- Two-gate chat pipeline (Gate 1 action detection, Gate 2 free conversation)
- Session persistence in SQLite DB (survives refresh and restart)
- Confirmation flow for all write operations
- LLM command extraction via delimiter approach (provider-agnostic)
- Parameter validation and normalisation in SwitchAgent
- Brand to Netmiko type mapping (H3C → hp_comware etc)
- Memory system (system_memory table, records all actions, injects context into LLM)
- Auto device resolution (single device auto-selected, multiple prompts engineer)
- VLAN creation before port assignment in command map

### Still To Build
- Fix 6 — SSE streaming LiveActionPanel (next priority)
- Fix 7 — Context scoping per agent
- Threading for parallel switch polling
- Firewall/router/NAS device types
- OS deployment
- Role system (admin vs engineer)
- LLM provider choice in setup.py
- README with screenshots and demo GIF
