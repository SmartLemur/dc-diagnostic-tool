from fastapi import FastAPI, Request, Form, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import subprocess, socket, requests, paramiko, winrm, re, os, json
from auth import login, verify_session, logout
from database import get_devices, log_action, init_db
from ilo import get_all_servers_status

app = FastAPI()
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"
sessions = {}
init_db()

def get_current_user(session_token=None):
    if not session_token: return None
    return verify_session(session_token)

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

def ask_ai(prompt):
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "messages": [{"role":"user","content":prompt}],
            "stream": False, "think": False,
            "options": {"temperature":0.1,"num_predict":500,"num_ctx":4096}
        }, timeout=120)
        if r.status_code == 200:
            return r.json().get("message",{}).get("content","").strip() or "No response"
        return f"API error {r.status_code}"
    except Exception as e:
        return f"Error: {e}"

def extract_ip(text):
    m = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
    return m[0] if m else None

@app.get("/", response_class=HTMLResponse)
async def index(session_token: str = Cookie(None)):
    user = get_current_user(session_token)
    if not user: return RedirectResponse("/login")
    return HTMLResponse(open("/home/harris/diagnostic-tool/dashboard.html").read())

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    return HTMLResponse(get_login_html(error))

@app.post("/login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    token, error = login(username, password)
    if error: return RedirectResponse(f"/login?error={error}", status_code=302)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("session_token", token, httponly=True, max_age=28800)
    return response

@app.get("/logout")
async def do_logout(session_token: str = Cookie(None)):
    if session_token: logout(session_token)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session_token")
    return response

@app.get("/api/servers")
async def api_servers():
    try:
        data = get_all_servers_status()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/chat")
async def chat(request: Request, session_token: str = Cookie(None)):
    user = get_current_user(session_token)
    if not user: return JSONResponse({"error":"Unauthorized"}, status_code=401)
    data = await request.json()
    sid = data.get("session_id", user)
    msg = data.get("message","").strip()
    if sid not in sessions: sessions[sid] = {"step":"idle","data":{}}
    s = sessions[sid]; step = s["step"]; sd = s["data"]

    if step == "idle":
        ip = extract_ip(msg)
        if ip or any(w in msg.lower() for w in ["check","scan","diagnose","server"]):
            if ip:
                sd["target_ip"] = ip; s["step"] = "ask_gateway"
                return JSONResponse({"reply": f"Got it — checking server **{ip}**.\n\nWhat is the **gateway IP**?", "step":"ask_gateway"})
            else:
                s["step"] = "ask_ip"
                return JSONResponse({"reply":"What is the **IP address** of the server?","step":"ask_ip"})
        else:
            reply = ask_ai(f"You are a helpful data centre assistant for Cloudify Asia. Answer briefly: {msg}")
            return JSONResponse({"reply":reply,"step":"idle"})

    elif step == "ask_ip":
        ip = extract_ip(msg)
        if ip:
            sd["target_ip"] = ip; s["step"] = "ask_gateway"
            return JSONResponse({"reply":f"Server IP: **{ip}**\n\nWhat is the **gateway IP**?","step":"ask_gateway"})
        return JSONResponse({"reply":"Please enter a valid IP e.g. **192.168.99.143**","step":"ask_ip"})

    elif step == "ask_gateway":
        ip = extract_ip(msg)
        if ip:
            sd["gateway_ip"] = ip; s["step"] = "ask_dns"
            return JSONResponse({"reply":f"Gateway: **{ip}**\n\nDNS server? (type **skip** for 8.8.8.8)","step":"ask_dns"})
        return JSONResponse({"reply":"Please enter a valid gateway IP.","step":"ask_gateway"})

    elif step == "ask_dns":
        ip = extract_ip(msg)
        sd["dns_server"] = ip if ip else "8.8.8.8"; s["step"] = "ask_username"
        return JSONResponse({"reply":f"DNS: **{sd['dns_server']}**\n\nServer **username**?","step":"ask_username"})

    elif step == "ask_username":
        sd["username"] = msg; s["step"] = "ask_password"
        return JSONResponse({"reply":"Server **password**?","step":"ask_password"})

    elif step == "ask_password":
        sd["password"] = msg; s["step"] = "idle"
        log_action(user, "DIAGNOSTIC", f"Running diagnostic on {sd.get('target_ip')}")
        return JSONResponse({"reply":f"Running diagnostic on **{sd['target_ip']}**... please wait","step":"running","run_diagnostic":True,"data":sd})

    return JSONResponse({"reply":"Type **check [IP]** to start.","step":"idle"})

@app.post("/run_diagnostic")
async def run_diag(request: Request, session_token: str = Cookie(None)):
    user = get_current_user(session_token)
    if not user: return JSONResponse({"error":"Unauthorized"}, status_code=401)
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
async def report(target_ip: str = "", gateway_ip: str = "", dns_server: str = "8.8.8.8",
                 username: str = "", password: str = "", session_token: str = Cookie(None)):
    user = get_current_user(session_token)
    if not user: return RedirectResponse("/login")
    return HTMLResponse(get_report_html(target_ip, gateway_ip, dns_server, username, password))

def get_login_html(error=""):
    error_html = f'<div class="error">{error}</div>' if error else ""
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>NexDeploy AI</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#1a1d2e;border:1px solid #2d3748;border-radius:16px;padding:2.5rem;width:380px}
.logo{text-align:center;margin-bottom:2rem}
.logo h1{font-size:1.6rem;font-weight:700;color:#fff}
.logo p{font-size:0.85rem;color:#718096;margin-top:0.25rem}
.form-group{margin-bottom:1rem}
label{font-size:0.8rem;color:#718096;display:block;margin-bottom:0.4rem}
input{width:100%;background:#0f1117;border:1px solid #2d3748;border-radius:8px;padding:0.7rem;color:#e2e8f0;font-size:0.9rem;outline:none}
input:focus{border-color:#3182ce}
.btn{width:100%;background:#3182ce;color:white;border:none;padding:0.75rem;border-radius:8px;font-size:0.95rem;font-weight:600;cursor:pointer;margin-top:0.5rem}
.btn:hover{background:#2b6cb0}
.error{background:#742a2a;color:#fc8181;padding:0.75rem;border-radius:8px;font-size:0.85rem;margin-bottom:1rem;text-align:center}
</style></head>
<body>
<div class="card">
  <div class="logo">
    <h1>NexDeploy AI</h1>
    <p>Intelligent Server Deployment Assistant</p>
    <p style="color:#4a5568;font-size:0.75rem;margin-top:0.5rem">Cloudify Asia</p>
  </div>
  """ + error_html + """
  <form method="post" action="/login">
    <div class="form-group"><label>Username</label>
      <input type="text" name="username" placeholder="Enter username" required autofocus></div>
    <div class="form-group"><label>Password</label>
      <input type="password" name="password" placeholder="Enter password" required></div>
    <button type="submit" class="btn">Sign In</button>
  </form>
  <div style="text-align:center;color:#4a5568;font-size:0.75rem;margin-top:1.5rem">NexDeploy AI v1.0</div>
</div>
</body></html>"""

def get_dashboard_html(user):
    return """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<title>NexDeploy AI</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}
.header{background:#1a1d2e;border-bottom:1px solid #2d3748;padding:0.75rem 1.5rem;display:flex;align-items:center;flex-shrink:0}
.header h1{font-size:1.1rem;font-weight:700;color:#fff}
.header p{font-size:0.8rem;color:#718096;margin-top:0.1rem}
.user-info{margin-left:auto;display:flex;align-items:center;gap:1rem}
.user-badge{background:#2d3748;padding:0.3rem 0.75rem;border-radius:20px;font-size:0.8rem;color:#a0aec0}
.logout-btn{background:none;border:1px solid #2d3748;color:#718096;padding:0.3rem 0.75rem;border-radius:6px;cursor:pointer;font-size:0.8rem}
.main{display:flex;flex:1;overflow:hidden}
.dashboard{flex:1;overflow-y:auto;padding:1.5rem}
.sidebar{width:360px;background:#1a1d2e;border-left:1px solid #2d3748;display:flex;flex-direction:column;flex-shrink:0}
.section-label{font-size:0.75rem;font-weight:600;color:#718096;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.75rem}
.server-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem}
.server-card{background:#1a1d2e;border:1px solid #2d3748;border-radius:10px;padding:1rem}
.server-card.ok{border-left:3px solid #48bb78}
.server-card.warning{border-left:3px solid #ed8936}
.server-card.critical{border-left:3px solid #fc8181}
.server-name{font-size:0.95rem;font-weight:600;color:#fff;margin-bottom:0.2rem}
.server-model{font-size:0.75rem;color:#718096;margin-bottom:0.5rem}
.power-badge{display:inline-block;padding:0.15rem 0.5rem;border-radius:4px;font-size:0.7rem;font-weight:600}
.power-on{background:#276749;color:#9ae6b4}
.power-off{background:#742a2a;color:#fc8181}
.health-grid{display:flex;flex-wrap:wrap;gap:0.3rem;margin-top:0.5rem}
.hb{font-size:0.65rem;padding:0.15rem 0.4rem;border-radius:3px;font-weight:600}
.hb-ok{background:#276749;color:#9ae6b4}
.hb-warn{background:#744210;color:#f6ad55}
.hb-crit{background:#742a2a;color:#fc8181}
.hb-unk{background:#2d3748;color:#718096}
.loading-box{background:#1a1d2e;border:1px solid #2d3748;border-radius:10px;padding:2rem;text-align:center;color:#718096;grid-column:span 2}
.spinner{width:24px;height:24px;border:2px solid #2d3748;border-top-color:#3182ce;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 0.5rem}
@keyframes spin{to{transform:rotate(360deg)}}
.refresh-btn{background:none;border:1px solid #2d3748;color:#718096;padding:0.3rem 0.75rem;border-radius:6px;cursor:pointer;font-size:0.78rem}
.chat-header{padding:1rem 1.25rem;border-bottom:1px solid #2d3748;display:flex;align-items:center;gap:0.75rem;flex-shrink:0}
.chat-avatar{width:32px;height:32px;border-radius:50%;background:#3182ce;display:flex;align-items:center;justify-content:center;font-weight:700;color:white;font-size:0.8rem;flex-shrink:0}
.chat-title{font-size:0.9rem;font-weight:600}
.chat-sub{font-size:0.7rem;color:#48bb78}
.messages{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:0.75rem}
.msg{max-width:85%;padding:0.6rem 0.85rem;border-radius:10px;font-size:0.82rem;line-height:1.5}
.msg.ai{background:#2d3748;color:#e2e8f0;align-self:flex-start;border-bottom-left-radius:3px}
.msg.user{background:#3182ce;color:white;align-self:flex-end;border-bottom-right-radius:3px}
.msg.thinking{background:#2d3748;color:#718096;align-self:flex-start;font-style:italic}
.report-btn{display:block;margin-top:0.5rem;padding:0.4rem 0.75rem;background:#276749;color:#9ae6b4;border-radius:6px;font-size:0.78rem;font-weight:600;cursor:pointer;border:none;width:100%;text-align:center}
.input-area{padding:0.75rem;border-top:1px solid #2d3748;display:flex;gap:0.5rem;flex-shrink:0}
.msg-input{flex:1;background:#0f1117;border:1px solid #2d3748;border-radius:8px;padding:0.6rem 0.8rem;color:#e2e8f0;font-size:0.82rem;outline:none}
.msg-input:focus{border-color:#3182ce}
.send-btn{background:#3182ce;border:none;border-radius:8px;width:36px;height:36px;cursor:pointer;color:white;font-size:1rem;flex-shrink:0}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>NexDeploy AI</h1>
    <p>Intelligent Server Deployment Assistant — Cloudify Asia</p>
  </div>
  <div class="user-info">
    <span class="user-badge">👤 """ + user + """</span>
    <button class="logout-btn" onclick="window.location='/logout'">Sign Out</button>
  </div>
</div>
<div class="main">
  <div class="dashboard">
    <div style="display:flex;align-items:center;margin-bottom:1rem">
      <span class="section-label" style="margin:0">Server Hardware Health</span>
      <button class="refresh-btn" style="margin-left:auto" onclick="loadServers()">↻ Refresh</button>
    </div>
    <div class="server-grid" id="serverGrid">
      <div class="loading-box"><div class="spinner"></div>Loading servers...</div>
    </div>
    <div class="section-label">Network Diagnostic</div>
    <div style="background:#1a1d2e;border:1px solid #2d3748;border-radius:10px;padding:1rem;color:#718096;font-size:0.85rem">
      Use the chat on the right — type <strong style="color:#e2e8f0">check [server IP]</strong> to run a diagnostic.
    </div>
  </div>
  <div class="sidebar">
    <div class="chat-header">
      <div class="chat-avatar">AI</div>
      <div>
        <div class="chat-title">NexDeploy Assistant</div>
        <div class="chat-sub">● Online</div>
      </div>
    </div>
    <div class="messages" id="messages">
      <div class="msg ai">Hi """ + user + """! I am your NexDeploy assistant.<br><br>
      Type <strong>check [IP]</strong> to run a diagnostic on any server.</div>
    </div>
    <div class="input-area">
      <input class="msg-input" id="msgInput" type="text" placeholder="Type a message..."
             onkeydown="if(event.key==='Enter')send()">
      <button class="send-btn" onclick="send()">&#10148;</button>
    </div>
  </div>
</div>
<script>
var sid = Math.random().toString(36).substr(2,9);

function getHealthClass(h) {
  if (h === 'OK') return 'hb-ok';
  if (h === 'Warning') return 'hb-warn';
  if (h === 'Critical') return 'hb-crit';
  return 'hb-unk';
}

function getCardClass(h) {
  if (h === 'OK') return 'ok';
  if (h === 'Warning') return 'warning';
  return 'critical';
}

async function loadServers() {
  var grid = document.getElementById('serverGrid');
  grid.innerHTML = '<div class="loading-box"><div class="spinner"></div>Loading...</div>';
  try {
    var res = await fetch('/api/servers');
    var data = await res.json();
    var html = '';
    var keys = Object.keys(data);
    if (keys.length === 0) {
      html = '<div class="loading-box">No servers found</div>';
    } else {
      for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        var s = data[key];
        if (s.error) {
          html += '<div class="server-card"><div class="server-name">' + key + '</div><div class="server-model" style="color:#fc8181">Error: ' + s.error + '</div></div>';
          continue;
        }
        var cardClass = getCardClass(s.overall_health);
        var powerClass = s.power_state === 'On' ? 'power-on' : 'power-off';
        var badges = '';
        var hd = s.health_details || {};
        var hdKeys = Object.keys(hd);
        for (var j = 0; j < hdKeys.length; j++) {
          var hk = hdKeys[j];
          var hv = hd[hk];
          badges += '<span class="hb ' + getHealthClass(hv) + '">' + hk.replace('_',' ') + ': ' + hv + '</span>';
        }
        html += '<div class="server-card ' + cardClass + '">' +
          '<div class="server-name">' + (s.name || key) + '</div>' +
          '<div class="server-model">' + (s.model || '') + ' &middot; ' + s.ram_gb + 'GB RAM</div>' +
          '<span class="power-badge ' + powerClass + '">' + s.power_state + '</span>' +
          '<div class="health-grid">' + badges + '</div>' +
          '</div>';
      }
    }
    grid.innerHTML = html;
  } catch(e) {
    grid.innerHTML = '<div class="loading-box">Failed to load: ' + e.message + '</div>';
  }
}

function addMsg(text, type) {
  var box = document.getElementById('messages');
  var d = document.createElement('div');
  d.className = 'msg ' + type;
  var t = text;
  while (t.indexOf('**') !== -1) {
    t = t.replace('**', '<strong>');
    t = t.replace('**', '</strong>');
  }
  t = t.split('\n').join('<br>');
  d.innerHTML = t;
  box.appendChild(d);
  box.scrollTop = box.scrollHeight;
  return d;
}

function addReportBtn(data) {
  var box = document.getElementById('messages');
  var btn = document.createElement('button');
  btn.className = 'report-btn';
  btn.textContent = 'View Full Report';
  btn.onclick = function() {
    var p = new URLSearchParams({
      target_ip: data.target_ip || '',
      gateway_ip: data.gateway_ip || '',
      dns_server: data.dns_server || '8.8.8.8',
      username: data.username || '',
      password: data.password || ''
    });
    window.open('/report?' + p.toString(), '_blank');
  };
  box.appendChild(btn);
  box.scrollTop = box.scrollHeight;
}

async function send() {
  var input = document.getElementById('msgInput');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  addMsg(msg, 'user');
  var thinking = addMsg('Thinking...', 'thinking');
  try {
    var res = await fetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg, session_id: sid})
    });
    var data = await res.json();
    thinking.remove();
    addMsg(data.reply, 'ai');
    if (data.run_diagnostic && data.data) {
      var running = addMsg('Running checks... please wait (30-60 seconds)', 'thinking');
      var res2 = await fetch('/run_diagnostic', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(Object.assign({}, data.data, {session_id: sid}))
      });
      var d2 = await res2.json();
      running.remove();
      addMsg(d2.reply, 'ai');
      if (d2.show_report_btn) addReportBtn(d2);
    }
  } catch(e) {
    thinking.remove();
    addMsg('Connection error. Please try again.', 'ai');
  }
}

loadServers();
setInterval(loadServers, 30000);
</script>
</body></html>"""

def get_report_html(target_ip, gateway_ip, dns_server, username, password):
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Report</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}
.header{background:#1a1d2e;border-bottom:1px solid #2d3748;padding:1rem 2rem;display:flex;align-items:center;gap:1rem}
.header h1{font-size:1rem;font-weight:600}
.back{background:none;border:1px solid #2d3748;color:#a0aec0;padding:0.4rem 0.9rem;border-radius:6px;cursor:pointer;font-size:0.85rem}
.container{max-width:900px;margin:2rem auto;padding:0 1.5rem}
.card{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem}
.card h2{font-size:0.8rem;font-weight:600;color:#a0aec0;margin-bottom:1rem;text-transform:uppercase;letter-spacing:0.05em}
.spinner{width:28px;height:28px;border:2px solid #2d3748;border-top-color:#3182ce;border-radius:50%;animation:spin 0.8s linear infinite;margin:1rem auto}
@keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body>
<div class="header">
  <button class="back" onclick="window.close()">Close</button>
  <h1>Full Diagnostic Report — """ + target_ip + """</h1>
</div>
<div class="container">
  <div class="card" id="content">
    <div style="text-align:center;padding:2rem;color:#718096">
      <div class="spinner"></div>Running diagnostic on """ + target_ip + """...
    </div>
  </div>
</div>
<script>
var labels = {
  gateway_reachable:'Gateway reachable',server_reachable:'Server reachable',
  dns_working:'DNS resolution',ssh_open:'SSH port 22',winrm_open:'WinRM port 5985',
  http_open:'HTTP port 80',https_open:'HTTPS port 443'
};
fetch('/run_diagnostic', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    target_ip: '""" + target_ip + """',
    gateway_ip: '""" + gateway_ip + """',
    dns_server: '""" + dns_server + """',
    username: '""" + username + """',
    password: '""" + password + """',
    session_id: 'report'
  })
}).then(function(r){ return r.json(); }).then(function(data) {
  var checks = '';
  var rkeys = Object.keys(data.results || {});
  for (var i = 0; i < rkeys.length; i++) {
    var k = rkeys[i];
    var v = data.results[k];
    checks += '<div style="display:flex;align-items:center;gap:.75rem;padding:.6rem 0;border-bottom:1px solid #2d3748">' +
      '<span style="color:' + (v ? '#48bb78' : '#fc8181') + ';font-weight:700">' + (v ? 'OK' : 'FAIL') + '</span>' +
      '<span style="font-size:.9rem">' + (labels[k] || k) + '</span></div>';
  }
  var deep = '';
  var dkeys = Object.keys(data.details || {});
  for (var j = 0; j < dkeys.length; j++) {
    var dk = dkeys[j];
    if (dk === 'os_type') continue;
    deep += '<div style="background:#0f1117;border-radius:6px;padding:.5rem .75rem">' +
      '<div style="font-size:.75rem;color:#718096">' + dk.replace(/_/g,' ') + '</div>' +
      '<div style="font-size:.85rem;word-break:break-all;margin-top:.1rem">' + data.details[dk] + '</div></div>';
  }
  var conflict = data.conflict ?
    '<div style="background:#742a2a;border:1px solid #fc8181;border-radius:8px;padding:.75rem 1rem;margin-bottom:1rem;color:#fc8181">IP Conflict: ' + data.conflict_info + '</div>' : '';
  document.getElementById('content').outerHTML =
    conflict +
    '<div class="card"><h2>Network Checks <span style="background:#2b4c7e;color:#90cdf4;padding:.2rem .6rem;border-radius:4px;font-size:.75rem;margin-left:.5rem">' + (data.os_type || '').toUpperCase() + '</span></h2>' + checks + '</div>' +
    '<div class="card"><h2>Deep Scan</h2><div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem">' + deep + '</div></div>' +
    '<div class="card"><h2>AI Diagnosis</h2><div style="background:#0f1117;border-radius:8px;padding:1rem;font-size:.88rem;line-height:1.7;white-space:pre-wrap">' + data.reply + '</div></div>';
});
</script></body></html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
