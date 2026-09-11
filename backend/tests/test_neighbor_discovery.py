from app.services.neighbor_discovery import extract_neighbors, has_neighbor_command, normalize_device_name

_CDP_DETAIL_OUTPUT = """\
-------------------------
Device ID: SW02.corp.local
Entry address(es):
  IP address: 10.0.0.2
Platform: cisco WS-C3850-24P,  Capabilities: Switch IGMP
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/1
Holdtime : 163 sec

Version :
Cisco IOS Software, IOS-XE Software

advertisement version: 2
-------------------------
Device ID: AP-01
Entry address(es):
  IP address: 10.0.0.50
Platform: cisco AIR-AP,  Capabilities: Trans-Bridge
Interface: GigabitEthernet1/0/2,  Port ID (outgoing port): GigabitEthernet0
Holdtime : 130 sec

Total cdp entries displayed : 2
"""

_LLDP_DETAIL_OUTPUT = """\
------------------------------------------------
Local Intf: Gi1/0/2
Chassis id: aabb.ccdd.eeff
Port id: Gi1/0/1
Port Description: GigabitEthernet1/0/1
System Name: SW03.corp.local

System Description:
Cisco IOS Software

Time remaining: 112 seconds
System Capabilities: B
Enabled Capabilities: B
Management Addresses:
    IP: 10.0.0.3
------------------------------------------------
Total entries displayed: 1
"""


def _wrap_snapshot(command: str, output: str) -> str:
    return f"! ---- {command} ----\n{output}"


def test_extract_neighbors_from_cdp_detail():
    content = _wrap_snapshot("show cdp neighbor detail", _CDP_DETAIL_OUTPUT)
    neighbors = extract_neighbors(content)
    assert len(neighbors) == 2
    assert neighbors[0].name == "SW02.corp.local"
    assert neighbors[0].ip == "10.0.0.2"
    assert neighbors[0].protocol == "cdp"
    assert neighbors[1].name == "AP-01"
    assert neighbors[1].ip == "10.0.0.50"


def test_extract_neighbors_from_lldp_detail():
    content = _wrap_snapshot("sh lldp nei detail", _LLDP_DETAIL_OUTPUT)
    neighbors = extract_neighbors(content)
    assert len(neighbors) == 1
    assert neighbors[0].name == "SW03.corp.local"
    assert neighbors[0].ip == "10.0.0.3"
    assert neighbors[0].protocol == "lldp"


def test_extract_neighbors_combines_multiple_command_blocks():
    content = "\n".join(
        [
            _wrap_snapshot("term len 0", "ok"),
            _wrap_snapshot("show cdp neighbor detail", _CDP_DETAIL_OUTPUT),
            _wrap_snapshot("show run", "hostname core-sw1\n! nothing cdp/lldp related here"),
            _wrap_snapshot("sh lldp nei detail", _LLDP_DETAIL_OUTPUT),
        ]
    )
    neighbors = extract_neighbors(content)
    assert {n.name for n in neighbors} == {"SW02.corp.local", "AP-01", "SW03.corp.local"}


def test_extract_neighbors_ignores_non_detail_summary_commands():
    # The plain (non-"detail") summary table isn't parsed - column-aligned
    # output wraps unpredictably for long device names, so this only
    # trusts the one-neighbor-per-block "detail" variants.
    summary_output = (
        "Device ID        Local Intrfce     Holdtme    Capability  Platform  Port ID\n"
        "SW02.corp.local  Gig 1/0/1         163              S I   WS-C3850  Gig 1/0/1\n"
    )
    content = _wrap_snapshot("show cdp neighbor", summary_output)
    assert extract_neighbors(content) == []


def test_extract_neighbors_empty_when_no_cdp_lldp_commands_ran():
    content = _wrap_snapshot("show running-config", "hostname sw1\ninterface Gi1\n")
    assert extract_neighbors(content) == []


def test_has_neighbor_command_true_when_a_detail_command_ran():
    content = _wrap_snapshot("show cdp neighbor detail", _CDP_DETAIL_OUTPUT)
    assert has_neighbor_command(content) is True


def test_has_neighbor_command_false_without_a_detail_command():
    content = _wrap_snapshot("show running-config", "hostname sw1\n")
    assert has_neighbor_command(content) is False


def test_has_neighbor_command_true_even_with_zero_neighbors_found():
    # A device with no CDP neighbors right now still ran the command - that
    # should count as "checked", just with nothing missing to report.
    content = _wrap_snapshot("show cdp neighbor detail", "Total cdp entries displayed : 0\n")
    assert has_neighbor_command(content) is True
    assert extract_neighbors(content) == []


def test_normalize_device_name_strips_domain_and_uppercases():
    assert normalize_device_name("sw02.corp.local") == "SW02"
    assert normalize_device_name("AP-01") == "AP-01"
    assert normalize_device_name("  SW03.corp.local  ") == "SW03"
