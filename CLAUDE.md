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

# Restart
pkill -f "python3 app.py" && sleep 1 && python3 app.py
```

Access at: `http://<server-ip>:8000`
Default login: `admin` / `admin123`

---

## File Structure
diagnostic-tool/
├── app.py          ← FastAPI routes, DeepSeek API call, chat logic
├── bmc.py          ← BMC server management via Redfish (HPE iLO, Dell iDRAC, H3C HDM, any Redfish-compliant brand)
├── switch.py       ← Netmiko switch integration (80+ brands, dynamic from DB)
├── topology.py     ← Network topology builder
├── discovery.py    ← ARP scan + device classification
├── database.py     ← SQLite + Fernet encryption
├── auth.py         ← Login + session management
├── events.py       ← Real time event detection background thread
├── setup.py        ← First time setup wizard — run once on new machine
├── CLAUDE.md       ← This file
├── static/
│   ├── style.css   ← All CSS
│   └── app.js      ← All JavaScript
├── templates/
│   ├── base.html   ← Main layout — sidebar + content + chatbot + port drawer
│   ├── login.html  ← Login page
│   └── report.html ← Full diagnostic report page
├── .env            ← DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL (NOT in GitHub)
├── nexdeploy.db    ← SQLite database (NOT in GitHub)
└── .secret_key     ← Fernet encryption key (NOT in GitHub)

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Frontend | Vanilla HTML, CSS, JavaScript — no frameworks |
| Templates | Jinja2 |
| AI Chatbot | Direct DeepSeek V3.2 API via requests library |
| Server mgmt | Redfish REST API — brand agnostic (HPE, Dell, Lenovo, Supermicro, H3C, any Redfish-compliant BMC) |
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

DeepSeek config stored in `.env`:
DEEPSEEK_API_KEY=your_key
DEEPSEEK_BASE_URL=https://500.tokenvisor.ai/api/v1
DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V3.2

---

## Database Structure
users       (id, username, password_hash, role, created_at)
devices     (id, name, type, ip, username, password_encrypted, brand, model, added_by, created_at)
audit_log   (id, username, action, details, timestamp)
sessions    (id, username, token, expires_at, created_at)

Device types: `bmc`, `switch`, `router`
NOTE: type is `bmc` not `ilo` — this was renamed. If DB rows still say `ilo` run:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('/home/harris/diagnostic-tool/nexdeploy.db')
conn.execute(\"UPDATE devices SET type='bmc' WHERE type='ilo'\")
conn.commit()
conn.close()
print('Done')
"
```

---

## API Endpoints

| Method | Endpoint | What it does |
|--------|----------|--------------|
| GET | `/` | Main dashboard |
| GET/POST | `/login` | Login page / authenticate |
| GET | `/logout` | Clear session |
| GET | `/api/servers` | Live BMC health data |
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

**Fully dynamic — no hardcoded IPs or credentials:** Everything reads from SQLite database. Anyone clones repo, runs setup.py, adds their devices, system works with their infrastructure.

**Brand-agnostic servers via Redfish:** bmc.py dynamically discovers the correct Redfish paths by calling `/redfish/v1/Systems/`, `/redfish/v1/Chassis/`, `/redfish/v1/Managers/` and reading `Members[0]["@odata.id"]` — works for HPE (path: `/redfish/v1/Systems/1`), Dell (path: `/redfish/v1/Systems/System.Embedded.1`), and any other Redfish-compliant brand automatically.

**Brand-agnostic switches:** switch.py uses Netmiko — 80+ switch brands. Brand detected from SSH banner. Correct commands selected automatically via SWITCH_COMMANDS dict.

**Caching system:** BMC and switch data cached in memory. Cache warms on startup. Pages load instantly. Refreshes every 60 seconds staggered.

**Chat confirmation flow:** Dangerous actions (port config, power control) handled by FastAPI session — not AI. Engineer types action → system asks confirm → engineer types yes → Python executes directly.

**Real time event detection:** events.py runs as background thread. Compares current vs previous state every 30 seconds.

---

## Dashboard Pages

- **Topology** — visual network diagram, auto-generated from ARP + MAC + database
- **Servers** — BMC health cards, power state, health badges
- **Switches** — port grid, click any port → side drawer with raw config + VLAN change
- **Event Log** — real time feed from audit_log
- **Settings** — add/delete devices, change password

---

## Chatbot Capabilities

Engineer can ask:
- Server health status
- Switch port status
- Network topology
- Run diagnostic on specific IP
- Recent events
- `show full config` / `show switch config` → opens full running config in side drawer

Engineer can command (with approval gate):
- Set port X to VLAN Y
- Restart/shutdown server
- Power on server

---

## What's Built and Working

- Login system with bcrypt + session tokens
- Dashboard with 5 navigation pages
- Live BMC server health via Redfish API (brand-agnostic, dynamic path discovery)
- H3C switch via Netmiko (54 ports, UP/DOWN detection)
- Real time event detection (events.py)
- Direct DeepSeek API chatbot (1-3 second responses)
- Switch port config via chatbot with approval gate
- Power control via chatbot with approval gate
- Network topology auto-discovery
- Full diagnostic report
- Audit logging + event log page
- Add/delete devices from Settings
- Caching system
- setup.py — fully portable
- Netmiko — 80+ switch brands
- Switch port side drawer — click any port → raw config + VLAN change
- Full switch running config via chatbot → renders in side drawer
- Dynamic Redfish path discovery — works with any Redfish-compliant server brand

---

## What's Not Done Yet

**Immediate:**
- Full config drawer raw config box needs vertical scroll (currently clipped)
- Add visible button on Switches page to trigger full config (engineers shouldn't have to know the chat command)
- Delete old ilo.py from disk: `rm ~/diagnostic-tool/ilo.py`
- README with screenshots and demo GIF

**Blocked — need from senior:**
- Cisco Catalyst 3550 credentials (IP: 192.168.99.144)
- iLO network IP for bare metal servers
- Ubuntu Desktop ISO
- Windows Server Desktop Experience ISO
- Sangfor HCI ISO + install procedure
- RAID level used by company
- Post-install checklist

**OS Installation (architecture decided, not built):**
- Ubuntu Desktop — autoinstall.yaml
- Windows Server — unattend.xml
- Sangfor HCI — unknown, need senior
- RAID via BMC Smart Array Redfish API first
- ISO served via HTTP from AI server
- BMC virtual media mounting
- Boot order → DVD → power on → OS installs → SSH configure network

**Future Phase 3:**
- Hermes as autonomous background agent
- Telegram alerts
- Self-healing actions
- Docker packaging

---

## Lab Environment

| Device | IP | Details |
|--------|----|---------|
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

# Test BMC
python3 -c "from bmc import get_all_servers_status; import json; print(json.dumps(get_all_servers_status(), indent=2))"

# Test switch
python3 -c "from switch import get_switch_summary; import json; print(json.dumps(get_switch_summary(), indent=2))"

# Test switch raw config
python3 -c "from switch import get_port_raw_config; print(get_port_raw_config('WGE1/0/5'))"

# Test full switch config
python3 -c "from switch import get_full_config; print(get_full_config())"

# Test topology
python3 -c "from topology import build_topology; r = build_topology(); print(f'Nodes: {len(r[\"nodes\"])}')"

# View database
python3 -c "from database import get_devices; [print(d) for d in get_devices()]"

# Fix ilo→bmc in DB if needed
python3 -c "import sqlite3; conn = sqlite3.connect('/home/harris/diagnostic-tool/nexdeploy.db'); conn.execute(\"UPDATE devices SET type='bmc' WHERE type='ilo'\"); conn.commit(); conn.close(); print('Done')"

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
- Device type is `bmc` not `ilo` — was renamed, do not use `ilo` anywhere
- DeepSeek API called directly in `ask_ai()` function in app.py
- Hermes NOT used for chatbot — only installed for future Phase 3
- pageVersion counter in app.js prevents wrong page on fast navigation
- Cache uses threading — be careful with thread safety
- events.py runs as daemon thread — starts automatically with app
- .env file stores DeepSeek config — never commit to GitHub
- nexdeploy.db and .secret_key must travel together — different key = can't read db
- bmc.py uses dynamic Redfish path discovery — do not hardcode /redfish/v1/Systems/1
- Port drawer in base.html is reused for both port details and full switch config