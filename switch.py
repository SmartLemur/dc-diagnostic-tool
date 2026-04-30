import paramiko
import time

SWITCH = {
    "ip": "192.168.99.5",
    "username": "admin",
    "password": "QweAsd!23cdf"
}

def get_switch_output(command, wait=3):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            SWITCH["ip"],
            username=SWITCH["username"],
            password=SWITCH["password"],
            timeout=10,
            disabled_algorithms={"pubkeys": ["rsa-sha2-256", "rsa-sha2-512"]}
        )
        channel = client.invoke_shell()
        time.sleep(2)
        # Clear initial banner
        if channel.recv_ready():
            channel.recv(65535)
        # Send command
        channel.send(command + "\n")
        time.sleep(wait)
        # Keep reading until no more data
        output = ""
        for _ in range(10):
            time.sleep(0.5)
            if channel.recv_ready():
                chunk = channel.recv(65535).decode('utf-8', errors='ignore')
                output += chunk
                # Send space if "More" prompt appears
                if "More" in chunk or "more" in chunk:
                    channel.send(" ")
                    time.sleep(1)
            else:
                break
        client.close()
        return output
    except Exception as e:
        return f"ERROR: {e}"

def get_port_status():
    output = get_switch_output("display interface brief", wait=4)
    if output.startswith("ERROR"):
        return {"error": output}

    ports = []
    bridge_section = False
    for line in output.split('\n'):
        line = line.strip()
        if 'bridge mode' in line.lower():
            bridge_section = True
            continue
        if not bridge_section:
            continue
        # Skip header lines
        if any(h in line for h in ['Link:', 'Speed:', 'Duplex:', 'Type:', 'Interface']):
            continue
        parts = line.split()
        if len(parts) >= 5 and (parts[0].startswith('WGE') or parts[0].startswith('HGE') or parts[0].startswith('GE') or parts[0].startswith('XGE')):
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

def get_mac_table():
    output = get_switch_output("display mac-address", wait=3)
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

def get_switch_summary():
    ports = get_port_status()
    if isinstance(ports, dict) and "error" in ports:
        return ports
    macs = get_mac_table()
    up_ports = [p for p in ports if p["status"] == "UP"]
    down_ports = [p for p in ports if p["status"] == "DOWN"]
    return {
        "total_ports": len(ports),
        "up_count": len(up_ports),
        "down_count": len(down_ports),
        "up_ports": up_ports,
        "down_ports": down_ports,
        "mac_table": macs if isinstance(macs, list) else []
    }

if __name__ == "__main__":
    print("Testing H3C switch connection...")
    print("\nRaw output sample:")
    output = get_switch_output("display interface brief", wait=4)
    print(output[:500])
    print("\n--- Parsed ports ---")
    summary = get_switch_summary()
    if "error" in summary:
        print(f"Error: {summary['error']}")
    else:
        print(f"Total: {summary['total_ports']} | UP: {summary['up_count']} | DOWN: {summary['down_count']}")
        print("\nActive ports:")
        for p in summary['up_ports']:
            print(f"  {p['interface']} — {p['speed']} — VLAN {p['vlan']}")
