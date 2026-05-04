import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_ilo_servers():
    try:
        from database import get_devices
        devices = get_devices(device_type="ilo")
        servers = {}
        for i, d in enumerate(devices):
            key = f"server{i+1}"
            servers[key] = {
                "ip": d["ip"],
                "username": d["username"],
                "password": d["password"],
                "name": d["name"],
                "brand": d.get("brand", "Unknown"),
                "model": d.get("model", "Unknown")
            }
        return servers
    except Exception as e:
        return {}

def get_server_info(server_key=None, ip=None, username=None, password=None):
    if ip and username and password:
        target = {"ip": ip, "username": username, "password": password, "name": ip}
    else:
        servers = get_ilo_servers()
        target = servers.get(server_key)
        if not target:
            return {"error": f"Server {server_key} not found in database"}
    try:
        response = requests.get(
            f"https://{target['ip']}/redfish/v1/Systems/1",
            auth=(target["username"], target["password"]),
            verify=False,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            health = data.get("Oem", {}).get("Hpe", {}).get("AggregateHealthStatus", {})
            manufacturer = data.get("Manufacturer", "Unknown")
            if "HPE" in manufacturer or "Hewlett" in manufacturer:
                brand = "HPE"
            elif "Dell" in manufacturer:
                brand = "Dell"
            elif "H3C" in manufacturer:
                brand = "H3C"
            else:
                brand = manufacturer
            return {
                "name": data.get("HostName", target.get("name", "Unknown")),
                "model": data.get("Model", "Unknown"),
                "brand": brand,
                "manufacturer": manufacturer,
                "power_state": data.get("PowerState", "Unknown"),
                "overall_health": data.get("Status", {}).get("Health", "Unknown"),
                "ram_gb": data.get("MemorySummary", {}).get("TotalSystemMemoryGiB", 0),
                "cpu_model": data.get("ProcessorSummary", {}).get("Model", "Unknown"),
                "cpu_count": data.get("ProcessorSummary", {}).get("Count", 0),
                "bios_version": data.get("BiosVersion", "Unknown"),
                "serial": data.get("SerialNumber", "Unknown"),
                "ilo_ip": target["ip"],
                "health_details": {
                    "memory": health.get("Memory", {}).get("Status", {}).get("Health", "Unknown"),
                    "storage": health.get("Storage", {}).get("Status", {}).get("Health", "Unknown"),
                    "network": health.get("Network", {}).get("Status", {}).get("Health", "Unknown"),
                    "power_supply": health.get("PowerSupplies", {}).get("Status", {}).get("Health", "Unknown"),
                    "fans": health.get("Fans", {}).get("Status", {}).get("Health", "Unknown"),
                    "temperatures": health.get("Temperatures", {}).get("Status", {}).get("Health", "Unknown"),
                    "processors": health.get("Processors", {}).get("Status", {}).get("Health", "Unknown"),
                }
            }
        return {"error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

def get_all_servers_status():
    servers = get_ilo_servers()
    if not servers:
        return {}
    results = {}
    for key in servers:
        results[key] = get_server_info(server_key=key)
    return results

def power_action(server_key, action):
    servers = get_ilo_servers()
    server = servers.get(server_key)
    if not server:
        return {"error": "Server not found in database"}
    allowed = ["On", "ForceOff", "GracefulShutdown", "GracefulRestart", "ForceRestart"]
    if action not in allowed:
        return {"error": f"Invalid action. Allowed: {allowed}"}
    try:
        response = requests.post(
            f"https://{server['ip']}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            auth=(server["username"], server["password"]),
            verify=False,
            timeout=10,
            json={"ResetType": action}
        )
        if response.status_code in [200, 204]:
            return {"success": True, "action": action}
        return {"error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    print("Testing iLO from database...")
    servers = get_ilo_servers()
    if not servers:
        print("No iLO devices in database. Add via Settings page.")
    else:
        for key, server in servers.items():
            print(f"\nChecking {server['name']} ({server['ip']})...")
            info = get_server_info(server_key=key)
            if "error" in info:
                print(f"  ERROR: {info['error']}")
            else:
                print(f"  Power: {info['power_state']}")
                print(f"  Health: {info['overall_health']}")
                print(f"  Model: {info['model']}")
                print(f"  Brand: {info['brand']}")
                print(f"  RAM: {info['ram_gb']}GB")
                print(f"  CPU: {info['cpu_model']} x{info['cpu_count']}")
                print(f"  Serial: {info['serial']}")
                print(f"  Health Details:")
                for k, v in info['health_details'].items():
                    icon = "OK" if v == "OK" else "ALERT"
                    print(f"    {k}: {icon} ({v})")