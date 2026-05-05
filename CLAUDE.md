# CLAUDE.md — NexDeploy AI Project Context

## What This Project Is

NexDeploy AI is an intelligent server deployment assistant built for Cloudify Asia.
It helps data centre engineers set up, monitor, and manage servers and network devices
through a web-based AI chatbot interface. Engineers talk to the system in plain language
and it executes tasks automatically.

**GitHub:** https://github.com/SmartLemur/dc-diagnostic-tool
**Developer:** Harris (intern at Cloudify Asia)
**Status:** Phase 2 — active development

---

## How To Run

```bash
# First time setup
cd ~/diagnostic-tool
python3 setup.py

# Start the app
python3 app.py
```

Access at: `http://<server-ip>:8000`
Default login: `admin` / `admin123`

---

## File Structure
diagnostic-tool/
├── app.py              ← FastAPI routes, DeepSeek API call, chat logic
├── ilo.py              ← HPE/Dell/H3C server BMC via Redfish (dynamic from DB)
├── switch.py           ← Netmiko switch integration (80+ brands, dynamic from DB)
├── topology.py         ← Network topology builder
├── discovery.py        ← ARP scan + device classification
├── database.py         ← SQLite + Fernet encryption
├── auth.py             ← Login + session management
├── events.py           ← Real time event detection background thread
├── setup.py            ← First time setup wizard — run once on new machine
├── CLAUDE.md           ← This file
├── static/
│   ├── style.css       ← All CSS
│   └── app.js          ← All JavaScript
├── templates/
│   ├── base.html       ← Main layout — sidebar + content + chatbot
│   ├── login.html      ← Login page
│   └── report.html     ← Full diagnostic report page
├── .env                ← DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL (NOT in GitHub)
├── nexdeploy.db        ← SQLite database (NOT in GitHub)
└── .secret_key         ← Fernet encryption key (NOT in GitHub)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Frontend | Vanilla HTML, CSS, JavaScript — no frameworks |
| Templates | Jinja2 |
| AI Chatbot | Direct DeepSeek V3.2 API via requests library |
| Server mgmt | Redfish REST API (HPE iLO, Dell iDRAC, H3C HDM) |
| Switch mgmt | Netmiko — 80+ switch brands supported |
| Linux access | paramiko SSH |
| Windows access | pywinrm WinRM |
| Database | SQLite with Fernet AES-256 encryption |
| Auth | bcrypt + session tokens |
| Network scan | arp-scan |
| Event monitor | Python threading — background daemon |

---

## AI Architecture
Web chatbot → Direct DeepSeek V3.2 API (1-3 second responses)
Live system data injected as context before each message
Conversation history stored per session
Handles confirmation flow for dangerous actions
Hermes Agent → Installed but NOT used for chatbot
Kept for future Phase 3 autonomous background agent
nexdeploy-dc skill written at ~/.hermes/skills/nexdeploy/nexdeploy-dc/SKILL.md

DeepSeek config stored in `.env`:
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://500.tokenvisor.ai/api/v1
DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V3.2

---

## Database Structure

```sql
users (id, username, password_hash, role, created_at)
devices (id, name, type, ip, username, password_encrypted, brand, model, added_by, created_at)
audit_log (id, username, action, details, timestamp)
sessions (id, username, token, expires_at, created_at)
```

Device types: `ilo`, `switch`, `router`

---

## API Endpoints

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/` | Main dashboard |
| GET/POST | `/login` | Login page / authenticate |
| GET | `/logout` | Clear session |
| GET | `/api/servers` | Live iLO health data |
| GET | `/api/switch` | Switch port status |
| GET | `/api/switch/port/{interface}` | Raw config for specific port |
| GET | `/api/topology` | Full network topology |
| GET | `/api/events` | Audit log / event feed |
| GET | `/api/devices` | All registered devices |
| POST | `/api/devices` | Add new device |
| DELETE | `/api/devices/{id}` | Delete device |
| POST | `/api/change-password` | Change password |
| POST | `/chat` | AI chatbot message |
| POST | `/run_diagnostic` | Run network diagnostic |
| GET | `/report` | Full diagnostic report page |

---

## Key Architecture Decisions

**Fully dynamic — no hardcoded IPs or credentials:**
Everything reads from SQLite database. Anyone clones repo, runs setup.py, adds their devices, system works with their infrastructure.

**Brand-agnostic servers:**
ilo.py uses Redfish API — same standard for HPE, Dell, H3C. Auto-detects brand from Manufacturer field.

**Brand-agnostic switches:**
switch.py uses Netmiko — 80+ switch brands. Brand detected from SSH banner. Correct commands selected automatically via SWITCH_COMMANDS dict and BRAND_TO_NETMIKO mapping.

**Caching system:**
iLO and switch data cached in memory. Cache warms on startup. Pages load instantly. Refreshes every 60 seconds staggered.

**Chat confirmation flow:**
Dangerous actions (port config, power control) handled by FastAPI session — not AI.
Engineer types action → system asks confirm → engineer types yes → Python executes directly.

**Real time event detection:**
events.py runs as background thread. Compares current vs previous state every 30 seconds.
Detects: port up/down, server power changes, server health changes, new devices on network.

---

## Dashboard Pages

**Topology** — visual network diagram, auto-generated from ARP + MAC + database
**Servers** — iLO health cards, power state, health badges
**Switches** — port grid, click port for raw config (side panel — in progress)
**Event Log** — real time feed from audit_log
**Settings** — add/delete devices, change password

---

## Chatbot Capabilities

Engineer can ask:
- Server health status
- Switch port status
- Network topology
- Run diagnostic on specific IP
- Recent events

Engineer can command (with approval gate):
- Set port X to VLAN Y
- Restart/shutdown server
- Power on server

---

## What's Built and Working

- Login system with bcrypt + session tokens
- Dashboard with 5 navigation pages
- Live iLO server health via Redfish API
- H3C switch via Netmiko (54 ports, 6 UP detected)
- Real time event detection (events.py)
- Direct DeepSeek API chatbot (1-3 second responses)
- Switch port config via chatbot with approval gate
- Power control via chatbot with approval gate
- Network topology auto-discovery
- Full diagnostic report
- Audit logging + event log page
- Add/delete devices from Settings
- Caching system
- Page version control (no wrong content on fast nav)
- setup.py — fully portable
- Netmiko — 80+ switch brands
- Switch raw config API endpoint /api/switch/port/{interface}

---

## What's Not Done Yet

**Immediate (no blockers):**
- Fix switch raw config 401 cookie issue
- Switch side panel — click port → raw config slides in
- UI/UX polish
- README with screenshots

**Blocked — need from senior:**
- Cisco Catalyst 3550 credentials (IP: 192.168.99.144)
- iLO network IP for bare metal servers
- Ubuntu Desktop ISO
- Windows Server Desktop Experience ISO
- Sangfor HCI ISO + install procedure
- RAID level used by company
- Post-install checklist

**OS Installation (architecture decided, not built):**
- Ubuntu Desktop — autoinstall.yaml (no clicking needed)
- Windows Server — unattend.xml (no clicking needed)
- Sangfor HCI — unknown, need senior
- RAID via iLO Smart Array Redfish API first
- ISO served via HTTP from AI server
- iLO virtual media mounting
- Boot order → DVD → power on → OS installs → SSH configure network

**Future Phase 3:**
- Hermes as autonomous background agent
- Telegram alerts
- Self-healing actions
- Docker packaging

---

## Lab Environment

| Device | IP | Details |
|---|---|---|
| HarrisPlayground | 192.168.99.142 | Ubuntu 24.04, runs NexDeploy |
| S2D-Node1 | 192.168.99.104 | HPE ProLiant DL380 Gen10, iLO5 |
| H3C Switch | 192.168.99.5 | Netmiko hp_comware, 54 ports |
| Cisco Catalyst 3550 | 192.168.99.144 | No credentials yet |
| Router/Gateway | 192.168.99.1 | |

---

## Common Commands

```bash
# Start
cd ~/diagnostic-tool && python3 app.py

# Restart
pkill -f "python3 app.py" && sleep 1 && python3 app.py

# Test iLO
python3 -c "from ilo import get_all_servers_status; import json; print(json.dumps(get_all_servers_status(), indent=2))"

# Test switch
python3 -c "from switch import get_switch_summary; import json; print(json.dumps(get_switch_summary(), indent=2))"

# Test switch raw config
python3 -c "from switch import get_port_raw_config; print(get_port_raw_config('WGE1/0/5'))"

# Test topology
python3 -c "from topology import build_topology; r = build_topology(); print(f'Nodes: {len(r[\"nodes\"])}')"

# View database
python3 -c "from database import get_devices; [print(d) for d in get_devices()]"

# Push to GitHub
cd ~/diagnostic-tool && git add . && git commit -m 'update' && git push origin main
```

---

## Dependencies
fastapi
uvicorn
requests
paramiko
pywinrm
bcrypt
cryptography
python-jose[cryptography]
python-multipart
sqlalchemy
jinja2
netmiko

Install: `pip3 install -r requirements.txt --break-system-packages`

---

## Notes for Claude Code

- All HTML in `templates/` as Jinja2 templates
- All CSS in `static/style.css`
- All JavaScript in `static/app.js`
- Never put HTML inside `app.py` — routes only
- Never hardcode IPs or credentials — always read from database
- DeepSeek API called directly in `ask_ai()` function in app.py
- Hermes NOT used for chatbot — only installed for future Phase 3
- pageVersion counter in app.js prevents wrong page on fast navigation
- Cache uses threading — be careful with thread safety
- events.py runs as daemon thread — starts automatically with app
- .env file stores DeepSeek config — never commit to GitHub
- nexdeploy.db and .secret_key must travel together — different key = can't read db