import threading
import time
import sqlite3
import os
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, "nexdeploy.db")

# Store previous states for comparison
_previous_states = {
    "servers": {},
    "switch_ports": {}
}

def log_event(action, details, level="info"):
    """Write event to audit_log"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "INSERT INTO audit_log (username, action, details, timestamp) VALUES (?, ?, ?, ?)",
            ("SYSTEM", action, details, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Event log error: {e}")

def check_server_changes():
    """Detect server power state and health changes"""
    try:
        from ilo import get_all_servers_status
        current = get_all_servers_status()

        for key, server in current.items():
            if "error" in server:
                continue

            name = server.get("name", key)
            power = server.get("power_state", "Unknown")
            health = server.get("overall_health", "Unknown")
            prev = _previous_states["servers"].get(key, {})

            # Detect power state change
            if prev and prev.get("power_state") != power:
                log_event(
                    "SERVER_POWER_CHANGE",
                    f"{name} power state changed: {prev.get('power_state')} → {power}"
                )
                print(f"[EVENT] {name} power: {prev.get('power_state')} → {power}")

            # Detect health change
            if prev and prev.get("overall_health") != health:
                log_event(
                    "SERVER_HEALTH_CHANGE",
                    f"{name} health changed: {prev.get('overall_health')} → {health}"
                )
                print(f"[EVENT] {name} health: {prev.get('overall_health')} → {health}")

            # Detect individual component changes
            prev_hd = prev.get("health_details", {})
            curr_hd = server.get("health_details", {})
            for component, status in curr_hd.items():
                prev_status = prev_hd.get(component)
                if prev_status and prev_status != status:
                    log_event(
                        "SERVER_COMPONENT_CHANGE",
                        f"{name} {component} changed: {prev_status} → {status}"
                    )
                    print(f"[EVENT] {name} {component}: {prev_status} → {status}")

            # Update previous state
            _previous_states["servers"][key] = {
                "power_state": power,
                "overall_health": health,
                "health_details": curr_hd
            }

    except Exception as e:
        print(f"Server check error: {e}")

def check_switch_changes():
    """Detect switch port up/down changes"""
    try:
        from switch import get_switch_summary
        current = get_switch_summary()

        if "error" in current:
            return

        # Build current port states
        current_ports = {}
        for port in current.get("up_ports", []):
            current_ports[port["interface"]] = "UP"
        for port in current.get("down_ports", []):
            current_ports[port["interface"]] = "DOWN"

        prev_ports = _previous_states["switch_ports"]

        if prev_ports:
            for interface, status in current_ports.items():
                prev_status = prev_ports.get(interface)
                if prev_status and prev_status != status:
                    action = "PORT_UP" if status == "UP" else "PORT_DOWN"
                    log_event(
                        action,
                        f"Switch port {interface} changed: {prev_status} → {status}"
                    )
                    print(f"[EVENT] Port {interface}: {prev_status} → {status}")

            # Detect new ports
            for interface in current_ports:
                if interface not in prev_ports:
                    log_event(
                        "PORT_DETECTED",
                        f"New port detected: {interface} ({current_ports[interface]})"
                    )

        _previous_states["switch_ports"] = current_ports

    except Exception as e:
        print(f"Switch check error: {e}")

def check_new_devices():
    """Detect new devices on network via ARP"""
    try:
        from discovery import arp_scan
        from database import get_devices

        arp_devices = arp_scan()
        if isinstance(arp_devices, dict):
            return

        db_devices = get_devices()
        db_ips = {d["ip"] for d in db_devices}

        # Check for new unregistered devices
        prev_ips = _previous_states.get("known_ips", set())
        current_ips = {d["ip"] for d in arp_devices}

        if prev_ips:
            new_ips = current_ips - prev_ips
            for ip in new_ips:
                if ip not in db_ips:
                    # Find MAC for this IP
                    mac = next((d["mac"] for d in arp_devices if d["ip"] == ip), "unknown")
                    log_event(
                        "NEW_DEVICE_DETECTED",
                        f"New device appeared on network: {ip} (MAC: {mac})"
                    )
                    print(f"[EVENT] New device detected: {ip}")

            gone_ips = prev_ips - current_ips
            for ip in gone_ips:
                if ip in db_ips:
                    log_event(
                        "DEVICE_OFFLINE",
                        f"Registered device went offline: {ip}"
                    )
                    print(f"[EVENT] Device offline: {ip}")

        _previous_states["known_ips"] = current_ips

    except Exception as e:
        print(f"Device check error: {e}")

def event_monitor():
    """Main monitoring loop — runs in background"""
    print("[EventMonitor] Starting...")
    # Wait for app to fully start
    time.sleep(15)
    print("[EventMonitor] Running first check...")

    while True:
        try:
            check_server_changes()
            time.sleep(10)
            check_switch_changes()
            time.sleep(10)
            check_new_devices()
            time.sleep(10)
        except Exception as e:
            print(f"[EventMonitor] Error: {e}")
            time.sleep(30)

def start_event_monitor():
    """Start the event monitor in background thread"""
    thread = threading.Thread(target=event_monitor, daemon=True)
    thread.start()
    print("[EventMonitor] Background thread started")
    return thread