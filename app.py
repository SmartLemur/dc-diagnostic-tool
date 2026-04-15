from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
import subprocess
import socket
import requests
import paramiko
import winrm

app = FastAPI()

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"

def check_ping(host):
    try:
        result = subprocess.run(["ping", "-c", "2", "-W", "2", host], capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False

def check_port(host, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def check_dns(hostname):
    try:
        socket.gethostbyname(hostname)
        return True
    except:
        return False

def check_ip_conflict(target_ip):
    try:
        result = subprocess.run(["sudo", "arp-scan", "--localnet"], capture_output=True, text=True)
        lines = result.stdout.split('\n')
        matches = []
        for line in lines:
            if target_ip in line and not line.startswith('WARNING'):
                parts = line.split()
                if len(parts) >= 2:
                    mac = parts[1]
                    dup = "DUPLICATE" if "DUP" in line else "PRIMARY"
                    matches.append({"mac": mac, "type": dup})
        if len(matches) > 1:
            msg = f"IP {target_ip} found on {len(matches)} devices: "
            msg += " | ".join([f"MAC {d['mac']} ({d['type']})" for d in matches])
            return True, msg
        elif len(matches) == 1:
            return False, f"Unique — MAC {matches[0]['mac']}"
        else:
            return False, "Not found on network scan"
    except Exception as e:
        return False, f"Scan failed: {str(e)}"

def detect_os(target_ip):
    if check_port(target_ip, 5985):
        return "windows"
    if check_port(target_ip, 22):
        return "linux"
    return "unknown"

def deep_check_linux(target_ip, username, password):
    details = {"os_type": "linux"}
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(target_ip, username=username, password=password, timeout=5)
        checks = {
            "ssh_service": "systemctl is-active ssh 2>/dev/null || echo inactive",
            "http_service": "systemctl is-active apache2 2>/dev/null || systemctl is-active nginx 2>/dev/null || echo inactive",
            "firewall": "sudo ufw status 2>/dev/null || echo unknown",
            "disk_usage": "df -h / | tail -1 | awk '{print $5}'",
            "memory_usage": "free -m | awk 'NR==2{printf \"%s/%s MB\", $3, $2}'",
            "cpu_load": "uptime | awk -F'load average:' '{print $2}' | xargs",
            "os_version": "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            "ip_address": "ip addr show eth0 | grep 'inet ' | awk '{print $2}'",
            "default_route": "ip route | grep default | awk '{print $3}'",
            "open_ports": "ss -tlnp | grep LISTEN | awk '{print $4}' | tr '\n' ' '"
        }
        for key, cmd in checks.items():
            stdin, stdout, stderr = client.exec_command(cmd)
            output = stdout.read().decode().strip()
            details[key] = output if output else "not found"
        client.close()
        details["accessible"] = True
    except paramiko.AuthenticationException:
        details["accessible"] = False
        details["error"] = "Authentication failed"
    except Exception as e:
        details["accessible"] = False
        details["error"] = str(e)
    return details

def deep_check_windows(target_ip, username, password):
    details = {"os_type": "windows"}
    try:
        session = winrm.Session(target_ip, auth=(username, password), transport='ntlm', server_cert_validation='ignore')
        checks = {
            "os_version": "Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption",
            "ip_address": "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -ne '127.0.0.1'} | Select-Object -First 1).IPAddress",
            "default_route": "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).NextHop",
            "disk_usage": "$d = Get-PSDrive C; [math]::Round($d.Used/($d.Used+$d.Free)*100),'%' -join ''",
            "memory_usage": "$os = Get-CimInstance Win32_OperatingSystem; \"$([math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/1KB))/$([math]::Round($os.TotalVisibleMemorySize/1KB)) MB\"",
            "cpu_load": "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average",
            "firewall": "(Get-NetFirewallProfile | Select-Object -ExpandProperty Enabled) -join ','",
            "winrm_service": "(Get-Service WinRM).Status",
            "iis_service": "try { (Get-Service W3SVC).Status } catch { 'not installed' }",
        }
        for key, cmd in checks.items():
            try:
                result = session.run_ps(cmd)
                output = result.std_out.decode().strip()
                details[key] = output if output else "not found"
            except:
                details[key] = "error reading"
        details["accessible"] = True
    except Exception as e:
        details["accessible"] = False
        details["error"] = str(e)
    return details

def ask_ai(results, details, target_ip, conflict_info, os_type):
    failed = [k for k, v in results.items() if not v]
    passed = [k for k, v in results.items() if v]
    detail_str = ""
    if details.get("accessible"):
        detail_str = f"\nDeep scan ({os_type.upper()}):\n"
        for key, val in details.items():
            if key not in ["accessible", "os_type", "error"]:
                detail_str += f"- {key.replace('_', ' ').title()}: {val}\n"
    else:
        detail_str = f"\nCould not access server: {details.get('error', 'unknown')}"
    message = f"""You are a network engineer. Be direct and practical.
Server at {target_ip} ({os_type.upper()}) was just deployed.
PASSED: {', '.join(passed) if passed else 'None'}
FAILED: {', '.join(failed) if failed else 'None'}
IP conflict: {conflict_info}
{detail_str}
List ALL problems ranked by priority. For each: what is wrong, why, exact fix command."""
    try:
        response = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 600, "num_ctx": 4096}
        }, timeout=120)
        if response.status_code == 200:
            text = response.json().get("message", {}).get("content", "").strip()
            return text if text else "No response from AI"
        return f"API error {response.status_code}"
    except Exception as e:
        return f"Error: {str(e)}"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DC Diagnostic Tool</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }
.header { background: #1a1d2e; border-bottom: 1px solid #2d3748; padding: 1.5rem 2rem; }
.header h1 { font-size: 1.4rem; font-weight: 600; color: #fff; }
.header p { font-size: 0.85rem; color: #718096; margin-top: 0.25rem; }
.container { max-width: 900px; margin: 2rem auto; padding: 0 1.5rem; }
.card { background: #1a1d2e; border: 1px solid #2d3748; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }
.card h2 { font-size: 1rem; font-weight: 600; color: #a0aec0; margin-bottom: 1rem; text-transform: uppercase; letter-spacing: 0.05em; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.form-group { display: flex; flex-direction: column; gap: 0.4rem; }
.form-group label { font-size: 0.8rem; color: #718096; font-weight: 500; }
.form-group input { background: #0f1117; border: 1px solid #2d3748; border-radius: 8px; padding: 0.6rem 0.8rem; color: #e2e8f0; font-size: 0.9rem; outline: none; }
.form-group input:focus { border-color: #4299e1; }
.btn { background: #3182ce; color: white; border: none; padding: 0.75rem 2rem; border-radius: 8px; font-size: 0.95rem; font-weight: 600; cursor: pointer; width: 100%; margin-top: 1rem; }
.btn:hover { background: #2b6cb0; }
.check-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 0; border-bottom: 1px solid #2d3748; }
.check-row:last-child { border-bottom: none; }
.badge { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; flex-shrink: 0; }
.badge.pass { background: #276749; color: #9ae6b4; }
.badge.fail { background: #742a2a; color: #fc8181; }
.check-name { font-size: 0.9rem; color: #e2e8f0; }
.check-detail { font-size: 0.8rem; color: #718096; margin-left: auto; }
.os-badge { display: inline-block; padding: 0.2rem 0.6rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
.os-linux { background: #276749; color: #9ae6b4; }
.os-windows { background: #2b4c7e; color: #90cdf4; }
.os-unknown { background: #4a5568; color: #a0aec0; }
.deep-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
.deep-item { background: #0f1117; border-radius: 6px; padding: 0.5rem 0.75rem; }
.deep-key { font-size: 0.75rem; color: #718096; }
.deep-val { font-size: 0.85rem; color: #e2e8f0; margin-top: 0.1rem; word-break: break-all; }
.ai-box { background: #0f1117; border-radius: 8px; padding: 1rem; font-size: 0.88rem; line-height: 1.7; white-space: pre-wrap; color: #e2e8f0; }
.conflict-warn { background: #742a2a; border: 1px solid #fc8181; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-size: 0.88rem; color: #fc8181; }
.all-pass { background: #276749; border: 1px solid #9ae6b4; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-size: 0.88rem; color: #9ae6b4; text-align: center; font-weight: 600; }
.loading { text-align: center; padding: 3rem; color: #718096; }
</style>
</head>
<body>
<div class="header">
  <h1>Data Centre Deployment Diagnostic Tool</h1>
  <p>AI-powered server deployment checker — Cloudify Asia</p>
</div>
<div class="container">
  <div class="card">
    <h2>Run Diagnostic</h2>
    <form method="post" action="/diagnose">
      <div class="form-grid">
        <div class="form-group">
          <label>Target Server IP</label>
          <input type="text" name="target_ip" placeholder="192.168.99.143" required>
        </div>
        <div class="form-group">
          <label>Gateway IP</label>
          <input type="text" name="gateway_ip" placeholder="192.168.99.1" required>
        </div>
        <div class="form-group">
          <label>DNS Server</label>
          <input type="text" name="dns_server" placeholder="8.8.8.8" required>
        </div>
        <div class="form-group">
          <label>Username</label>
          <input type="text" name="username" placeholder="administrator" required>
        </div>
        <div class="form-group">
          <label>Password</label>
          <input type="password" name="password" placeholder="••••••••" required>
        </div>
      </div>
      <button type="submit" class="btn">Run Diagnostic</button>
    </form>
  </div>
  {results_html}
</div>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_TEMPLATE.replace("{results_html}", "")

@app.post("/diagnose", response_class=HTMLResponse)
async def diagnose(
    target_ip: str = Form(...),
    gateway_ip: str = Form(...),
    dns_server: str = Form(...),
    username: str = Form(...),
    password: str = Form(...)
):
    results = {
        "gateway_reachable": check_ping(gateway_ip),
        "server_reachable": check_ping(target_ip),
        "dns_working": check_dns(dns_server),
        "ssh_open": check_port(target_ip, 22),
        "winrm_open": check_port(target_ip, 5985),
        "http_open": check_port(target_ip, 80),
        "https_open": check_port(target_ip, 443),
    }

    conflict, conflict_info = check_ip_conflict(target_ip)
    os_type = detect_os(target_ip)

    if os_type == "linux":
        details = deep_check_linux(target_ip, username, password)
    elif os_type == "windows":
        details = deep_check_windows(target_ip, username, password)
    else:
        details = {"accessible": False, "error": "OS unknown — server may be offline"}

    failed = [k for k, v in results.items() if not v]
    diagnosis = ""
    if failed or conflict:
        diagnosis = ask_ai(results, details, target_ip, conflict_info, os_type)

    # Build results HTML
    checks_html = ""
    labels = {
        "gateway_reachable": "Gateway reachable",
        "server_reachable": "Server reachable (ping)",
        "dns_working": "DNS resolution",
        "ssh_open": "SSH port 22 (Linux)",
        "winrm_open": "WinRM port 5985 (Windows)",
        "http_open": "HTTP port 80",
        "https_open": "HTTPS port 443",
    }
    for key, val in results.items():
        status = "pass" if val else "fail"
        icon = "✓" if val else "✗"
        checks_html += f"""
        <div class="check-row">
          <div class="badge {status}">{icon}</div>
          <span class="check-name">{labels.get(key, key)}</span>
        </div>"""

    conflict_html = ""
    if conflict:
        conflict_html = f'<div class="conflict-warn">⚠ IP Conflict: {conflict_info}</div>'

    os_class = f"os-{os_type}"
    os_label = os_type.upper()

    deep_html = ""
    if details.get("accessible"):
        deep_html = '<div class="deep-grid">'
        for key, val in details.items():
            if key not in ["accessible", "os_type", "error"]:
                deep_html += f"""
                <div class="deep-item">
                  <div class="deep-key">{key.replace('_', ' ').title()}</div>
                  <div class="deep-val">{val}</div>
                </div>"""
        deep_html += "</div>"
    else:
        deep_html = f'<p style="color:#718096;font-size:0.85rem">Could not access server: {details.get("error", "unknown")}</p>'

    all_pass_html = ""
    if not failed and not conflict and details.get("accessible"):
        all_pass_html = '<div class="all-pass">✓ All checks passed — server is ready for deployment</div>'

    ai_html = ""
    if diagnosis:
        ai_html = f"""
        <div class="card">
          <h2>AI Diagnosis</h2>
          <div class="ai-box">{diagnosis}</div>
        </div>"""

    results_html = f"""
    {conflict_html}
    {all_pass_html}
    <div class="card">
      <h2>Network Checks &nbsp; <span class="os-badge {os_class}">{os_label}</span></h2>
      {checks_html}
    </div>
    <div class="card">
      <h2>Deep Scan Results</h2>
      {deep_html}
    </div>
    {ai_html}"""

    return HTML_TEMPLATE.replace("{results_html}", results_html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
