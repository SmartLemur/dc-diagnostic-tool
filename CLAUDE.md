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
cd ~/diagnostic-tool
python3 app.py
```

Access at: `http://<server-ip>:8000`
Default login: `admin` / `admin123`

**First time setup:**
```bash
python3 database.py  # creates database and admin account
python3 app.py       # start the web app
```

---

## Full File Structure

```
diagnostic-tool/
├── app.py              # FastAPI routes only — no HTML inside
├── ilo.py              # Brand-agnostic BMC/iLO/iDRAC integration (Redfish API)
├── switch.py           # Brand-agnostic switch integration (H3C, Cisco, Juniper)
├── topology.py         # Network topology builder — combines ARP + MAC + database
├── discovery.py        # Network discovery — ARP scan + device classification
├── database.py         # SQLite database functions + encrypted credential storage
├── auth.py             # Login, session management, bcrypt password hashing
├── diagnostic.py       # Legacy diagnostic functions (ping, DNS, ports, deep scan)
│
├── static/
│   ├── style.css       # All CSS — dark theme, component styles
│   └── app.js          # All JavaScript — navigation, API calls, chatbot
│
├── templates/
│   ├── base.html       # Main layout — sidebar nav + content area + chatbot
│   ├── login.html      # Login page
│   └── report.html     # Full diagnostic report page
│
├── nexdeploy.db        # SQLite database (NOT in GitHub)
├── .secret_key         # Encryption key for credentials (NOT in GitHub)
├── requirements.txt    # Python dependencies
└── CLAUDE.md           # This file
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Frontend | Vanilla HTML, CSS, JavaScript (no frameworks) |
| Templates | Jinja2 |
| AI (local) | Ollama + Gemma4:26b — for deep diagnostic analysis |
| AI (fast) | Hermes Agent + DeepSeek V3.2 — for chatbot conversation |
| Server mgmt | Redfish REST API (HPE iLO, Dell iDRAC, H3C HDM) |
| Switch mgmt | SSH via paramiko (H3C, Cisco, Juniper) |
| Linux access | paramiko SSH |
| Windows access | pywinrm WinRM |
| Database | SQLite |
| Encryption | cryptography.Fernet |
| Auth | bcrypt + session tokens |
| Network scan | arp-scan |

---

## Database Structure

```sql
-- Engineer login accounts
users (id, username, password_hash, role, created_at)

-- All network devices — iLO, switches, routers
-- Credentials are AES-256 encrypted before storage
devices (id, name, type, ip, username, password_encrypted, brand, model, added_by, created_at)

-- Full audit trail of all actions
audit_log (id, username, action, details, timestamp)

-- Active login sessions
sessions (id, username, token, expires_at, created_at)
```

**Device types in database:**
- `ilo` — server BMC (HPE iLO, Dell iDRAC, H3C HDM)
- `switch` — network switch (H3C, Cisco, Juniper)
- `router` — network router

---

## API Endpoints

| Method | Endpoint | What it does |
|---|---|---|
| GET | `/` | Main dashboard (requires login) |
| GET | `/login` | Login page |
| POST | `/login` | Authenticate engineer |
| GET | `/logout` | Clear session |
| GET | `/api/servers` | Live iLO health data for all servers |
| GET | `/api/switch` | Switch port status |
| GET | `/api/topology` | Full network topology |
| GET | `/api/events` | Audit log / event feed |
| GET | `/api/devices` | All registered devices from database |
| POST | `/api/devices` | Add new device |
| DELETE | `/api/devices/{id}` | Delete device |
| POST | `/api/change-password` | Change engineer password |
| POST | `/chat` | Send message to AI chatbot |
| POST | `/run_diagnostic` | Run full network diagnostic on a server |
| GET | `/report` | Full diagnostic report page |

---

## Key Architecture Decisions

**Fully dynamic — no hardcoded IPs or credentials:**
All device IPs, usernames and passwords are stored in SQLite database.
Adding a new device via Settings page makes it immediately available to all functions.
Anyone who clones the repo and adds their own devices gets a fully working system.

**Brand-agnostic device support:**
- `ilo.py` — works with HPE, Dell, H3C servers via Redfish standard API
- `switch.py` — works with H3C, Cisco, Juniper switches via SSH
- Brand is auto-detected from SSH banner or Redfish Manufacturer field
- Correct commands selected automatically per brand via SWITCH_COMMANDS dict

**Caching system:**
All slow API calls (iLO, switch SSH) are cached in memory.
Cache warms up on startup in background thread.
Pages load instantly after first load.
Cache refreshes every 60 seconds staggered to avoid blocking.

**Two AI models:**
- Hermes Agent + DeepSeek V3.2 — fast cloud AI for chatbot conversation
- Gemma4:26b via Ollama — local AI for sensitive deep diagnostic analysis

**Hermes chatbot integration:**
Chatbot calls Hermes via command line subprocess.
Live system data (servers, switches, topology, events) injected into context before each call.
Conversation history stored per session for memory.

---

## Dashboard Pages

**Topology (default)**
Visual network diagram — auto-generated from ARP scan + MAC table + database.
Shows all devices, connections, live status.

**Servers**
iLO health cards for all registered servers.
Shows power state, health badges, RAM, CPU.
Click card to run diagnostic.
Add Server button — enters credentials, auto-detects brand.

**Switches**
Port grid for all registered switches.
Green = UP, Grey = DOWN.
Shows connected device per port.
Add Switch button.

**Event Log**
Real time feed from audit_log table.
Shows who did what and when.

**Settings**
Manage devices (add/delete).
Change password.

---

## Network Discovery Flow

```
1. ARP scan → finds all IPs and MACs on network
2. Classify each IP (check ports 443, 22, 5985, 161)
3. Identify device type (iLO/server/switch/unknown)
4. Cross-reference with database for names
5. Cross-reference MAC table from switch for port mapping
6. Build topology diagram
```

---

## Environment Variables / Sensitive Files

These files are in `.gitignore` and must NEVER be committed:
- `nexdeploy.db` — contains encrypted credentials
- `.secret_key` — Fernet encryption key (must travel with database)

**Migration:** Always copy both files together. Different key = can't read database.

---

## Current Lab Environment

| Device | IP | Details |
|---|---|---|
| HarrisPlayground (AI server) | 192.168.99.105 | Ubuntu 24.04, runs NexDeploy |
| S2D-Node1 | 192.168.99.104 | HPE ProLiant DL380 Gen10, iLO5 |
| H3C Switch | 192.168.99.5 | SSH enabled, 51 ports |
| Cisco Catalyst 3550 | 192.168.99.144 | SSH/Telnet enabled |
| Router/Gateway | 192.168.99.1 | |

---

## What's Built and Working

- Login system with session management
- Encrypted credential storage
- Live dashboard with sidebar navigation
- iLO server health (Redfish API)
- H3C switch port status (SSH)
- Network topology auto-discovery
- AI chatbot with live system context
- Hermes Agent + DeepSeek V3.2 integration
- Full diagnostic report (ping, DNS, ports, deep scan)
- Linux deep scan via SSH
- Windows deep scan via WinRM
- OS auto-detection
- IP conflict detection with MAC addresses
- Audit logging
- Add/delete devices from dashboard
- Caching system for fast page loads
- Page version control (no wrong page content on fast navigation)

---

## What's Planned / Not Done Yet

- OS installation via chatbot (needs ISO files + templates from senior)
- Switch port configuration via chatbot (needs approval gate)
- Real time event detection (cable plug/unplug state change)
- Cisco switch SSH integration (have IP, need credentials)
- iLO network access for new bare metal servers
- Telegram/Slack alerts
- Docker packaging (Phase 4)
- UI/UX improvements (deferred)

---

## Common Commands

```bash
# Start the app
cd ~/diagnostic-tool && python3 app.py

# Kill and restart
pkill -f "python3 app.py" && sleep 1 && python3 app.py

# Test iLO connection
python3 -c "from ilo import get_all_servers_status; import json; print(json.dumps(get_all_servers_status(), indent=2))"

# Test switch connection
python3 -c "from switch import get_switch_summary; import json; print(json.dumps(get_switch_summary(), indent=2))"

# Test topology
python3 -c "from topology import build_topology; r = build_topology(); print(f'Nodes: {len(r[\"nodes\"])}')"

# View database devices
python3 -c "from database import get_devices; [print(d) for d in get_devices()]"

# Push to GitHub
cd ~/diagnostic-tool && git add . && git commit -m 'update' && git push origin main

# Start Hermes
hermes

# Test Hermes
hermes chat -q "say hello"
```

---

## Dependencies (requirements.txt)

```
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
```

Install: `pip3 install -r requirements.txt --break-system-packages`

---

## Notes for Claude Code

- All HTML lives in `templates/` folder as Jinja2 templates
- All CSS lives in `static/style.css`
- All JavaScript lives in `static/app.js`
- Never put HTML inside `app.py` — routes only
- Never hardcode IPs or credentials — always read from database
- The `pageVersion` counter in app.js prevents wrong page content on fast navigation
- Cache system in app.py uses threading — be careful with thread safety
- Hermes is called via subprocess — response parsed from box characters in output