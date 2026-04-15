from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import subprocess, socket, requests, paramiko, winrm, re, os

app = FastAPI()

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"
sessions = {}

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
            "http_service":"systemctl is-active nginx 2>/dev/null || systemctl is-active apache2 2>/dev/null || echo inactive",
            "firewall":"sudo ufw status 2>/dev/null || echo unknown",
            "disk_usage":"df -h / | tail -1 | awk '{print $5}'",
            "memory_usage":"free -m | awk 'NR==2{printf \"%s/%s MB\",$3,$2}'",
            "cpu_load":"uptime | awk -F'load average:' '{print $2}' | xargs",
            "os_version":"cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            "ip_address":"ip addr show eth0 | grep 'inet ' | awk '{print $2}'",
            "default_route":"ip route | grep default | awk '{print $3}'",
            "open_ports":"ss -tlnp | grep LISTEN | awk '{print $4}' | tr '\n' ' '"
        }
        for k,cmd in cmds.items():
            _,out,_ = c.exec_command(cmd)
            d[k] = out.read().decode().strip() or "not found"
        c.close()
        d["accessible"] = True
    except paramiko.AuthenticationException:
        d["accessible"] = False; d["error"] = "Authentication failed"
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
            "default_route":"(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).NextHop",
            "disk_usage":"$d=Get-PSDrive C;[math]::Round($d.Used/($d.Used+$d.Free)*100),'%' -join ''",
            "memory_usage":"$o=Get-CimInstance Win32_OperatingSystem;\"$([math]::Round(($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/1KB))/$([math]::Round($o.TotalVisibleMemorySize/1KB)) MB\"",
            "cpu_load":"(Get-CimInstance Win32_Processor|Measure-Object -Property LoadPercentage -Average).Average",
            "firewall":"(Get-NetFirewallProfile|Select-Object -ExpandProperty Enabled) -join ','",
            "winrm_service":"(Get-Service WinRM).Status",
            "iis_service":"try{(Get-Service W3SVC).Status}catch{'not installed'}"
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
async def index():
    return open("/home/harris/diagnostic-tool/chat.html").read()

@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    sid = data.get("session_id","default")
    msg = data.get("message","").strip()
    if sid not in sessions:
        sessions[sid] = {"step":"idle","data":{}}
    s = sessions[sid]
    step = s["step"]
    sd = s["data"]

    if step == "idle":
        ip = extract_ip(msg)
        if ip or any(w in msg.lower() for w in ["check","scan","diagnose","server"]):
            if ip:
                sd["target_ip"] = ip
                s["step"] = "ask_gateway"
                return JSONResponse({"reply": f"Got it — checking server **{ip}**.\n\nWhat is the **gateway IP**?", "step":"ask_gateway"})
            else:
                s["step"] = "ask_ip"
                return JSONResponse({"reply":"Sure! What is the **IP address** of the server you want to check?","step":"ask_ip"})
        else:
            reply = ask_ai(f"You are a helpful data centre assistant. Answer briefly: {msg}")
            return JSONResponse({"reply":reply,"step":"idle"})

    elif step == "ask_ip":
        ip = extract_ip(msg)
        if ip:
            sd["target_ip"] = ip
            s["step"] = "ask_gateway"
            return JSONResponse({"reply":f"Server IP: **{ip}**\n\nWhat is the **gateway IP**?","step":"ask_gateway"})
        return JSONResponse({"reply":"Please enter a valid IP e.g. **192.168.99.143**","step":"ask_ip"})

    elif step == "ask_gateway":
        ip = extract_ip(msg)
        if ip:
            sd["gateway_ip"] = ip
            s["step"] = "ask_dns"
            return JSONResponse({"reply":f"Gateway: **{ip}**\n\nDNS server IP? (type **skip** to use 8.8.8.8)","step":"ask_dns"})
        return JSONResponse({"reply":"Please enter a valid gateway IP.","step":"ask_gateway"})

    elif step == "ask_dns":
        ip = extract_ip(msg)
        sd["dns_server"] = ip if ip else "8.8.8.8"
        s["step"] = "ask_username"
        return JSONResponse({"reply":f"DNS: **{sd['dns_server']}**\n\nWhat is the **username** for this server?","step":"ask_username"})

    elif step == "ask_username":
        sd["username"] = msg
        s["step"] = "ask_password"
        return JSONResponse({"reply":"What is the **password**?","step":"ask_password"})

    elif step == "ask_password":
        sd["password"] = msg
        s["step"] = "idle"
        return JSONResponse({"reply":f"Running diagnostic on **{sd['target_ip']}**... please wait ⏳","step":"running","run_diagnostic":True,"data":sd})

    return JSONResponse({"reply":"Type **check [IP]** to start.","step":"idle"})

@app.post("/run_diagnostic")
async def run_diag(request: Request):
    data = await request.json()
    ip = data.get("target_ip")
    gw = data.get("gateway_ip")
    dns = data.get("dns_server","8.8.8.8")
    user = data.get("username")
    pw = data.get("password")

    results, conflict, conflict_info, os_type, details = run_diagnostic(ip, gw, dns, user, pw)

    failed = [k for k,v in results.items() if not v]
    passed = [k for k,v in results.items() if v]

    detail_str = ""
    if details.get("accessible"):
        detail_str = f"\nDeep scan ({os_type.upper()}):\n"
        for k,v in details.items():
            if k not in ["accessible","os_type","error"]:
                detail_str += f"- {k.replace('_',' ').title()}: {v}\n"
    else:
        detail_str = f"\nCould not access server: {details.get('error','unknown')}"

    prompt = f"""You are a network engineer. Server at {ip} ({os_type.upper()}) just deployed.
PASSED: {', '.join(passed) if passed else 'None'}
FAILED: {', '.join(failed) if failed else 'None'}
IP conflict: {conflict_info}
{detail_str}
Give a SHORT summary (3-4 sentences) of what is wrong and the most important fix."""

    reply = ask_ai(prompt)

    return JSONResponse({
        "reply": reply,
        "results": results,
        "conflict": conflict,
        "conflict_info": conflict_info,
        "os_type": os_type,
        "details": {k:v for k,v in details.items() if k not in ["accessible","error"]},
        "accessible": details.get("accessible", False),
        "show_report_btn": True,
        "target_ip": ip,
        "gateway_ip": gw,
        "dns_server": dns,
        "username": user,
        "password": pw
    })


@app.get("/report", response_class=HTMLResponse)
async def report(target_ip: str = "", gateway_ip: str = "", dns_server: str = "8.8.8.8", username: str = "", password: str = ""):
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Report — {target_ip}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e2e8f0;min-height:100vh}}
.header{{background:#1a1d2e;border-bottom:1px solid #2d3748;padding:1.25rem 2rem;display:flex;align-items:center;gap:1rem}}
.header h1{{font-size:1.1rem;font-weight:600}}
.back{{background:none;border:1px solid #2d3748;color:#a0aec0;padding:0.4rem 0.9rem;border-radius:6px;cursor:pointer;font-size:0.85rem}}
.container{{max-width:900px;margin:2rem auto;padding:0 1.5rem}}
.card{{background:#1a1d2e;border:1px solid #2d3748;border-radius:12px;padding:1.5rem;margin-bottom:1.5rem}}
.card h2{{font-size:0.85rem;font-weight:600;color:#a0aec0;margin-bottom:1rem;text-transform:uppercase;letter-spacing:0.05em}}
.spinner{{width:32px;height:32px;border:3px solid #2d3748;border-top-color:#3182ce;border-radius:50%;animation:spin 0.8s linear infinite;margin:1rem auto}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.loading{{text-align:center;padding:2rem;color:#718096}}
</style></head>
<body>
<div class="header">
  <button class="back" onclick="window.close()">← Close</button>
  <h1>Full Diagnostic Report — {target_ip}</h1>
</div>
<div class="container">
  <div id="content" class="card">
    <div class="loading"><div class="spinner"></div><p>Running full diagnostic on {target_ip}...</p></div>
  </div>
</div>
<script>
const labels = {{
  gateway_reachable:'Gateway reachable',server_reachable:'Server reachable (ping)',
  dns_working:'DNS resolution',ssh_open:'SSH port 22 (Linux)',
  winrm_open:'WinRM port 5985 (Windows)',http_open:'HTTP port 80',https_open:'HTTPS port 443'
}};
fetch('/run_diagnostic',{{
  method:'POST',
  headers:{{'Content-Type':'application/json'}},
  body:JSON.stringify({{
    target_ip:'{target_ip}',
    gateway_ip:'{gateway_ip}',
    dns_server:'{dns_server}',
    username:'{username}',
    password:'{password}',
    session_id:'report'
  }})
}}).then(r=>r.json()).then(data=>{{
  let checks='';
  for(const [k,v] of Object.entries(data.results||{{}})){{
    checks+=`<div style="display:flex;align-items:center;gap:.75rem;padding:.6rem 0;border-bottom:1px solid #2d3748">
      <span style="color:${{v?'#48bb78':'#fc8181'}};font-weight:700;font-size:1.1rem">${{v?'✓':'✗'}}</span>
      <span style="font-size:.9rem">${{labels[k]||k}}</span></div>`;
  }}
  let deep='';
  for(const [k,v] of Object.entries(data.details||{{}})){{
    if(k==='os_type')continue;
    deep+=`<div style="background:#0f1117;border-radius:6px;padding:.5rem .75rem">
      <div style="font-size:.75rem;color:#718096">${{k.replace(/_/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase())}}</div>
      <div style="font-size:.85rem;word-break:break-all;margin-top:.1rem">${{v}}</div></div>`;
  }}
  const conflict=data.conflict?`<div style="background:#742a2a;border:1px solid #fc8181;border-radius:8px;padding:.75rem 1rem;margin-bottom:1rem;color:#fc8181">⚠ IP Conflict: ${{data.conflict_info}}</div>`:'';
  document.getElementById('content').outerHTML=`
    ${{conflict}}
    <div class="card"><h2>Network Checks <span style="background:#2b4c7e;color:#90cdf4;padding:.2rem .6rem;border-radius:4px;font-size:.75rem;margin-left:.5rem">${{(data.os_type||'').toUpperCase()}}</span></h2>${{checks}}</div>
    <div class="card"><h2>Deep Scan Results</h2><div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem">${{deep}}</div></div>
    <div class="card"><h2>AI Diagnosis</h2><div style="background:#0f1117;border-radius:8px;padding:1rem;font-size:.88rem;line-height:1.7;white-space:pre-wrap">${{data.reply}}</div></div>`;
}});
</script></body></html>"""
    return html

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
