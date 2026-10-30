import pytest

from app.services import dns_check

pytestmark = pytest.mark.asyncio


async def test_is_ip_address():
    assert dns_check._is_ip_address("10.0.0.1") is True
    assert dns_check._is_ip_address("::1") is True
    assert dns_check._is_ip_address("core-sw1") is False
    assert dns_check._is_ip_address("") is False


async def test_forward_lookup_succeeds_for_localhost():
    ok, ips = await dns_check._forward_lookup("localhost")
    assert ok is True
    assert "127.0.0.1" in ips or "::1" in ips


async def test_forward_lookup_fails_for_bogus_name():
    ok, ips = await dns_check._forward_lookup("this-host-does-not-exist.invalid")
    assert ok is False
    assert ips == []


async def test_run_dns_check_for_a_hostname_combines_ping_forward_reverse(monkeypatch):
    async def fake_ping(host: str) -> bool:
        assert host == "core-sw1"
        return True

    async def fake_forward(hostname: str):
        assert hostname == "core-sw1"
        return True, ["10.0.0.5"]

    async def fake_reverse(ip: str):
        assert ip == "10.0.0.5"
        return True, "core-sw1.example.com"

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)

    result = await dns_check.run_dns_check("core-sw1")
    assert result.target == "core-sw1"
    assert result.ping_ok is True
    assert result.forward_ok is True
    assert result.forward_ips == ["10.0.0.5"]
    assert result.reverse_ok is True
    assert result.reverse_hostname == "core-sw1.example.com"


async def test_run_dns_check_for_an_ip_reverses_then_forward_confirms_the_ptr_name(monkeypatch):
    async def fake_ping(host: str) -> bool:
        assert host == "10.0.0.5"
        return True

    async def fake_reverse(ip: str):
        assert ip == "10.0.0.5"
        return True, "core-sw1.example.com"

    looked_up = []

    async def fake_forward(hostname: str):
        looked_up.append(hostname)
        return True, ["10.0.0.5"]

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)

    result = await dns_check.run_dns_check("10.0.0.5")
    # The IP itself is never forward-resolved; the PTR name it returns is.
    assert looked_up == ["core-sw1.example.com"]
    assert result.forward_ok is True
    assert result.forward_ips == ["10.0.0.5"]
    assert result.reverse_ok is True
    assert result.reverse_hostname == "core-sw1.example.com"


async def test_run_dns_check_for_an_ip_without_ptr_does_no_forward_lookup(monkeypatch):
    async def fake_ping(host: str) -> bool:
        return False

    async def fake_reverse(ip: str):
        return False, None

    async def fake_forward(hostname: str):
        raise AssertionError("nothing to forward-confirm without a PTR record")

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)

    result = await dns_check.run_dns_check("10.0.0.9")
    assert (result.ping_ok, result.forward_ok, result.forward_ips, result.reverse_ok) == (False, False, [], False)


async def test_run_dns_check_skips_reverse_lookup_when_forward_lookup_fails(monkeypatch):
    reverse_called = False

    async def fake_ping(host: str) -> bool:
        return False

    async def fake_forward(hostname: str):
        return False, []

    async def fake_reverse(ip: str):
        nonlocal reverse_called
        reverse_called = True
        return True, "should-not-be-called"

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)
    monkeypatch.setattr(dns_check, "_reverse_lookup", fake_reverse)

    result = await dns_check.run_dns_check("no-such-host.invalid")
    assert result.reverse_ok is False
    assert result.reverse_hostname is None
    assert reverse_called is False


async def test_run_dns_checks_runs_every_target(monkeypatch):
    async def fake_ping(host: str) -> bool:
        return host == "up"

    async def fake_forward(hostname: str):
        return False, []

    monkeypatch.setattr(dns_check, "_ping", fake_ping)
    monkeypatch.setattr(dns_check, "_forward_lookup", fake_forward)

    results = await dns_check.run_dns_checks(["up", "10.0.0.9"])
    by_target = {r.target: r for r in results}
    assert by_target["up"].ping_ok is True
    assert by_target["10.0.0.9"].ping_ok is False


# --- subnet sweeps ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("10.1.1.0/24", "10.1.1.0/24"),
        ("  10.1.1.0/24 ", "10.1.1.0/24"),
        ("10.1.1.1/24", "10.1.1.0/24"),  # an interface address: host bits dropped
        ("10.1.1.0 255.255.255.0", "10.1.1.0/24"),
        ("10.1.1.0  255.255.255.192", "10.1.1.0/26"),
        ("10.1.1.0/255.255.254.0", "10.1.0.0/23"),
        ("192.0.2.7/32", "192.0.2.7/32"),
        ("2001:db8::/120", "2001:db8::/120"),
    ],
)
async def test_parse_subnet_accepts_the_usual_spellings(text, expected):
    assert str(dns_check.parse_subnet(text)) == expected


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "Enter a subnet"),
        ("10.1.1.0", "has no mask"),
        ("10.1.1.0/33", "not a valid subnet"),
        ("core-sw1/24", "not a valid subnet"),
        ("10.1.1.0/24 255.255.255.0", "is not a subnet"),
    ],
)
async def test_parse_subnet_explains_what_is_wrong(text, message):
    with pytest.raises(dns_check.SubnetError, match=message):
        dns_check.parse_subnet(text)


async def test_usable_hosts_skips_network_and_broadcast():
    hosts = dns_check.usable_hosts(dns_check.parse_subnet("10.1.1.0/24"))
    assert len(hosts) == 254 and hosts[0] == "10.1.1.1" and hosts[-1] == "10.1.1.254"
    assert dns_check.usable_hosts(dns_check.parse_subnet("10.1.1.0/30")) == ["10.1.1.1", "10.1.1.2"]
    # Point-to-point /31 and a single /32 keep every address.
    assert dns_check.usable_hosts(dns_check.parse_subnet("10.1.1.0/31")) == ["10.1.1.0", "10.1.1.1"]
    assert dns_check.usable_hosts(dns_check.parse_subnet("10.1.1.9/32")) == ["10.1.1.9"]
    assert len(dns_check.usable_hosts(dns_check.parse_subnet("2001:db8::/120"))) == 255


async def test_usable_hosts_refuses_a_subnet_too_big_to_sweep():
    assert len(dns_check.usable_hosts(dns_check.parse_subnet("10.0.0.0/19"))) == 8190
    with pytest.raises(dns_check.SubnetError, match=r"16,382 usable addresses.*largest IPv4 subnet one sweep covers is a /19"):
        dns_check.usable_hosts(dns_check.parse_subnet("10.0.0.0/18"))
    with pytest.raises(dns_check.SubnetError, match="more than"):
        dns_check.usable_hosts(dns_check.parse_subnet("2001:db8::/64"))


async def test_dns_matches_compares_the_ptr_name_with_the_address():
    assert dns_check.dns_matches("10.0.0.5", True, True, ["10.0.0.5"]) is True
    assert dns_check.dns_matches("10.0.0.5", True, True, ["10.0.0.99"]) is False  # stale PTR
    assert dns_check.dns_matches("10.0.0.5", True, False, []) is False  # PTR name does not resolve
    assert dns_check.dns_matches("10.0.0.5", False, False, []) is None  # no PTR to check
    assert dns_check.dns_matches("2001:db8::1", True, True, ["2001:DB8:0::1"]) is True
    assert dns_check.dns_matches("core-sw1", True, True, ["10.0.0.5"]) is None  # hostname target
