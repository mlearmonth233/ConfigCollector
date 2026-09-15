"""Per-org device-naming rules: the rule engine itself, the rules API, and
that device creation / detection / device-roles all follow an org's own
convention instead of the built-in one."""

import pytest
from httpx import AsyncClient

from app.services.hostname_detection import BUILTIN_RULES, MatchMode, Rule, detect, device_type_for_role, roles_for_rules, validate_rule

pytestmark = pytest.mark.asyncio


# --- engine ----------------------------------------------------------------------------

DENVER_RULES = (
    Rule("-core-", device_role="core_switch", device_type="cisco_nxos"),
    Rule("-acc-", device_role="access_switch", device_type="cisco_ios"),
    Rule("^den-", MatchMode.REGEX, device_type="cisco_ios"),
    Rule("plant", MatchMode.PREFIX, device_role="ot_switch", role_label="Plant switch"),
    Rule("-fw", MatchMode.SUFFIX, device_role="firewall", device_type="fortinet"),
    Rule("-wan", device_role="wan_edge", role_label="WAN edge router", device_type="cisco_ios"),
)


def test_custom_rules_replace_the_builtin_convention():
    result = detect("den-core-sw01", DENVER_RULES)
    assert (result.device_role, result.suggested_device_type) == ("core_switch", "cisco_nxos")
    # The built-in "SWC" convention no longer applies once an org has rules...
    assert detect("GBGYSP01SWC001", DENVER_RULES).device_role is None
    # ...but still does when it has none.
    assert detect("GBGYSP01SWC001").device_role == "core_switch"
    assert detect("GBGYSP01SWC001", ()).device_role == "core_switch"


def test_match_modes():
    assert detect("plant2-sw-03", DENVER_RULES).device_role == "ot_switch"  # prefix
    assert detect("my-plant-sw-03", DENVER_RULES).device_role is None  # prefix doesn't match mid-name
    assert detect("edge-fw", DENVER_RULES).device_role == "firewall"  # suffix
    assert detect("edge-fw-2", DENVER_RULES).device_role is None
    assert detect("DEN-SW77", DENVER_RULES).suggested_device_type == "cisco_ios"  # regex, case-insensitive


def test_first_matching_rule_wins_independently_for_role_and_type():
    rules = (
        Rule("core", device_role="core_switch"),  # names no device type
        Rule("sw", device_role="access_switch", device_type="cisco_ios"),  # also matches, later
    )
    result = detect("den-core-sw1", rules)
    assert result.device_role == "core_switch"  # first role rule
    assert result.suggested_device_type == "cisco_ios"  # first rule that names a type


def test_custom_role_gets_a_label_and_shows_in_roles_list():
    result = detect("br07-wan-1", DENVER_RULES)
    assert result.device_role == "wan_edge" and result.device_role_label == "WAN edge router"
    roles = roles_for_rules(DENVER_RULES)
    assert roles["wan_edge"] == "WAN edge router"
    assert roles["core_switch"] == "Core switch"  # built-ins keep their labels
    # An unlabelled custom role is prettified from its key.
    assert roles_for_rules((Rule("x", device_role="oob_mgmt"),))["oob_mgmt"] == "Oob mgmt"


def test_device_type_for_role_follows_the_rules():
    assert device_type_for_role("core_switch", DENVER_RULES) == "cisco_nxos"
    assert device_type_for_role("core_switch") == "cisco_ios"  # built-in
    assert device_type_for_role("wan_edge") is None  # unknown to the built-ins


def test_builtin_rules_reproduce_the_original_convention():
    result = detect("GBGYSP01SWA001", BUILTIN_RULES)
    assert (result.device_role, result.suggested_device_type) == ("access_switch", "cisco_ios")
    assert detect("GBGYO01WLC001").suggested_device_type is None


@pytest.mark.parametrize(
    ("rule", "fragment"),
    [
        (Rule("   "), "needs a pattern"),
        (Rule("[unclosed", MatchMode.REGEX, device_role="x"), "not a valid regular expression"),
        (Rule("SWA"), "sets nothing"),
        (Rule("SWA", device_role="Bad Role!"), "lowercase"),
    ],
)
def test_validate_rule(rule, fragment):
    with pytest.raises(ValueError, match=fragment):
        validate_rule(rule)


# --- API ----------------------------------------------------------------------------------


async def _register(client: AsyncClient, email: str, org_name: str = "RulesOrg") -> str:
    resp = await client.post("/api/auth/register", json={"org_name": org_name, "email": email, "password": "password123"})
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


DENVER_PAYLOAD = {
    "rules": [
        {"pattern": "-core-", "match_mode": "contains", "device_role": "core_switch", "device_type": "cisco_nxos"},
        {"pattern": "-acc-", "match_mode": "contains", "device_role": "access_switch", "device_type": "cisco_ios"},
        {"pattern": "^den-", "match_mode": "regex", "device_type": "cisco_ios"},
        {"pattern": "plant", "match_mode": "prefix", "device_role": "ot_switch", "role_label": "Plant switch"},
        {"pattern": "-wan", "match_mode": "contains", "device_role": "WAN Edge", "role_label": "WAN edge router", "device_type": "cisco_ios"},
    ]
}


async def test_rules_default_to_builtin_and_can_be_replaced_and_reset(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)

    initial = await client.get("/api/hostname-rules", headers=_auth(token))
    assert initial.status_code == 200
    assert initial.json()["using_builtin"] is True
    assert initial.json()["rules"][0]["pattern"] == "SWA"
    assert len(initial.json()["rules"]) == len(BUILTIN_RULES)

    saved = await client.put("/api/hostname-rules", headers=_auth(token), json=DENVER_PAYLOAD)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["using_builtin"] is False
    assert [r["pattern"] for r in body["rules"]] == ["-core-", "-acc-", "^den-", "plant", "-wan"]
    assert body["rules"][4]["device_role"] == "wan_edge"  # slugified

    # Detection now follows the org's convention.
    detected = await client.get("/api/devices/detect", headers=_auth(token), params={"name": "den-core-sw01"})
    assert detected.json() == {
        "device_role": "core_switch",
        "device_role_label": "Core switch",
        "suggested_device_type": "cisco_nxos",
    }
    assert (await client.get("/api/devices/detect", headers=_auth(token), params={"name": "GBGYSP01SWA001"})).json()["device_role"] is None

    # The custom role is offered in the roles list.
    roles = {r["key"]: r["label"] for r in (await client.get("/api/device-roles", headers=_auth(token))).json()}
    assert roles["wan_edge"] == "WAN edge router" and roles["core_switch"] == "Core switch"

    # Saving an empty list goes back to the built-ins.
    reset = await client.put("/api/hostname-rules", headers=_auth(token), json={"rules": []})
    assert reset.json()["using_builtin"] is True
    assert (await client.get("/api/devices/detect", headers=_auth(token), params={"name": "GBGYSP01SWA001"})).json()["device_role"] == "access_switch"


async def test_rules_are_per_org(client: AsyncClient, unique_email):
    token_a = await _register(client, unique_email)
    await client.put("/api/hostname-rules", headers=_auth(token_a), json=DENVER_PAYLOAD)
    token_b = await _register(client, f"other-{unique_email}", org_name="OtherRulesOrg")
    assert (await client.get("/api/hostname-rules", headers=_auth(token_b))).json()["using_builtin"] is True


@pytest.mark.parametrize(
    ("rule", "fragment"),
    [
        ({"pattern": "[bad", "match_mode": "regex", "device_role": "x"}, "not a valid regular expression"),
        ({"pattern": "SWA"}, "sets nothing"),
        ({"pattern": "SWA", "device_role": "core_switch", "device_type": "no_such_type"}, "unknown device type"),
    ],
)
async def test_invalid_rules_are_rejected_with_their_position(client: AsyncClient, unique_email, rule, fragment):
    token = await _register(client, unique_email)
    resp = await client.put(
        "/api/hostname-rules",
        headers=_auth(token),
        json={"rules": [{"pattern": "ok", "device_role": "core_switch"}, rule]},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"].startswith("Rule 2:")
    assert fragment in resp.json()["detail"]


async def test_non_admin_cannot_change_rules(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    member_email = f"member-{unique_email}"
    await client.post("/api/users", headers=_auth(token), json={"email": member_email, "password": "password123"})
    member = (await client.post("/api/auth/login", json={"email": member_email, "password": "password123"})).json()["access_token"]
    assert (await client.put("/api/hostname-rules", headers=_auth(member), json=DENVER_PAYLOAD)).status_code == 403
    assert (await client.get("/api/hostname-rules", headers=_auth(member))).status_code == 200


async def test_test_endpoint_uses_saved_rules_or_a_draft(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    # Saved (built-in) rules:
    saved = await client.post("/api/hostname-rules/test", headers=_auth(token), json={"name": "GBGYSP01SWA001"})
    assert saved.json()["device_role"] == "access_switch"
    # An unsaved draft from the editor:
    draft = await client.post(
        "/api/hostname-rules/test", headers=_auth(token), json={"name": "den-core-sw01", **DENVER_PAYLOAD}
    )
    assert draft.json()["suggested_device_type"] == "cisco_nxos"
    # ...and the draft didn't get saved.
    assert (await client.get("/api/hostname-rules", headers=_auth(token))).json()["using_builtin"] is True


async def test_device_creation_follows_org_rules_including_custom_device_types(client: AsyncClient, unique_email):
    token = await _register(client, unique_email)
    await client.post(
        "/api/custom-device-types",
        headers=_auth(token),
        json={"key": "arista_eos", "label": "Arista EOS", "category": "switch", "netmiko_driver": "arista_eos", "default_commands": ["show running-config"]},
    )
    saved = await client.put(
        "/api/hostname-rules",
        headers=_auth(token),
        json={"rules": [{"pattern": "-leaf-", "device_role": "access_switch", "device_type": "arista_eos"}, {"pattern": "^dc", "match_mode": "regex", "device_type": "arista_eos"}]},
    )
    assert saved.status_code == 200, saved.text

    device = await client.post("/api/devices", headers=_auth(token), json={"name": "dc1-leaf-07", "host": "192.0.2.7"})
    assert device.status_code == 201, device.text
    assert device.json()["device_type"] == "arista_eos"
    assert device.json()["device_role"] == "access_switch"

    # A role given explicitly still maps to a type via the org's rules.
    device2 = await client.post(
        "/api/devices", headers=_auth(token), json={"name": "mystery-box", "host": "192.0.2.8", "device_role": "access_switch"}
    )
    assert device2.status_code == 201, device2.text
    assert device2.json()["device_type"] == "arista_eos"
