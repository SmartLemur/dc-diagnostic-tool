import paramiko
import time

SWITCH_COMMANDS = {
    "h3c": {
        "show_ports": "display interface brief",
        "show_mac": "display mac-address",
        "show_vlan": "display vlan all",
    },
    "cisco": {
        "show_ports": "show interfaces status",
        "show_mac": "show mac address-table",
        "show_vlan": "show vlan brief",
    },
    "juniper": {
        "show_ports": "show interfaces terse",
        "show_mac": "show ethernet-switching table",
        "show_vlan": "show vlans",
    }
}

def get_switches_from_db():
    try:
        from database import get_devices
        devices = get_devices(device_type="switch")
        return devices
    except:
        return []

def detect_brand_from_banner(banner):
    banner_lower = banner.lower()
    if "h3c" in banner_lower or "new h3c" in banner_lower or "comware" in banner_lower:
        return "h3c"
    elif "cisco" in banner_lower or "ios" in banner_lower or "catalyst" in banner_lower:
        return "cisco"
    elif "juniper" in banner_lower or "junos" in banner_lower:
        return "juniper"
    elif "huawei" in banner_lower:
        return "h3c"
    return "unknown"

def ssh_connect(ip, username, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        ip,
        username=username,
        password=password,
        timeout=10,
        disabled_algorithms={"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
    )
    return client

def get_switch_output(ip, username, password, command, wait=3):
    try:
        client = ssh_connect(ip, username, password)
        channel = client.invoke_shell()
        time.sleep(2)
        banner = ""
        if channel.recv_ready():
            banner = channel.recv(65535).decode('utf-8', errors='ignore')
        channel.send(command + "\n")
        time.sleep(wait)
        output = ""
        for _ in range(10):
            time.sleep(0.5)
            if channel.recv_ready():
                chunk = channel.recv(65535).decode('utf-8', errors='ignore')
                output += chunk
                if "More" in chunk or "more" in chunk:
                    channel.send(" ")
                    time.sleep(1)
            else:
                break
        client.close()
        return output, banner
    except Exception as e:
        return f"ERROR: {e}", ""

def parse_ports_h3c(output):
    ports = []
    bridge_section = False
    for line in output.split('\n'):
        line = line.strip()
        if 'bridge mode' in line.lower():
            bridge_section = True
            continue
        if not bridge_section:
            continue
        if any(h in line for h in ['Link:', 'Speed:', 'Duplex:', 'Type:', 'Interface']):
            continue
        parts = line.split()
        if len(parts) >= 5 and (
            parts[0].startswith('WGE') or
            parts[0].startswith('HGE') or
            parts[0].startswith('GE') or
            parts[0].startswith('XGE') or
            parts[0].startswith('Eth')
        ):
            ports.append({
                "interface": parts[0],
                "status": parts[1],
                "speed": parts[2] if len(parts) > 2 else "auto",
                "duplex": parts[3] if len(parts) > 3 else "auto",
                "type": parts[4] if len(parts) > 4 else "A",
                "vlan": parts[5] if len(parts) > 5 else "1",
                "description": " ".join(parts[6:]) if len(parts) > 6 else ""
            })
    return ports

def parse_ports_cisco(output):
    ports = []
    for line in output.split('\n'):
        line = line.strip()
        parts = line.split()
        if len(parts) >= 3 and (
            parts[0].startswith('Gi') or
            parts[0].startswith('Fa') or
            parts[0].startswith('Te') or
            parts[0].startswith('Et')
        ):
            status = "UP" if "connected" in line.lower() else "DOWN"
            ports.append({
                "interface": parts[0],
                "status": status,
                "speed": parts[-1] if len(parts) > 4 else "auto",
                "duplex": parts[-2] if len(parts) > 3 else "auto",
                "type": "A",
                "vlan": parts[2] if len(parts) > 2 else "1",
                "description": parts[1] if len(parts) > 1 else ""
            })
    return ports

def get_port_status(ip=None, username=None, password=None, brand=None):
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "").lower()

    if not brand or brand == "unknown":
        command = SWITCH_COMMANDS["h3c"]["show_ports"]
    elif "cisco" in brand.lower():
        command = SWITCH_COMMANDS["cisco"]["show_ports"]
    elif "juniper" in brand.lower():
        command = SWITCH_COMMANDS["juniper"]["show_ports"]
    else:
        command = SWITCH_COMMANDS["h3c"]["show_ports"]

    output, banner = get_switch_output(ip, username, password, command, wait=4)

    if output.startswith("ERROR"):
        return {"error": output}

    if not brand or brand == "unknown":
        brand = detect_brand_from_banner(banner)

    if "cisco" in brand.lower():
        return parse_ports_cisco(output)
    else:
        return parse_ports_h3c(output)

def get_mac_table(ip=None, username=None, password=None, brand=None):
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "").lower()

    if brand and "cisco" in brand.lower():
        command = SWITCH_COMMANDS["cisco"]["show_mac"]
    else:
        command = SWITCH_COMMANDS["h3c"]["show_mac"]

    output, _ = get_switch_output(ip, username, password, command, wait=3)
    if output.startswith("ERROR"):
        return {"error": output}

    macs = []
    for line in output.split('\n'):
        line = line.strip()
        parts = line.split()
        if len(parts) >= 3 and '-' in parts[0] and len(parts[0]) == 14:
            macs.append({
                "mac": parts[0],
                "vlan": parts[1] if len(parts) > 1 else "1",
                "interface": parts[2] if len(parts) > 2 else "",
                "type": parts[3] if len(parts) > 3 else ""
            })
    return macs

def get_switch_summary(ip=None, username=None, password=None, brand=None):
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches registered. Add switches via Settings page."}
        targets = switches
    else:
        targets = [{"ip": ip, "username": username, "password": password, "brand": brand or "unknown", "name": ip}]

    all_switches = []
    for sw in targets:
        ports = get_port_status(
            ip=sw["ip"],
            username=sw["username"],
            password=sw["password"],
            brand=sw.get("brand", "unknown")
        )

        if isinstance(ports, dict) and "error" in ports:
            all_switches.append({
                "name": sw.get("name", sw["ip"]),
                "ip": sw["ip"],
                "error": ports["error"]
            })
            continue

        up_ports = [p for p in ports if p["status"] == "UP"]
        down_ports = [p for p in ports if p["status"] == "DOWN"]

        all_switches.append({
            "name": sw.get("name", sw["ip"]),
            "ip": sw["ip"],
            "brand": sw.get("brand", "Unknown"),
            "total_ports": len(ports),
            "up_count": len(up_ports),
            "down_count": len(down_ports),
            "up_ports": up_ports,
            "down_ports": down_ports
        })

    if len(all_switches) == 1:
        return all_switches[0]
    return {"switches": all_switches, "total_switches": len(all_switches)}

if __name__ == "__main__":
    print("Testing switches from database...")
    switches = get_switches_from_db()
    if not switches:
        print("No switches in database. Add via Settings page.")
    else:
        for sw in switches:
            print(f"\nChecking {sw['name']} ({sw['ip']})...")
            summary = get_switch_summary(
                ip=sw["ip"],
                username=sw["username"],
                password=sw["password"],
                brand=sw.get("brand", "unknown")
            )
            if "error" in summary:
                print(f"  ERROR: {summary['error']}")
            else:
                print(f"  Total: {summary['total_ports']} | UP: {summary['up_count']} | DOWN: {summary['down_count']}")
                for p in summary.get('up_ports', []):
                    print(f"  {p['interface']} - {p['speed']} - VLAN {p['vlan']}")


def get_vlans(ip=None, username=None, password=None, brand=None):
    """Get all valid VLANs from switch dynamically"""
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    if brand and "cisco" in brand.lower():
        command = SWITCH_COMMANDS["cisco"]["show_vlan"]
    else:
        command = SWITCH_COMMANDS["h3c"]["show_vlan"]

    output, _ = get_switch_output(ip, username, password, command, wait=3)
    if output.startswith("ERROR"):
        return {"error": output}

    vlans = []
    for line in output.split('\n'):
        line = line.strip()
        if 'VLAN ID:' in line:
            try:
                vlan_id = int(line.split('VLAN ID:')[1].strip())
                vlans.append(vlan_id)
            except:
                pass
        elif line.startswith('VLAN') and 'active' in line.lower():
            try:
                vlan_id = int(line.split()[1])
                vlans.append(vlan_id)
            except:
                pass
    return vlans

def configure_port_vlan(interface, vlan_id, ip=None, username=None, password=None, brand=None):
    """Configure switch port VLAN — dynamic, works for any switch"""
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"success": False, "error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    try:
        client = ssh_connect(ip, username, password)
        channel = client.invoke_shell()
        time.sleep(2)
        if channel.recv_ready():
            channel.recv(65535)

        if brand and "cisco" in brand.lower():
            commands = [
                "configure terminal",
                f"interface {interface}",
                "switchport mode access",
                f"switchport access vlan {vlan_id}",
                "end",
                "write memory"
            ]
        else:
            # H3C commands
            commands = [
                "system-view",
                f"interface {interface}",
                "port link-type access",
                f"port access vlan {vlan_id}",
                "quit",
                "quit",
                "save force"
            ]

        output = ""
        for cmd in commands:
            channel.send(cmd + "\n")
            time.sleep(1)
            if channel.recv_ready():
                output += channel.recv(65535).decode('utf-8', errors='ignore')

        client.close()

        # Verify the change
        time.sleep(2)
        verify_output, _ = get_switch_output(
            ip, username, password,
            f"display interface {interface} brief" if "cisco" not in (brand or "").lower()
            else f"show interface {interface} status",
            wait=2
        )

        success = str(vlan_id) in verify_output
        return {
            "success": success,
            "interface": interface,
            "vlan_id": vlan_id,
            "output": output[:500],
            "verified": success
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

def get_port_details(interface, ip=None, username=None, password=None, brand=None):
    """Get details of a specific port"""
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    if brand and "cisco" in brand.lower():
        command = f"show interface {interface} status"
    else:
        command = f"display interface {interface} brief"

    output, _ = get_switch_output(ip, username, password, command, wait=2)
    if output.startswith("ERROR"):
        return {"error": output}

    for line in output.split('\n'):
        parts = line.split()
        if len(parts) >= 5 and interface in line:
            return {
                "interface": parts[0] if parts else interface,
                "status": parts[1] if len(parts) > 1 else "Unknown",
                "speed": parts[2] if len(parts) > 2 else "Unknown",
                "vlan": parts[5] if len(parts) > 5 else "Unknown"
            }
    return {"interface": interface, "status": "Unknown", "speed": "Unknown", "vlan": "Unknown"}