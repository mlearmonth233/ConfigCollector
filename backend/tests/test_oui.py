"""MAC address manufacturer lookup (services/oui.py)."""

from app.services import oui


def test_lookup_uses_the_most_specific_block():
    assert oui.manufacturer("70:0F:6A:11:22:00") == "Cisco Systems, Inc"  # 24-bit MA-L
    assert oui.manufacturer("a4bb.6d12.3456") == "Dell Inc."
    assert oui.manufacturer("8c1f.64af.a001") == "DATA ELECTRONIC DEVICES, INC"  # 36-bit MA-S beats the MA-L that owns 8C1F64
    assert oui.manufacturer("c85c.e271.0000") == "SYNERGY SYSTEMS AND SOLUTIONS"  # 28-bit MA-M
    assert oui.table_size() > 50000


def test_randomised_and_malformed_addresses():
    assert oui.is_randomized("da:a1:19:12:34:56") is True  # second hex digit A: locally administered unicast
    assert oui.is_randomized("70:0f:6a:11:22:00") is False
    assert oui.is_randomized("01:00:5e:00:00:01") is False  # multicast is not "randomised"
    assert oui.manufacturer("da:a1:19:12:34:56") == oui.RANDOMIZED
    assert oui.manufacturer("not a mac") is None
    assert oui.manufacturer("") is None and oui.manufacturer(None) is None
    assert oui.manufacturer("ff:ff:ff:ff:ff:ff") is None
