import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def normalize_mac(mac):
    mac = mac.lower()
    mac = mac.replace('-', '').replace(':', '').replace('.', '')
    return ':'.join(mac[i:i+2] for i in range(0, 12, 2))

def build_topology():
    try:
        from discovery import arp_scan
        from switch import get_mac_table, get_port_status, get_switches_from_db
        from database import get_devices

        # Step 1 — ARP scan
        arp_devices = arp_scan()
        if isinstance(arp_devices, dict):
            return {"error": "ARP scan failed", "nodes": [], "connections": [], "switch_ports": []}

        # Build MAC to IP mapping
        mac_to_ip = {}
        ip_to_device = {}
        for d in arp_devices:
            normalized = normalize_mac(d["mac"])
            mac_to_ip[normalized] = d["ip"]
            ip_to_device[d["ip"]] = {
                "ip": d["ip"],
                "mac": normalized,
                "vendor": d.get("vendor", "Unknown"),
                "name": d["ip"],
                "type": "unknown",
                "brand": "Unknown"
            }

        # Step 2 — Enrich with database devices
        db_devices = get_devices()
        for d in db_devices:
            ip = d["ip"]
            if ip in ip_to_device:
                ip_to_device[ip].update({
                    "name": d["name"],
                    "type": d["type"],
                    "brand": d.get("brand", "Unknown"),
                    "model": d.get("model", "Unknown")
                })
            else:
                ip_to_device[ip] = {
                    "ip": ip,
                    "mac": "",
                    "vendor": d.get("brand", "Unknown"),
                    "name": d["name"],
                    "type": d["type"],
                    "brand": d.get("brand", "Unknown"),
                    "model": d.get("model", "Unknown")
                }

        # Step 3 — Get switch MAC table and port status
        port_to_macs = {}
        port_map = {}
        switches = get_switches_from_db()

        for sw in switches:
            try:
                mac_table = get_mac_table(
                    ip=sw["ip"],
                    username=sw["username"],
                    password=sw["password"],
                    brand=sw.get("brand", "unknown")
                )
                if isinstance(mac_table, list):
                    for entry in mac_table:
                        port = entry.get("type", "")
                        mac = normalize_mac(entry.get("mac", ""))
                        if port not in port_to_macs:
                            port_to_macs[port] = []
                        port_to_macs[port].append(mac)
            except:
                pass

            try:
                port_status_list = get_port_status(
                    ip=sw["ip"],
                    username=sw["username"],
                    password=sw["password"],
                    brand=sw.get("brand", "unknown")
                )
                if isinstance(port_status_list, list):
                    for p in port_status_list:
                        port_map[p["interface"]] = p
            except:
                pass

        # Step 4 — Build nodes
        nodes = []
        for ip, device in ip_to_device.items():
            nodes.append({
                "id": ip,
                "ip": ip,
                "mac": device.get("mac", ""),
                "name": device.get("name", ip),
                "type": device.get("type", "unknown"),
                "brand": device.get("brand", "Unknown"),
                "model": device.get("model", "Unknown"),
                "status": "up"
            })

        # Step 5 — Build switch ports with connected devices
        switch_ports = []
        for port, status in port_map.items():
            macs_on_port = port_to_macs.get(port, [])
            devices_on_port = []
            for mac in macs_on_port:
                ip = mac_to_ip.get(mac, "")
                if ip and ip in ip_to_device:
                    devices_on_port.append(ip_to_device[ip])
            switch_ports.append({
                "port": port,
                "status": status.get("status", "DOWN"),
                "speed": status.get("speed", ""),
                "vlan": status.get("vlan", "1"),
                "devices": devices_on_port
            })

        # Step 6 — Build connections
        connections = []
        for sw in switches:
            sw_ip = sw["ip"]
            for ip in ip_to_device:
                if ip != sw_ip:
                    connections.append({
                        "from": sw_ip,
                        "to": ip,
                        "status": "up"
                    })

        return {
            "nodes": nodes,
            "connections": connections,
            "switch_ports": switch_ports
        }

    except Exception as e:
        return {
            "error": str(e),
            "nodes": [],
            "connections": [],
            "switch_ports": []
        }

if __name__ == "__main__":
    print("Building topology from database...")
    result = build_topology()
    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"Nodes: {len(result['nodes'])}")
        print(f"Ports: {len(result['switch_ports'])}")
        for node in result['nodes']:
            print(f"  {node['name']} ({node['ip']}) — {node['type']} — {node['brand']}")