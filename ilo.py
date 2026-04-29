import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ILO_SERVERS = {
    "server1": {
        "ip": "192.168.99.104",
        "username": "cdfadmin",
        "password": "QweAsd!23cdf",
        "name": "S2D-Node1"
    },
    "server2": {
        "ip": "192.168.99.102",
        "username": "cdfadmin",
        "password": "QweAsd!23cdf",
        "name": "Server 2"
    }
}

def get_server_info(server_key):
    server = ILO_SERVERS.get(server_key)
    if not server:
        return {"error": f"Server {server_key} not found"}

    try:
        response = requests.get(
            f"https://{server['ip']}/redfish/v1/Systems/1",
            auth=(server['username'], server['password']),
            verify=False,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            health = data.get("Oem", {}).get("Hpe", {}).get("AggregateHealthStatus", {})

            return {
                "name": data.get("HostName", "Unknown"),
                "model": data.get("Model", "Unknown"),
                "power_state": data.get("PowerState", "Unknown"),
                "overall_health": data.get("Status", {}).get("Health", "Unknown"),
                "ram_gb": data.get("MemorySummary", {}).get("TotalSystemMemoryGiB", 0),
                "cpu_model": data.get("ProcessorSummary", {}).get("Model", "Unknown"),
                "cpu_count": data.get("ProcessorSummary", {}).get("Count", 0),
                "bios_version": data.get("BiosVersion", "Unknown"),
                "serial": data.get("SerialNumber", "Unknown"),
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
        else:
            return {"error": f"HTTP {response.status_code}"}

    except Exception as e:
        return {"error": str(e)}

def get_power_state(server_key):
    info = get_server_info(server_key)
    return info.get("power_state", "Unknown")

def power_action(server_key, action):
    server = ILO_SERVERS.get(server_key)
    if not server:
        return {"error": "Server not found"}

    allowed = ["On", "ForceOff", "GracefulShutdown", "GracefulRestart", "ForceRestart"]
    if action not in allowed:
        return {"error": f"Invalid action. Allowed: {allowed}"}

    try:
        response = requests.post(
            f"https://{server['ip']}/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
            auth=(server['username'], server['password']),
            verify=False,
            timeout=10,
            json={"ResetType": action}
        )
        if response.status_code in [200, 204]:
            return {"success": True, "action": action, "server": server_key}
        else:
            return {"error": f"HTTP {response.status_code}: {response.text}"}
    except Exception as e:
        return {"error": str(e)}

def get_all_servers_status():
    results = {}
    for key in ILO_SERVERS:
        results[key] = get_server_info(key)
    return results

if __name__ == "__main__":
    print("Testing iLO connections...\n")
    for key, server in ILO_SERVERS.items():
        print(f"Checking {server['name']} ({server['ip']})...")
        info = get_server_info(key)
        if "error" in info:
            print(f"  ERROR: {info['error']}")
        else:
            print(f"  Model: {info['model']}")
            print(f"  Power: {info['power_state']}")
            print(f"  Health: {info['overall_health']}")
            print(f"  RAM: {info['ram_gb']}GB")
            print(f"  Health Details:")
            for k, v in info['health_details'].items():
                icon = "✓" if v == "OK" else "✗"
                print(f"    {icon} {k.replace('_',' ').title()}: {v}")
        print()
