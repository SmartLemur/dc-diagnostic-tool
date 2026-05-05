import time
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

BRAND_TO_NETMIKO = {
    "h3c": "hp_comware",
    "cisco": "cisco_ios",
    "juniper": "juniper_junos",
    "arista": "arista_eos",
    "huawei": "huawei",
    "fortinet": "fortinet",
    "unknown": "hp_comware"  # default fallback
}

SWITCH_COMMANDS = {
    "hp_comware": {
        "show_ports": "display interface brief",
        "show_mac": "display mac-address",
        "show_vlan": "display vlan all",
        "show_port_config": "display current-configuration interface {interface}",
        "interface_full_prefix": "Twenty-FiveGigE"
    },
    "cisco_ios": {
        "show_ports": "show interfaces status",
        "show_mac": "show mac address-table",
        "show_vlan": "show vlan brief",
        "show_port_config": "show running-config interface {interface}",
        "interface_full_prefix": "GigabitEthernet"
    },
    "juniper_junos": {
        "show_ports": "show interfaces terse",
        "show_mac": "show ethernet-switching table",
        "show_vlan": "show vlans",
        "show_port_config": "show configuration interfaces {interface}",
        "interface_full_prefix": "ge-"
    },
    "arista_eos": {
        "show_ports": "show interfaces status",
        "show_mac": "show mac address-table",
        "show_vlan": "show vlan",
        "show_port_config": "show running-config interfaces {interface}",
        "interface_full_prefix": "Ethernet"
    },
    "huawei": {
        "show_ports": "display interface brief",
        "show_mac": "display mac-address",
        "show_vlan": "display vlan all",
        "show_port_config": "display current-configuration interface {interface}",
        "interface_full_prefix": "GigabitEthernet"
    },
    "fortinet": {
        "show_ports": "get switch trunk",
        "show_mac": "diagnose switch trunk list",
        "show_vlan": "config switch vlan",
        "show_port_config": "show switch interface {interface}",
        "interface_full_prefix": "port"
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
    if "h3c" in banner_lower or "comware" in banner_lower or "new h3c" in banner_lower:
        return "h3c"
    elif "cisco" in banner_lower or "ios" in banner_lower:
        return "cisco"
    elif "juniper" in banner_lower or "junos" in banner_lower:
        return "juniper"
    elif "arista" in banner_lower:
        return "arista"
    elif "huawei" in banner_lower:
        return "huawei"
    elif "fortinet" in banner_lower or "fortiswitch" in banner_lower:
        return "fortinet"
    return "unknown"

def get_netmiko_device_type(brand):
    brand_lower = brand.lower() if brand else "unknown"
    for key in BRAND_TO_NETMIKO:
        if key in brand_lower:
            return BRAND_TO_NETMIKO[key]
    return "hp_comware"

def get_full_interface_name(interface, device_type):
    """Convert short interface name to full name for commands"""
    commands = SWITCH_COMMANDS.get(device_type, SWITCH_COMMANDS["hp_comware"])
    prefix = commands.get("interface_full_prefix", "")

    if not prefix:
        return interface

    # Already has full name
    if prefix.lower() in interface.lower():
        return interface

    # WGE1/0/5 → Twenty-FiveGigE1/0/5
    if device_type == "hp_comware":
        if interface.startswith("WGE"):
            return interface.replace("WGE", "Twenty-FiveGigE")
        elif interface.startswith("HGE"):
            return interface.replace("HGE", "HundredGigE")
        elif interface.startswith("GE"):
            return interface.replace("GE", "GigabitEthernet")

    return interface

def connect_switch(ip, username, password, brand="unknown"):
    device_type = get_netmiko_device_type(brand)
    device = {
        "device_type": device_type,
        "host": ip,
        "username": username,
        "password": password,
        "timeout": 15,
        "session_log": None,
    }
    # H3C needs special key algorithm
    if "comware" in device_type:
        device["conn_timeout"] = 10
    return ConnectHandler(**device)

def run_switch_command(ip, username, password, brand, command):
    """Run single command on switch via Netmiko"""
    try:
        connection = connect_switch(ip, username, password, brand)
        output = connection.send_command(command, read_timeout=30)
        connection.disconnect()
        return output
    except NetmikoAuthenticationException:
        return "ERROR: Authentication failed"
    except NetmikoTimeoutException:
        return "ERROR: Connection timed out"
    except Exception as e:
        return f"ERROR: {str(e)}"

def get_port_status(ip=None, username=None, password=None, brand=None):
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    device_type = get_netmiko_device_type(brand)
    command = SWITCH_COMMANDS.get(device_type, SWITCH_COMMANDS["hp_comware"])["show_ports"]
    output = run_switch_command(ip, username, password, brand, command)

    if output.startswith("ERROR"):
        return {"error": output}

    if "comware" in device_type or "h3c" in (brand or "").lower():
        return parse_ports_h3c(output)
    elif "cisco" in device_type:
        return parse_ports_cisco(output)
    else:
        return parse_ports_h3c(output)

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
            parts[0].startswith('Twenty') or
            parts[0].startswith('Hundred') or
            parts[0].startswith('Eth')
        ):
            interface = parts[0]
            if parts[0].startswith('Twenty-FiveGigE'):
                interface = parts[0].replace('Twenty-FiveGigE', 'WGE')
            elif parts[0].startswith('HundredGigE'):
                interface = parts[0].replace('HundredGigE', 'HGE')
            ports.append({
                "interface": interface,
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

def get_mac_table(ip=None, username=None, password=None, brand=None):
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    device_type = get_netmiko_device_type(brand)
    command = SWITCH_COMMANDS.get(device_type, SWITCH_COMMANDS["hp_comware"])["show_mac"]
    output = run_switch_command(ip, username, password, brand, command)

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

def get_vlans(ip=None, username=None, password=None, brand=None):
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    device_type = get_netmiko_device_type(brand)
    command = SWITCH_COMMANDS.get(device_type, SWITCH_COMMANDS["hp_comware"])["show_vlan"]
    output = run_switch_command(ip, username, password, brand, command)

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

def get_port_raw_config(interface, ip=None, username=None, password=None, brand=None):
    """Get raw configuration of a specific port from switch"""
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    device_type = get_netmiko_device_type(brand)
    full_interface = get_full_interface_name(interface, device_type)
    command_template = SWITCH_COMMANDS.get(device_type, SWITCH_COMMANDS["hp_comware"])["show_port_config"]
    command = command_template.format(interface=full_interface)
    output = run_switch_command(ip, username, password, brand, command)

    if output.startswith("ERROR"):
        return {"error": output}

    return {
        "interface": interface,
        "full_interface": full_interface,
        "raw_config": output.strip(),
        "brand": brand,
        "device_type": device_type
    }

def get_port_details(interface, ip=None, username=None, password=None, brand=None):
    """Get parsed details of a specific port"""
    ports = get_port_status(ip=ip, username=username, password=password, brand=brand)
    if isinstance(ports, dict) and "error" in ports:
        return ports
    for port in ports:
        if port["interface"] == interface:
            return port
    return {"interface": interface, "status": "Unknown", "speed": "Unknown", "vlan": "Unknown"}

def configure_port_vlan(interface, vlan_id, ip=None, username=None, password=None, brand=None):
    """Configure switch port VLAN via Netmiko"""
    if not ip:
        switches = get_switches_from_db()
        if not switches:
            return {"success": False, "error": "No switches in database"}
        sw = switches[0]
        ip = sw["ip"]
        username = sw["username"]
        password = sw["password"]
        brand = sw.get("brand", "unknown")

    device_type = get_netmiko_device_type(brand)
    full_interface = get_full_interface_name(interface, device_type)

    try:
        connection = connect_switch(ip, username, password, brand)

        if "comware" in device_type:
            commands = [
                "system-view",
                f"interface {full_interface}",
                "port link-type access",
                f"port access vlan {vlan_id}",
                "quit",
                "quit",
                "save force"
            ]
        elif "cisco" in device_type:
            commands = [
                "configure terminal",
                f"interface {full_interface}",
                "switchport mode access",
                f"switchport access vlan {vlan_id}",
                "end",
                "write memory"
            ]
        elif "juniper" in device_type:
            commands = [
                f"set vlans vlan{vlan_id} interface {full_interface}",
                "commit"
            ]
        else:
            commands = [
                "system-view",
                f"interface {full_interface}",
                f"port access vlan {vlan_id}",
                "quit",
                "quit",
                "save force"
            ]

        output = connection.send_config_set(commands)
        connection.disconnect()

        time.sleep(2)
        verify = get_port_details(interface, ip=ip, username=username, password=password, brand=brand)
        verified = str(vlan_id) == str(verify.get("vlan", ""))

        return {
            "success": True,
            "interface": interface,
            "vlan_id": vlan_id,
            "verified": verified,
            "output": output[:300]
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

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
    print("Testing switches from database using Netmiko...")
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