SWITCH_COMMANDS = {
    "hp_comware": {
        "show_vlan": "display vlan all",
        "show_full_config": "display current-configuration",
        "show_interface": "display current-configuration interface {interface}",
        "show_mac_table": "display mac-address",
        "show_lacp": "display lacp summary",
        "show_port_status": "display interface brief",
        "set_vlan": [
            "vlan {vlan_id}",
            "interface {interface}",
            "port access vlan {vlan_id}",
        ],
        "verify_vlan": "display current-configuration interface {interface}",
        "set_trunk": [
            "vlan {vlans}",
            "interface {interface}",
            "port link-type trunk",
            "port trunk permit vlan {vlans}",
        ],
        "verify_trunk": "display current-configuration interface {interface}",
    },
    "cisco_ios": {
        "show_vlan": "show vlan brief",
        "show_full_config": "show running-config",
        "show_interface": "show running-config interface {interface}",
        "show_mac_table": "show mac address-table",
        "show_lacp": "show lacp summary",
        "show_port_status": "show interface status",
        "set_vlan": [
            "configure terminal",
            "interface {interface}",
            "switchport access vlan {vlan_id}",
            "end",
        ],
        "verify_vlan": "show running-config interface {interface}",
    },
    "cisco_nxos": {
        "show_vlan": "show vlan brief",
        "show_full_config": "show running-config",
        "show_interface": "show running-config interface {interface}",
        "show_mac_table": "show mac address-table",
        "show_lacp": "show lacp summary",
        "show_port_status": "show interface status",
        "set_vlan": [
            "configure terminal",
            "interface {interface}",
            "switchport access vlan {vlan_id}",
            "end",
        ],
        "verify_vlan": "show running-config interface {interface}",
    },
    "juniper_junos": {
        "show_vlan": "show vlans",
        "show_full_config": "show configuration",
        "show_interface": "show configuration interfaces {interface}",
        "show_mac_table": "show ethernet-switching table",
        "show_lacp": "show lacp interfaces",
        "show_port_status": "show interfaces terse",
        "set_vlan": [
            "configure",
            "set interfaces {interface} unit 0 family ethernet-switching vlan members {vlan_id}",
            "commit",
        ],
        "verify_vlan": "show configuration interfaces {interface}",
    },
}


def get_command(brand: str, operation: str, **kwargs):
    """Return command string or list for brand+operation. Raises KeyError if not found."""
    brand_commands = SWITCH_COMMANDS.get(brand)
    if not brand_commands:
        raise KeyError(f"Brand '{brand}' not in command map")
    cmd = brand_commands.get(operation)
    if cmd is None:
        raise KeyError(f"Operation '{operation}' not defined for brand '{brand}'")
    if isinstance(cmd, list):
        return [c.format(**kwargs) for c in cmd]
    return cmd.format(**kwargs)


def get_supported_brands() -> list:
    return list(SWITCH_COMMANDS.keys())
