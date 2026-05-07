from fastapi import FastAPI, Request, Form, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess, socket, requests, paramiko, winrm, os, sqlite3
from datetime import datetime
from auth import login, verify_session, logout
from database import get_devices, log_action, init_db, add_device, decrypt_password, save_session, load_session, clear_session_data
from agents.intent_classifier import IntentClassifier
from agents.agent_router import AgentRouter
from bmc import get_all_servers_status, get_server_info

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"

init_db()

# Start real time event monitor
from events import start_event_monitor
start_event_monitor()

# ── Cache ────────────────────────────────────────────────────────────
import threading
import time

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # seconds

def get_cache(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["time"]) < CACHE_TTL:
            return entry["data"]
        return None

def set_cache(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "time": time.time()}

def refresh_cache():
    """Staggered background refresh - one thing at a time"""
    while True:
        # Servers every 30 seconds
        try:
            data = get_all_servers_status()
            set_cache("servers", data)
        except: pass
        time.sleep(10)

        # Switch every 40 seconds
        try:
            from switch import get_switch_summary
            data = get_switch_summary()
            set_cache("switch", data)
        except: pass
        time.sleep(10)

        # Topology every 60 seconds
        try:
            from topology import build_topology
            data = build_topology()
            set_cache("topology", data)
        except: pass
        time.sleep(10)

# Pre-warm cache on startup
def warm_cache():
    time.sleep(2)
    try:
        data = get_all_servers_status()
        set_cache("servers", data)
    except: pass
    try:
        from switch import get_switch_summary
        data = get_switch_summary()
        set_cache("switch", data)
    except: pass
    try:
        from topology import build_topology
        data = build_topology()
        set_cache("topology", data)
    except: pass

# Start background cache refresh
cache_thread = threading.Thread(target=refresh_cache, daemon=True)
cache_thread.start()
warm_thread = threading.Thread(target=warm_cache, daemon=True)
warm_thread.start()


# ── Auth helper ──────────────────────────────────────────────────────

def get_user(session_token):
    if not session_token:
        return None
    return verify_session(session_token)

# ── Diagnostic functions ─────────────────────────────────────────────

def check_ping(host):
    try:
        r = subprocess.run(["ping","-c","2","-W","2",host], capture_output=True, text=True)
        return r.returncode == 0
    except: return False

def check_port(host, port):
    try:
        s = socket.socket()
        s.settimeout(3)
        r = s.connect_ex((host, port))
        s.close()
        return r == 0
    except: return False

def check_dns(h):
    try: socket.gethostbyname(h); return True
    except: return False

def check_ip_conflict(ip):
    try:
        r = subprocess.run(["sudo","arp-scan","--localnet"], capture_output=True, text=True)
        matches = []
        for line in r.stdout.split('\n'):
            if ip in line and not line.startswith('WARNING'):
                p = line.split()
                if len(p) >= 2:
                    matches.append({"mac": p[1], "type": "DUPLICATE" if "DUP" in line else "PRIMARY"})
        if len(matches) > 1:
            msg = f"IP {ip} found on {len(matches)} devices: " + " | ".join([f"MAC {d['mac']} ({d['type']})" for d in matches])
            return True, msg
        elif len(matches) == 1:
            return False, f"Unique — MAC {matches[0]['mac']}"
        return False, "Not found on network scan"
    except Exception as e:
        return False, f"Scan failed: {e}"

def detect_os(ip):
    if check_port(ip, 5985): return "windows"
    if check_port(ip, 22): return "linux"
    return "unknown"

def deep_linux(ip, user, pw):
    d = {"os_type":"linux"}
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(ip, username=user, password=pw, timeout=5)
        cmds = {
            "ssh_service":"systemctl is-active ssh 2>/dev/null || echo inactive",
            "http_service":"systemctl is-active nginx 2>/dev/null || echo inactive",
            "firewall":"sudo ufw status 2>/dev/null || echo unknown",
            "disk_usage":"df -h / | tail -1 | awk '{print $5}'",
            "memory_usage":"free -m | awk 'NR==2{printf \"%s/%s MB\",$3,$2}'",
            "os_version":"cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            "ip_address":"ip addr show eth0 | grep 'inet ' | awk '{print $2}'",
            "default_route":"ip route | grep default | awk '{print $3}'"
        }
        for k,cmd in cmds.items():
            _,out,_ = c.exec_command(cmd)
            d[k] = out.read().decode().strip() or "not found"
        c.close()
        d["accessible"] = True
    except Exception as e:
        d["accessible"] = False; d["error"] = str(e)
    return d

def deep_windows(ip, user, pw):
    d = {"os_type":"windows"}
    try:
        s = winrm.Session(ip, auth=(user,pw), transport='ntlm', server_cert_validation='ignore')
        cmds = {
            "os_version":"Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption",
            "ip_address":"(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -ne '127.0.0.1'} | Select-Object -First 1).IPAddress",
            "disk_usage":"$d=Get-PSDrive C;[math]::Round($d.Used/($d.Used+$d.Free)*100),'%' -join ''",
            "memory_usage":"$o=Get-CimInstance Win32_OperatingSystem;\"$([math]::Round(($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/1KB))/$([math]::Round($o.TotalVisibleMemorySize/1KB)) MB\""
        }
        for k,cmd in cmds.items():
            try:
                r = s.run_ps(cmd)
                d[k] = r.std_out.decode().strip() or "not found"
            except: d[k] = "error"
        d["accessible"] = True
    except Exception as e:
        d["accessible"] = False; d["error"] = str(e)
    return d

def run_diagnostic(ip, gw, dns, user, pw):
    results = {
        "gateway_reachable": check_ping(gw),
        "server_reachable": check_ping(ip),
        "dns_working": check_dns(dns),
        "ssh_open": check_port(ip, 22),
        "winrm_open": check_port(ip, 5985),
        "http_open": check_port(ip, 80),
        "https_open": check_port(ip, 443),
    }
    conflict, conflict_info = check_ip_conflict(ip)
    os_type = detect_os(ip)
    if os_type == "linux": details = deep_linux(ip, user, pw)
    elif os_type == "windows": details = deep_windows(ip, user, pw)
    else: details = {"accessible": False, "error": "OS unknown"}
    return results, conflict, conflict_info, os_type, details

def ask_ai(message, session_id=None):
    """Call DeepSeek API directly — reads config from .env"""
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
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

        if not api_key:
            return "API key not configured. Add DEEPSEEK_API_KEY to .env file."

        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": message}
                ],
                "max_tokens": 1000,
                "temperature": 0.1
            },
            timeout=30
        )

        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        else:
            return f"API error {response.status_code}: {response.text[:200]}"

    except requests.Timeout:
        return "Request timed out. Please try again."
    except Exception as e:
        return f"Error: {str(e)}"

# ── Event log helpers ────────────────────────────────────────────────

def get_events(limit=50):
    try:
        DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT username, action, details, timestamp FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        conn.close()
        events = []
        for row in rows:
            action = row[1]
            level = "info"
            if "FAIL" in action or "ERROR" in action or "Critical" in action:
                level = "error"
            elif "WARN" in action or "Warning" in action:
                level = "warning"
            elif "LOGIN" in action or "SUCCESS" in action:
                level = "success"
            events.append({
                "username": row[0],
                "action": action,
                "message": row[2] or action,
                "timestamp": row[3],
                "level": level
            })
        return events
    except Exception as e:
        return []

# ── Routes ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request=request, name="base.html", context={"user": user})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})

@app.post("/login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    token, error = login(username, password)
    if error:
        return RedirectResponse(f"/login?error={error}", status_code=302)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("session_token", token, httponly=True, max_age=28800)
    return response

@app.get("/logout")
async def do_logout(session_token: str = Cookie(None)):
    if session_token:
        clear_session_data(session_token)
        logout(session_token)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session_token")
    return response

@app.get("/api/servers")
async def api_servers(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        cached = get_cache("servers")
        if cached:
            return JSONResponse(cached)
        data = get_all_servers_status()
        set_cache("servers", data)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/switch")
async def api_switch(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        cached = get_cache("switch")
        if cached:
            return JSONResponse(cached)
        from switch import get_switch_summary
        data = get_switch_summary()
        set_cache("switch", data)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/switch/port/{interface:path}")
async def api_port_config(interface: str, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from switch import get_port_raw_config, get_port_details
        raw = get_port_raw_config(interface)
        details = get_port_details(interface)
        return JSONResponse({
            "interface": interface,
            "details": details,
            "raw_config": raw.get("raw_config", ""),
            "full_interface": raw.get("full_interface", interface)
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/topology")
async def api_topology(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        cached = get_cache("topology")
        if cached:
            return JSONResponse(cached)
        from topology import build_topology
        data = build_topology()
        set_cache("topology", data)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/session/history")
async def get_session_history(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"history": []})
    session = load_session(session_token)
    return JSONResponse({"history": session.get("history", [])})

@app.get("/api/events")
async def api_events(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    events = get_events()
    return JSONResponse({"events": events})

@app.get("/api/devices")
async def api_devices(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    devices = get_devices()
    return JSONResponse({"devices": devices})

@app.post("/api/devices")
async def api_add_device(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    try:
        add_device(
            name=data.get("name"),
            device_type=data.get("type"),
            ip=data.get("ip"),
            username=data.get("username"),
            password=data.get("password"),
            brand=data.get("brand", ""),
            model=data.get("model", ""),
            added_by=user
        )
        log_action(user, "ADD_DEVICE", f"Added {data.get('name')} ({data.get('ip')})")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False})

@app.delete("/api/devices/{device_id}")
async def api_delete_device(device_id: int, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        conn.commit()
        conn.close()
        log_action(user, "DELETE_DEVICE", f"Deleted device ID {device_id}")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False})

@app.post("/api/change-password")
async def api_change_password(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    try:
        from database import get_user, verify_password, hash_password
        DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
        user_data = get_user(user)
        if not verify_password(data.get("current"), user_data["password_hash"]):
            return JSONResponse({"error": "Current password incorrect", "success": False})
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                  (hash_password(data.get("new")), user))
        conn.commit()
        conn.close()
        log_action(user, "CHANGE_PASSWORD", "Password changed")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False})

def is_action_request(message, device_names):
    """
    Pure Python check — no LLM. Instant.
    Returns True if message looks like a hardware action.
    Returns False if message looks like conversation.
    """
    msg_lower = message.lower().strip()

    ACTION_SIGNALS = [
        # Switch actions
        "set port", "configure port", "change vlan", "show config",
        "show port", "full config", "trunk", "access vlan", "vlan",
        "show switch", "port config", "interface", "show running",
        "display", "lacp", "stp", "spanning tree",
        # Server actions
        "restart server", "reboot", "power on", "power off", "shutdown",
        "server health", "show server", "bmc", "ilo", "idrac", "redfish",
        "server status", "hardware health", "temperature", "fan",
        # Diagnostic
        "check ", "ping ", "diagnostic", "troubleshoot", "unreachable",
        "cannot connect", "not responding", "down", "offline",
        # OS deployment
        "deploy", "install os", "install ubuntu", "install windows",
        "install sangfor", "raid", "boot order", "iso",
        # General hardware queries
        "how many switches", "how many servers", "list devices",
        "show devices", "what devices", "show topology",
        "event log", "audit log", "recent events",
        # Additional signals for unusual phrasing
        "configure", "config ", "i want you to", "change port",
        "from vlan", "to vlan", "wge", "hge", "ge1/", "eth",
        "port link", "access port", "trunk port",
        "trunk mode", "trunk permit", "link-type", "port trunk",
        "allow vlan", "permit vlan", "trunk vlan",
    ]

    for signal in ACTION_SIGNALS:
        if signal in msg_lower:
            return True

    for name in device_names:
        if name.lower() in msg_lower:
            return True

    return False


@app.post("/chat")
async def chat(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    msg = data.get("message", "").strip()
    session = load_session(session_token)
    pending = session.get("pending_action")

    # ── Handle confirmation for pending actions ──
    if pending and msg.lower() in ["yes", "confirm", "y", "ok", "proceed"]:
        action_type = pending.get("type")

        if action_type == "configure_port":
            interface = pending.get("interface")
            vlan_id = pending.get("vlan_id")
            try:
                from switch import configure_port_vlan
                result = configure_port_vlan(interface, vlan_id)
                session["pending_action"] = None
                if result.get("success"):
                    log_action(user, "SWITCH_CONFIG", f"Port {interface} changed to VLAN {vlan_id}")
                    try:
                        from switch import get_port_details
                        updated = get_port_details(interface)
                        reply = f"Done! Port {interface} has been configured.\n\nUpdated configuration:\nPort: {interface}\nStatus: {updated.get('status', 'Unknown')}\nSpeed: {updated.get('speed', 'Unknown')}\nVLAN: {updated.get('vlan', 'Unknown')}\n\nChange verified successfully."
                    except:
                        reply = f"Done! Port {interface} has been changed to VLAN {vlan_id} successfully."
                else:
                    reply = f"Failed to configure port: {result.get('error', 'Unknown error')}"
            except Exception as e:
                session["pending_action"] = None
                reply = f"Error executing switch config: {str(e)}"
            save_session(session_token, session)
            return JSONResponse({"reply": reply, "step": "idle"})

        elif action_type == "power_action":
            server_key = pending.get("server_key")
            action = pending.get("action")
            try:
                from bmc import power_action
                result = power_action(server_key, action)
                session["pending_action"] = None
                if result.get("success"):
                    log_action(user, "POWER_ACTION", f"Server {server_key} action: {action}")
                    reply = f"Done! {action} executed on {server_key} successfully."
                else:
                    reply = f"Failed: {result.get('error', 'Unknown error')}"
            except Exception as e:
                session["pending_action"] = None
                reply = f"Error: {str(e)}"
            save_session(session_token, session)
            return JSONResponse({"reply": reply, "step": "idle"})

        elif action_type == "switch_llm_exec":
            commands = pending.get("commands", [])
            device_info = pending.get("device", {})
            device_name_pending = device_info.get("name")
            device_ip_pending = device_info.get("ip")

            # pending_action has no password — re-fetch from DB
            all_switches = get_devices(device_type="switch")
            full_device = None
            for d in all_switches:
                if d["name"] == device_name_pending or d["ip"] == device_ip_pending:
                    full_device = d
                    break

            if not full_device:
                session["pending_action"] = None
                save_session(session_token, session)
                return JSONResponse({"reply": f"Device '{device_name_pending}' not found in database.", "step": "idle"})

            try:
                from switch import connect_switch, get_netmiko_device_type
                from netmiko import NetmikoTimeoutException, NetmikoAuthenticationException

                brand = full_device.get("brand", "unknown")
                netmiko_type = get_netmiko_device_type(brand)
                conn = connect_switch(full_device["ip"], full_device["username"], full_device["password"], brand)
                try:
                    output = conn.send_config_set(commands)
                    # Extract interface from commands for targeted verification
                    interface_for_verify = None
                    for cmd in commands:
                        if cmd.strip().lower().startswith("interface "):
                            interface_for_verify = cmd.strip().split(" ", 1)[1]
                            break
                    if interface_for_verify:
                        verify_cmd = (
                            f"display current-configuration interface {interface_for_verify}"
                            if "comware" in netmiko_type
                            else f"show running-config interface {interface_for_verify}"
                        )
                        verification = conn.send_command(verify_cmd, read_timeout=15)
                    else:
                        verification = ""
                finally:
                    conn.disconnect()

                session["pending_action"] = None
                cmd_preview = "\n".join(commands)
                reply = f"Commands executed on **{full_device['name']}**.\n\nCommands sent:\n{cmd_preview}\n\nOutput:\n{output}"
                if verification:
                    reply += f"\n\nVerification:\n{verification}"
                log_action(user, "SWITCH_LLM_EXEC", f"LLM commands on {full_device['name']}")
                try:
                    from memory import record_action
                    device_name = pending.get("device", {}).get("name", "unknown switch")
                    record_action(
                        category="switch_config",
                        summary=f"Executed commands on {device_name}: {', '.join(pending.get('commands', [])[:2])}",
                        device_name=device_name,
                        triggered_by=user
                    )
                except Exception:
                    pass
                save_session(session_token, session)
                return JSONResponse({"reply": reply, "step": "idle"})

            except NetmikoAuthenticationException:
                session["pending_action"] = None
                save_session(session_token, session)
                return JSONResponse({"reply": f"Authentication failed for {full_device['name']}. Check credentials.", "step": "idle"})
            except NetmikoTimeoutException:
                session["pending_action"] = None
                save_session(session_token, session)
                return JSONResponse({"reply": f"Connection timed out to {full_device['name']}.", "step": "idle"})
            except Exception as e:
                session["pending_action"] = None
                save_session(session_token, session)
                return JSONResponse({"reply": f"Error executing commands on {full_device['name']}: {e}", "step": "idle"})

    # ── Handle cancellation ──
    if pending and msg.lower() in ["no", "cancel", "n", "abort"]:
        session["pending_action"] = None
        save_session(session_token, session)
        return JSONResponse({"reply": "Action cancelled. No changes were made.", "step": "idle"})

    # ── Two-gate pipeline ──
    devices = get_devices()
    device_names = [d["name"] for d in devices]

    if is_action_request(msg, device_names):
        # Gate 1 — hardware action: classify then route
        classifier = IntentClassifier()
        intent = classifier.classify(msg, devices)

        if intent.get("clarification_needed"):
            result = {"reply": intent["clarification_question"], "step": "idle"}
        else:
            intent.setdefault("parameters", {})["original_message"] = msg
            router = AgentRouter()
            result = router.route(intent, session)
            if "pending_action" in result:
                session["pending_action"] = result["pending_action"]
    else:
        # Gate 2 — conversation: skip classifier, go straight to GeneralAgent
        from agents.general_agent import GeneralAgent
        agent = GeneralAgent()
        intent = {
            "intent": "general_question",
            "device_type": None,
            "device_name": None,
            "parameters": {"original_message": msg},
            "confidence": "high",
            "clarification_needed": False,
            "clarification_question": None,
        }
        result = agent.handle(intent, session, msg)

    session["history"].append({"user": msg, "reply": result.get("reply", "")})
    if len(session["history"]) > 20:
        session["history"] = session["history"][-20:]
    save_session(session_token, session)
    log_action(user, "CHAT", f"Message: {msg[:50]}")
    return JSONResponse({
        "reply": result.get("reply", ""),
        "action": result.get("action"),
        "config": result.get("config"),
        "step": result.get("step", "idle")
    })

@app.post("/run_diagnostic")
async def run_diag(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    ip = data.get("target_ip"); gw = data.get("gateway_ip")
    dns = data.get("dns_server","8.8.8.8"); usr = data.get("username"); pw = data.get("password")
    results, conflict, conflict_info, os_type, details = run_diagnostic(ip, gw, dns, usr, pw)
    failed = [k for k,v in results.items() if not v]
    passed = [k for k,v in results.items() if v]
    detail_str = ""
    if details.get("accessible"):
        detail_str = f"\nDeep scan ({os_type.upper()}):\n"
        for k,v in details.items():
            if k not in ["accessible","os_type","error"]:
                detail_str += f"- {k.replace('_',' ').title()}: {v}\n"
    else:
        detail_str = f"\nCould not access: {details.get('error','unknown')}"
    prompt = f"""You are a network engineer. Server at {ip} ({os_type.upper()}).
PASSED: {', '.join(passed) if passed else 'None'}
FAILED: {', '.join(failed) if failed else 'None'}
IP conflict: {conflict_info}
{detail_str}
Give SHORT summary (3-4 sentences) of what is wrong and the most important fix."""
    reply = ask_ai(prompt)
    return JSONResponse({
        "reply": reply, "results": results,
        "conflict": conflict, "conflict_info": conflict_info,
        "os_type": os_type,
        "details": {k:v for k,v in details.items() if k not in ["accessible","error"]},
        "accessible": details.get("accessible", False),
        "show_report_btn": True,
        "target_ip": ip, "gateway_ip": gw, "dns_server": dns, "username": usr, "password": pw
    })

@app.get("/report", response_class=HTMLResponse)
async def report(request: Request, target_ip: str = "", gateway_ip: str = "",
                 dns_server: str = "8.8.8.8", username: str = "", password: str = "",
                 session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request=request, name="report.html", context={"target_ip": target_ip, "gateway_ip": gateway_ip, "dns_server": dns_server, "username": username, "password": password})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
