import subprocess
import socket
import requests
import paramiko
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def arp_scan():
    """Scan network and return all devices with IP and MAC"""
    try:
        result = subprocess.run(
            ["sudo", "arp-scan", "--localnet"],
            capture_output=True, text=True
        )
        devices = []
        for line in result.stdout.split('\n'):
            if line.startswith('WARNING') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                ip = parts[0].strip()
                mac = parts[1].strip()
                vendor = parts[2].strip() if len(parts) > 2 else "Unknown"
                # Basic IP validation
                if ip.count('.') == 3:
                    devices.append({
                        "ip": ip,
                        "mac": mac,
                        "vendor": vendor
                    })
        return devices
    except Exception as e:
        return {"error": str(e)}

def classify_device(ip):
    """Determine what type of device this IP is"""
    device_type = "unknown"
    open_ports = []

    port_checks = {
        443: "ilo_idrac",
        22: "ssh",
        5985: "winrm",
        80: "http",
        161: "snmp",
        23: "telnet"
    }

    for port, service in port_checks.items():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex((ip, port))
            s.close()
            if result == 0:
                open_ports.append(service)
        except:
            pass

    # Classify based on open ports
    if "ilo_idrac" in open_ports:
        device_type = "ilo"
    elif "winrm" in open_ports:
        device_type = "windows_server"
    elif "ssh" in open_ports and "snmp" not in open_ports:
        device_type = "linux_or_switch"
    elif "snmp" in open_ports:
        device_type = "network_device"
    elif "http" in open_ports:
        device_type = "web_device"

    return {
        "type": device_type,
        "open_ports": open_ports
    }

def identify_ilo(ip):
    """Get server details from iLO Redfish API — tries database credentials first"""
    try:
        from database import get_devices
        db_devices = get_devices(device_type="ilo")
        db_creds = [(d["username"], d["password"]) for d in db_devices]
    except:
        db_creds = []

    default_creds = [
        ("admin", "admin"),
        ("Administrator", "admin123"),
        ("root", "root")
    ]

    credentials = db_creds + default_creds
    
    for username, password in credentials:
        try:
            response = requests.get(
                f"https://{ip}/redfish/v1/Systems/1",
                auth=(username, password),
                verify=False,
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                health = data.get("Oem", {}).get("Hpe", {}).get("AggregateHealthStatus", {})
                return {
                    "identified": True,
                    "name": data.get("HostName", "Unknown"),
                    "model": data.get("Model", "Unknown"),
                    "brand": data.get("Manufacturer", "HPE"),
                    "power_state": data.get("PowerState", "Unknown"),
                    "ram_gb": data.get("MemorySummary", {}).get("TotalSystemMemoryGiB", 0),
                    "health": data.get("Status", {}).get("Health", "Unknown"),
                    "serial": data.get("SerialNumber", "Unknown"),
                    "credentials": {"username": username, "password": password}
                }
        except:
            continue
    return {"identified": False}

def identify_ssh_device(ip):
    """Try to identify if SSH device is a server or switch"""
    credentials = [
        ("admin", "QweAsd!23cdf"),
        ("harris", "harris"),
        ("root", "root"),
        ("admin", "admin")
    ]
    for username, password in credentials:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                ip, username=username, password=password,
                timeout=3,
                disabled_algorithms={"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
            )
            # Try Linux command
            stdin, stdout, stderr = client.exec_command("uname -a 2>/dev/null || echo 'not_linux'")
            output = stdout.read().decode().strip()
            client.close()
            if "Linux" in output:
                # Get more details
                client2 = paramiko.SSHClient()
                client2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client2.connect(ip, username=username, password=password, timeout=3)
                _, out, _ = client2.exec_command("cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'")
                os_name = out.read().decode().strip()
                _, out2, _ = client2.exec_command("hostname")
                hostname = out2.read().decode().strip()
                client2.close()
                return {
                    "identified": True,
                    "type": "linux_server",
                    "name": hostname,
                    "os": os_name,
                    "brand": "Unknown",
                    "credentials": {"username": username, "password": password}
                }
        except paramiko.AuthenticationException:
            continue
        except Exception as e:
            # Could be a switch - SSH connected but no Linux
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(
                    ip, username=username, password=password,
                    timeout=3,
                    disabled_algorithms={"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
                )
                channel = client.invoke_shell()
                import time
                time.sleep(1)
                output = channel.recv(1024).decode() if channel.recv_ready() else ""
                client.close()
                if "H3C" in output or "Huawei" in output:
                    return {"identified": True, "type": "h3c_switch", "name": f"H3C Switch ({ip})", "brand": "H3C", "credentials": {"username": username, "password": password}}
                elif "Cisco" in output:
                    return {"identified": True, "type": "cisco_switch", "name": f"Cisco Switch ({ip})", "brand": "Cisco", "credentials": {"username": username, "password": password}}
            except:
                pass
    return {"identified": False, "type": "unknown_ssh"}

def discover_network():
    """Full network discovery — foundation of topology"""
    print("Starting network discovery...")
    print("Step 1: ARP scan — finding all devices...")
    
    raw_devices = arp_scan()
    if isinstance(raw_devices, dict) and "error" in raw_devices:
        return {"error": raw_devices["error"]}

    print(f"Found {len(raw_devices)} devices on network")
    
    discovered = []
    for device in raw_devices:
        ip = device["ip"]
        mac = device["mac"]
        print(f"\nStep 2: Classifying {ip}...")
        
        classification = classify_device(ip)
        device_type = classification["type"]
        
        result = {
            "ip": ip,
            "mac": mac,
            "vendor": device["vendor"],
            "type": device_type,
            "open_ports": classification["open_ports"],
            "name": ip,
            "brand": "Unknown",
            "details": {}
        }

        print(f"  Type: {device_type} | Ports: {classification['open_ports']}")
        print(f"Step 3: Identifying {ip}...")

        if device_type == "ilo":
            ilo_info = identify_ilo(ip)
            if ilo_info["identified"]:
                result["name"] = ilo_info["name"]
                result["brand"] = ilo_info["brand"]
                result["type"] = "ilo"
                result["details"] = ilo_info
                print(f"  Identified as: {ilo_info['name']} ({ilo_info['brand']})")

        elif device_type in ["linux_or_switch", "ssh"]:
            ssh_info = identify_ssh_device(ip)
            if ssh_info["identified"]:
                result["type"] = ssh_info["type"]
                result["name"] = ssh_info.get("name", ip)
                result["brand"] = ssh_info.get("brand", "Unknown")
                result["details"] = ssh_info
                print(f"  Identified as: {ssh_info.get('name', ip)} ({ssh_info['type']})")

        elif device_type == "windows_server":
            result["name"] = f"Windows Server ({ip})"
            result["brand"] = "Microsoft"
            print(f"  Identified as: Windows Server")

        discovered.append(result)

    print(f"\nDiscovery complete — {len(discovered)} devices found")
    return discovered

if __name__ == "__main__":
    import json
    results = discover_network()
    print("\n" + "="*50)
    print("DISCOVERY RESULTS")
    print("="*50)
    if isinstance(results, list):
        for d in results:
            print(f"\n{d['name']} ({d['ip']})")
            print(f"  Type: {d['type']}")
            print(f"  MAC: {d['mac']}")
            print(f"  Brand: {d['brand']}")
            print(f"  Open ports: {d['open_ports']}")
    else:
        print(f"Error: {results}")
