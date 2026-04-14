# Data Centre Deployment Diagnostic Tool

An AI-powered diagnostic system for detecting and explaining problems during new server deployments in a data centre. Built for Cloudify Asia's internal infrastructure team.

---

## What This System Does

When an engineer deploys a new server in the data centre — mounting it in the rack, plugging in cables, configuring the switch and IP — this tool automatically checks everything that could go wrong and tells the engineer exactly what the problem is in plain language, with the exact command to fix it.

No more manually checking 10 different things. No more guessing. The AI does the diagnosis.

---

## Architecture

```
Engineer's Laptop (SSH)
        ↓
AI Server — HarrisPlayground (Ubuntu 24.04 VM)
        ├── Ollama + Gemma4:26b (AI brain)
        ├── diagnostic.py (check engine)
        └── Reaches out over network to:
                ├── Target server (Linux via SSH)
                ├── Target server (Windows via WinRM)
                ├── Gateway / Router (ping)
                ├── DNS server
                └── Network scan (ARP / IP conflict)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Hardware | HPE ProLiant DL380 Gen10 — 512GB RAM, 40 CPU cores |
| Host OS | Windows Server 2025 + Hyper-V |
| AI Server VM | Ubuntu 24.04 LTS — HarrisPlayground |
| AI Engine | Ollama + Gemma4:26b (CPU mode) |
| Backend | Python 3.12 |
| Linux access | paramiko (SSH) |
| Windows access | pywinrm (WinRM / PowerShell) |
| Network scanning | arp-scan |
| Dependencies | requests, socket, subprocess |

---

## What It Checks

| Check | Description |
|---|---|
| Gateway reachable | Can the server reach the router |
| Server reachable | Is the target server alive (ping) |
| DNS working | Can it resolve hostnames |
| SSH open | Is SSH port 22 accessible (Linux) |
| WinRM open | Is WinRM port 5985 accessible (Windows) |
| HTTP open | Is port 80 accessible |
| HTTPS open | Is port 443 accessible |
| IP conflict | Is the IP already used by another device (with MAC addresses) |
| OS detection | Automatically detects Linux vs Windows |
| Deep scan (Linux) | SSH in — reads services, firewall, disk, RAM, CPU, routes, ports |
| Deep scan (Windows) | WinRM in — reads services, firewall, disk, RAM, CPU, routes, ports |

---

## Roadmap

### Phase 1 — Deployment Validator (Complete)
- Network checks (ping, DNS, ports)
- IP conflict detection with MAC address identification
- Auto OS detection (Linux vs Windows)
- Deep scan via SSH (Linux) and WinRM (Windows)
- AI diagnosis with prioritised problems and exact fix commands

### Phase 2 — Always-On Monitor (Planned)
- Web UI — engineer opens browser instead of terminal
- Hermes Agent for 24/7 autonomous monitoring
- Telegram/Slack alerts when problems detected
- H3C/Cisco switch SSH integration
- HPE iLO / Dell iDRAC hardware health checks

### Phase 3 — Automation Agent (Future)
- Multi-agent architecture (network agent, server agent, switch agent)
- Engineer chatbot interface in plain English
- AI configures switches and servers with human approval gate
- Full audit log of all changes made

---

## Prerequisites

### On the AI Server (Ubuntu VM)

```bash
# Install Python libraries
pip3 install paramiko pywinrm requests --break-system-packages

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull Gemma4 model
ollama pull gemma4:26b

# Install network tools
sudo apt install arp-scan -y
sudo chmod 644 /usr/share/arp-scan/ieee-oui.txt
```

### On Linux target servers

```bash
# Install and enable SSH
sudo apt install openssh-server -y
sudo systemctl enable ssh
sudo systemctl start ssh
```

### On Windows target servers

```powershell
# Enable WinRM (run as Administrator in PowerShell)
Enable-PSRemoting -Force
winrm quickconfig -force
winrm set winrm/config/client '@{TrustedHosts="*"}'
```

---

## Installation

```bash
# Clone the repository
git clone https://github.com/YOURUSERNAME/dc-diagnostic-tool.git
cd dc-diagnostic-tool

# Install dependencies
pip3 install paramiko pywinrm requests --break-system-packages
```

---

## How to Run

SSH into the AI server first:

```bash
ssh harris@192.168.99.142
```

Then run the diagnostic tool:

```bash
python3 ~/diagnostic-tool/diagnostic.py
```

Enter the following when prompted:

```
Enter new server IP address:     [IP of server being deployed]
Enter gateway IP address:        [IP of the network gateway/router]
Enter DNS server hostname or IP: [DNS server IP e.g. 8.8.8.8]
Enter server username:           [SSH username (Linux) or Administrator (Windows)]
Enter server password:           [server password]
```

---

## Example Output

```
==================================================
  DATA CENTRE DEPLOYMENT DIAGNOSTIC TOOL
==================================================

Running network checks...
[1] Checking link to gateway 192.168.99.1...
[2] Checking target server 192.168.99.143...
[3] Checking DNS resolution...
[4] Checking SSH port (Linux)...
[5] Checking WinRM port (Windows)...
[6] Checking HTTP port...
[7] Checking HTTPS port...
[8] Checking for IP conflicts...
  ✗ CONFLICT DETECTED
    IP 192.168.99.143 found on 2 devices:
    Device 1: MAC 00:15:5d:05:00:04 (PRIMARY)
    Device 2: MAC 00:15:5d:05:00:03 (DUPLICATE)
[9] Detecting operating system...
  → Detected: LINUX

==================================================
NETWORK CHECK RESULTS
==================================================
  ✓ Gateway Reachable
  ✓ Server Reachable
  ✓ Dns Working
  ✗ Ssh Open
  ✗ Http Open
  ✗ Https Open
  ✗ IP Conflict Check
  → OS Detected: LINUX

==================================================
AI DIAGNOSIS
==================================================
Priority 1 — IP Conflict:
An IP conflict exists. Two devices share 192.168.99.143.
Fix: sudo ip addr add 192.168.99.144/24 dev eth0 && sudo ip addr del 192.168.99.143/24 dev eth0

Priority 2 — SSH blocked:
SSH port is unreachable due to network instability from the IP conflict.
Fix: Resolve IP conflict first, then verify: sudo ufw allow 22
==================================================
```

---

## Tested Scenarios

| Scenario | Detected | Correct Fix Given |
|---|---|---|
| Firewall blocking SSH | ✅ | ✅ |
| Web server not installed | ✅ | ✅ |
| IP conflict (with MAC IDs) | ✅ | ✅ |
| Missing default gateway | ✅ | ✅ |
| Windows Server — IIS not installed | ✅ | ✅ |
| Windows Server — ICMP blocked | ✅ | ✅ |
| Windows Server — SSH not installed | ✅ | ✅ |
| Multiple problems simultaneously | ✅ | ✅ prioritised |

---

## Security Notes

- WinRM should be restricted to AI server IP only in production
- Use a dedicated read-only service account — not Administrator
- SSH keys are preferred over passwords for Linux targets
- All credentials should be stored in a config file, not hardcoded
- Audit logging to be added in Phase 2

---

## Built By

Harris — Cloud & AI Explorer  
Intern at Cloudify Asia  

---

## License

MIT License — free to use and modify
