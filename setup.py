#!/usr/bin/env python3
"""
NexDeploy AI — First Time Setup Script
Run this once when setting up on a new machine.
"""

import os
import sys
import subprocess
import shutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME_DIR = os.path.expanduser("~")
SKILL_DIR = os.path.join(HOME_DIR, ".hermes", "skills", "nexdeploy", "nexdeploy-dc")
SKILL_FILE = os.path.join(SKILL_DIR, "SKILL.md")

def print_step(step, message):
    print(f"\n[{step}] {message}")

def print_ok(message):
    print(f"  ✓ {message}")

def print_fail(message):
    print(f"  ✗ {message}")

def print_warn(message):
    print(f"  ⚠ {message}")

def check_python():
    print_step(1, "Checking Python version...")
    version = sys.version_info
    if version.major == 3 and version.minor >= 10:
        print_ok(f"Python {version.major}.{version.minor} — OK")
        return True
    else:
        print_fail(f"Python {version.major}.{version.minor} — need 3.10+")
        return False

def install_dependencies():
    print_step(2, "Installing Python dependencies...")
    req_file = os.path.join(PROJECT_DIR, "requirements.txt")
    if not os.path.exists(req_file):
        print_fail("requirements.txt not found")
        return False
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req_file, "--break-system-packages", "-q"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print_ok("All dependencies installed")
            return True
        else:
            print_warn("Some dependencies may have failed — check manually")
            print(result.stderr[:200])
            return True
    except Exception as e:
        print_fail(f"Failed: {e}")
        return False

def check_arp_scan():
    print_step(3, "Checking arp-scan...")
    if shutil.which("arp-scan"):
        print_ok("arp-scan found")
    else:
        print_warn("arp-scan not found — installing...")
        os.system("sudo apt install arp-scan -y > /dev/null 2>&1")
        if shutil.which("arp-scan"):
            print_ok("arp-scan installed")
        else:
            print_warn("arp-scan could not be installed — network discovery may not work")
    return True

def setup_database():
    print_step(4, "Setting up database...")
    try:
        sys.path.insert(0, PROJECT_DIR)
        from database import init_db, is_first_run, create_user
        init_db()
        if is_first_run():
            print_warn("First run detected — creating admin account...")
            create_user("admin", "admin123", "admin")
            print_ok("Admin account created: admin / admin123")
            print_warn("IMPORTANT: Change this password after first login!")
        else:
            print_ok("Database already exists — skipping")
        return True
    except Exception as e:
        print_fail(f"Database setup failed: {e}")
        return False

def fix_app_path():
    print_step(5, "Fixing app.py paths...")
    app_file = os.path.join(PROJECT_DIR, "app.py")
    try:
        with open(app_file, 'r') as f:
            content = f.read()

        # Fix hardcoded path in ask_hermes
        if '"/home/harris/diagnostic-tool"' in content:
            content = content.replace(
                'cwd="/home/harris/diagnostic-tool"',
                f'cwd=os.path.dirname(os.path.abspath(__file__))'
            )
            with open(app_file, 'w') as f:
                f.write(content)
            print_ok("Fixed hardcoded path in app.py")
        else:
            print_ok("app.py paths already correct")
        return True
    except Exception as e:
        print_fail(f"Failed to fix app.py: {e}")
        return False

def setup_hermes_skill():
    print_step(6, "Setting up Hermes NexDeploy skill...")

    # Check if hermes is installed
    if not shutil.which("hermes"):
        print_warn("Hermes not installed — skipping skill setup")
        print_warn("Install Hermes first: curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash")
        return False

    # Create skill directory
    os.makedirs(SKILL_DIR, exist_ok=True)

    # Write skill file with correct paths
    skill_content = f"""name: nexdeploy-dc
description: NexDeploy AI data centre assistant skill. Use when engineer asks about servers, switches, network devices, health status, diagnostics, or deployment tasks.
version: 1.0.0
metadata:
  hermes:
    tags: [datacenter, servers, network, ilo, switch, deployment, diagnostic]
---

# NexDeploy DC Assistant

You are NexDeploy AI — an intelligent data centre assistant.
You have direct access to live infrastructure data through command line tools.
The project is located at: {PROJECT_DIR}

## Your Capabilities

### 1. Check Server Health
```bash
cd {PROJECT_DIR} && python3 -c "from ilo import get_all_servers_status; import json; print(json.dumps(get_all_servers_status(), indent=2))"
```

### 2. Check Switch Port Status
```bash
cd {PROJECT_DIR} && python3 -c "from switch import get_switch_summary; import json; print(json.dumps(get_switch_summary(), indent=2))"
```

### 3. Get Network Topology
```bash
cd {PROJECT_DIR} && python3 -c "from topology import build_topology; data = build_topology(); [print(f\\"{{n['name']}} | {{n['ip']}} | {{n['type']}}\\" ) for n in data['nodes']]"
```

### 4. Get All Registered Devices
```bash
cd {PROJECT_DIR} && python3 -c "from database import get_devices; [print(f\\"{{d['name']}} | {{d['ip']}} | {{d['type']}} | {{d['brand']}}\\" ) for d in get_devices()]"
```

### 5. Run Network Diagnostic
Replace SERVER_IP with actual IP:
```bash
cd {PROJECT_DIR} && python3 -c "
import subprocess, socket
ip = 'SERVER_IP'
ping = subprocess.run(['ping','-c','2','-W','2',ip], capture_output=True)
print('Ping:', 'OK' if ping.returncode == 0 else 'FAIL')
for port, name in [(22,'SSH'),(5985,'WinRM'),(80,'HTTP'),(443,'HTTPS')]:
    s = socket.socket()
    s.settimeout(2)
    r = s.connect_ex((ip, port))
    s.close()
    print(f'{{name}}:', 'OPEN' if r == 0 else 'CLOSED')
"
```

### 6. Check Recent Events
```bash
cd {PROJECT_DIR} && python3 -c "
import sqlite3, os
conn = sqlite3.connect(os.path.join('{PROJECT_DIR}', 'nexdeploy.db'))
c = conn.cursor()
c.execute('SELECT username, action, details, timestamp FROM audit_log ORDER BY timestamp DESC LIMIT 20')
for row in c.fetchall():
    print(f'[{{row[3]}}] {{row[0]}} — {{row[1]}}: {{row[2]}}')
conn.close()
"
```

### 7. Power Control — REQUIRES APPROVAL
Always ask engineer to confirm before executing.
```bash
cd {PROJECT_DIR} && python3 -c "from ilo import power_action; print(power_action('server1', 'ACTION'))"
```
Allowed: On, ForceOff, GracefulShutdown, GracefulRestart

## Approval Gate Rules

ALWAYS ask confirmation before:
- Powering off or restarting any server
- Changing switch configuration
- Modifying network settings
- Deleting anything

NEVER ask confirmation for:
- Reading health data
- Running diagnostic checks
- Generating reports

## Response Format

- Be concise and practical
- Run commands to get real data before answering
- Use actual values from output never guess
- Report errors clearly if command fails
- For health issues explain what is wrong and suggest fix

## Examples

Engineer: what is the health of our servers
Action: run server health command, report actual values

Engineer: how many ports are up
Action: run switch command, report actual count

Engineer: restart server 1
Action: say I plan to GracefulRestart [server name], confirm yes or no, wait, execute only if yes

Engineer: what can you do or what access do you have
Action: explain capabilities without running commands — server health, switch status, topology, diagnostics, power control with approval
"""

    with open(SKILL_FILE, 'w') as f:
        f.write(skill_content)

    print_ok(f"Skill file created at {SKILL_FILE}")
    print_ok(f"Paths automatically set to {PROJECT_DIR}")
    return True

def fix_gitignore():
    print_step(7, "Checking .gitignore...")
    gitignore = os.path.join(PROJECT_DIR, ".gitignore")
    required = ["nexdeploy.db", ".secret_key", "*.env", "__pycache__/", "*.pyc"]
    try:
        existing = open(gitignore).read() if os.path.exists(gitignore) else ""
        missing = [r for r in required if r not in existing]
        if missing:
            with open(gitignore, 'a') as f:
                f.write('\n'.join(missing) + '\n')
            print_ok(f"Added to .gitignore: {', '.join(missing)}")
        else:
            print_ok(".gitignore already correct")
    except Exception as e:
        print_fail(f"Failed: {e}")
    return True


def setup_env():
    print_step(8, "Setting up API configuration...")
    env_path = os.path.join(PROJECT_DIR, ".env")
    
    if os.path.exists(env_path):
        print_ok(".env file already exists — skipping")
        return True
    
    print("  DeepSeek API configuration needed.")
    print("  Press Enter to use defaults or type your values.\n")
    
    api_key = input("  DEEPSEEK_API_KEY: ").strip()
    base_url = input("  DEEPSEEK_BASE_URL (default: https://api.deepseek.com/v1): ").strip()
    model = input("  DEEPSEEK_MODEL (default: deepseek-chat): ").strip()
    
    if not base_url:
        base_url = "https://api.deepseek.com/v1"
    if not model:
        model = "deepseek-chat"
    
    if api_key:
        with open(env_path, 'w') as f:
            f.write(f"DEEPSEEK_API_KEY={api_key}\n")
            f.write(f"DEEPSEEK_BASE_URL={base_url}\n")
            f.write(f"DEEPSEEK_MODEL={model}\n")
        print_ok(".env file created successfully")
    else:
        print_warn("No API key provided — chatbot will not work until .env is configured")
    return True


def print_summary():
    print("\n" + "="*60)
    print("NexDeploy AI — Setup Complete!")
    print("="*60)
    print(f"\nProject directory: {PROJECT_DIR}")
    print(f"\nTo start the system:")
    print(f"  cd {PROJECT_DIR}")
    print(f"  python3 app.py")
    print(f"\nThen open: http://localhost:8000")
    print(f"\nDefault login:")
    print(f"  Username: admin")
    print(f"  Password: admin123")
    print(f"\nIMPORTANT:")
    print(f"  1. Change admin password after first login")
    print(f"  2. Add your devices via Settings page")
    print(f"  3. Configure Hermes with your AI API key: hermes model")
    print("="*60)

def main():
    print("="*60)
    print("NexDeploy AI — Setup Wizard")
    print("="*60)

    steps = [
        check_python,
        install_dependencies,
        check_arp_scan,
        setup_database,
        fix_app_path,
        setup_hermes_skill,
        fix_gitignore,
        setup_env,
    ]

    failed = []
    for step in steps:
        if not step():
            failed.append(step.__name__)

    print_summary()

    if failed:
        print(f"\nWarnings — these steps had issues: {', '.join(failed)}")
        print("System may still work — check manually if needed.")

if __name__ == "__main__":
    main()