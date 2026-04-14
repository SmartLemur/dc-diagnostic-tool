import subprocess
import socket
import requests
import paramiko
import winrm

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"

def check_ping(host):
    try:
        result = subprocess.run(
            ["ping", "-c", "2", "-W", "2", host],
            capture_output=True, text=True
        )
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
        result = subprocess.run(
            ["sudo", "arp-scan", "--localnet"],
            capture_output=True, text=True
        )
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
            msg = f"IP {target_ip} found on {len(matches)} devices:\n"
            for i, d in enumerate(matches):
                msg += f"      Device {i+1}: MAC {d['mac']} ({d['type']})\n"
            msg += f"      Action: Change IP on the DUPLICATE device"
            return True, msg
        elif len(matches) == 1:
            return False, f"IP {target_ip} is unique — MAC {matches[0]['mac']}"
        else:
            return False, f"IP {target_ip} not found on network scan"
    except Exception as e:
        return False, f"ARP scan failed: {str(e)}"

def detect_os(target_ip):
    # Check if WinRM port is open = Windows
    if check_port(target_ip, 5985):
        return "windows"
    # Check if SSH port is open = Linux
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
            "ssh_service":   "systemctl is-active ssh 2>/dev/null || echo inactive",
            "http_service":  "systemctl is-active apache2 2>/dev/null || systemctl is-active nginx 2>/dev/null || echo inactive",
            "firewall":      "sudo ufw status 2>/dev/null || echo unknown",
            "disk_usage":    "df -h / | tail -1 | awk '{print $5}'",
            "memory_usage":  "free -m | awk 'NR==2{printf \"%s/%s MB\", $3, $2}'",
            "cpu_load":      "uptime | awk -F'load average:' '{print $2}' | xargs",
            "os_version":    "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            "ip_address":    "ip addr show eth0 | grep 'inet ' | awk '{print $2}'",
            "default_route": "ip route | grep default | awk '{print $3}'",
            "open_ports":    "ss -tlnp | grep LISTEN | awk '{print $4}' | tr '\n' ' '"
        }

        for key, cmd in checks.items():
            stdin, stdout, stderr = client.exec_command(cmd)
            output = stdout.read().decode().strip()
            details[key] = output if output else "not found"

        client.close()
        details["ssh_accessible"] = True

    except paramiko.AuthenticationException:
        details["ssh_accessible"] = False
        details["error"] = "Authentication failed"
    except Exception as e:
        details["ssh_accessible"] = False
        details["error"] = str(e)

    return details

def deep_check_windows(target_ip, username, password):
    details = {"os_type": "windows"}
    try:
        session = winrm.Session(
            target_ip,
            auth=(username, password),
            transport='ntlm',
            server_cert_validation='ignore'
        )

        checks = {
            "os_version":    "Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption",
            "ip_address":    "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -ne '127.0.0.1'} | Select-Object -First 1).IPAddress",
            "default_route": "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1).NextHop",
            "disk_usage":    "$d = Get-PSDrive C; [math]::Round($d.Used/($d.Used+$d.Free)*100),'%' -join ''",
            "memory_usage":  "$os = Get-CimInstance Win32_OperatingSystem; \"$([math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/1KB))/$([math]::Round($os.TotalVisibleMemorySize/1KB)) MB\"",
            "cpu_load":      "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average",
            "firewall":      "(Get-NetFirewallProfile | Select-Object -ExpandProperty Enabled) -join ','",
            "winrm_service": "(Get-Service WinRM).Status",
            "iis_service":   "try { (Get-Service W3SVC).Status } catch { 'not installed' }",
            "open_ports":    "netstat -an | findstr LISTENING | ForEach-Object { ($_ -split '\s+')[2] } | Select-Object -Unique"
        }

        for key, cmd in checks.items():
            try:
                result = session.run_ps(cmd)
                output = result.std_out.decode().strip()
                details[key] = output if output else "not found"
            except:
                details[key] = "error reading"

        details["winrm_accessible"] = True

    except Exception as e:
        details["winrm_accessible"] = False
        details["error"] = str(e)

    return details

def run_checks(target_ip, gateway_ip, dns_server):
    results = {}
    print(f"\n[1] Checking link to gateway {gateway_ip}...")
    results["gateway_reachable"] = check_ping(gateway_ip)
    print(f"[2] Checking target server {target_ip}...")
    results["server_reachable"] = check_ping(target_ip)
    print(f"[3] Checking DNS resolution...")
    results["dns_working"] = check_dns(dns_server)
    print(f"[4] Checking SSH port (Linux)...")
    results["ssh_open"] = check_port(target_ip, 22)
    print(f"[5] Checking WinRM port (Windows)...")
    results["winrm_open"] = check_port(target_ip, 5985)
    print(f"[6] Checking HTTP port...")
    results["http_open"] = check_port(target_ip, 80)
    print(f"[7] Checking HTTPS port...")
    results["https_open"] = check_port(target_ip, 443)
    return results

def ask_ai(results, details, target_ip, conflict_info, os_type):
    failed = [k for k, v in results.items() if not v]
    passed = [k for k, v in results.items() if v]

    accessible = details.get("ssh_accessible") or details.get("winrm_accessible")

    detail_str = ""
    if accessible:
        detail_str = f"\nDeep scan from inside server ({os_type.upper()}):\n"
        for key, val in details.items():
            if key not in ["ssh_accessible", "winrm_accessible", "os_type", "error"]:
                detail_str += f"- {key.replace('_', ' ').title()}: {val}\n"
    else:
        detail_str = f"\nCould not access server: {details.get('error', 'unknown')}"

    message = f"""You are a network engineer. Be direct and practical.

Server at {target_ip} ({os_type.upper()}) was just deployed.
PASSED: {', '.join(passed) if passed else 'None'}
FAILED: {', '.join(failed) if failed else 'None'}
IP conflict scan: {conflict_info}
{detail_str}

List ALL problems ranked by priority. For each problem state:
1. What is wrong
2. Why it happened
3. Exact command to fix it ({os_type.upper()} specific)

Be specific and practical."""

    print("\n[AI] Analysing results...\n")

    try:
        response = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 600,
                "num_ctx": 4096
            }
        }, timeout=120)

        if response.status_code == 200:
            data = response.json()
            text = data.get("message", {}).get("content", "").strip()
            return text if text else f"Raw: {data}"
        else:
            return f"API error {response.status_code}"

    except requests.exceptions.Timeout:
        return "Request timed out."
    except Exception as e:
        return f"Error: {str(e)}"

def main():
    print("=" * 50)
    print("  DATA CENTRE DEPLOYMENT DIAGNOSTIC TOOL")
    print("=" * 50)

    target_ip  = input("\nEnter new server IP address: ").strip()
    gateway_ip = input("Enter gateway IP address: ").strip()
    dns_server = input("Enter DNS server hostname or IP: ").strip()
    username   = input("Enter server username: ").strip()
    password   = input("Enter server password: ").strip()

    print("\nRunning network checks...")
    results = run_checks(target_ip, gateway_ip, dns_server)

    print("\n[8] Checking for IP conflicts...")
    conflict, conflict_info = check_ip_conflict(target_ip)
    if conflict:
        print(f"  ✗ CONFLICT DETECTED")
        print(f"{conflict_info}")
    else:
        print(f"  ✓ {conflict_info}")

    print("\n[9] Detecting operating system...")
    os_type = detect_os(target_ip)
    print(f"  → Detected: {os_type.upper()}")

    print(f"\nRunning deep scan ({os_type.upper()})...")
    if os_type == "linux":
        details = deep_check_linux(target_ip, username, password)
    elif os_type == "windows":
        details = deep_check_windows(target_ip, username, password)
    else:
        details = {"error": "Could not detect OS — server may be offline or all ports blocked"}

    print("\n" + "=" * 50)
    print("NETWORK CHECK RESULTS")
    print("=" * 50)
    for check, status in results.items():
        icon = "✓" if status else "✗"
        print(f"  {icon} {check.replace('_', ' ').title()}")
    conflict_icon = "✗" if conflict else "✓"
    print(f"  {conflict_icon} IP Conflict Check")
    print(f"  → OS Detected: {os_type.upper()}")

    accessible = details.get("ssh_accessible") or details.get("winrm_accessible")
    if accessible:
        print("\n" + "=" * 50)
        print(f"DEEP SCAN RESULTS ({os_type.upper()})")
        print("=" * 50)
        for key, val in details.items():
            if key not in ["ssh_accessible", "winrm_accessible", "os_type", "error"]:
                print(f"  {key.replace('_',' ').title()}: {val}")

    failed = [k for k, v in results.items() if not v]
    if not failed and not conflict and accessible:
        print("\n✓ All checks passed. Server is ready.")
    else:
        print(f"\nRequesting AI diagnosis...")
        diagnosis = ask_ai(results, details, target_ip, conflict_info, os_type)
        print("\n" + "=" * 50)
        print("AI DIAGNOSIS")
        print("=" * 50)
        print(diagnosis)
        print("=" * 50)

if __name__ == "__main__":
    main()
