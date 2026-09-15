"""Polling a device over SNMP (v2c or v3) for the things an engineer
actually wants when they say "grab the logs": what the box is (system
group), how its interfaces are doing (IF-MIB status and error counters),
and the device's own syslog history buffer (CISCO-SYSLOG-MIB's
clogHistoryTable, where a Cisco box keeps its last N log messages) - plus
any extra OIDs the user asks for. The result is one plain-text report per
device (see format_report), stored on the SnmpJobItem the way a config
snapshot is stored for a collection.

Built on pysnmp's asyncio high-level API. Every network call goes through
the two small wrappers `snmp_get` and `snmp_walk` so tests can swap them
for fakes; everything above them (auth building, report formatting) is
pure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    get_cmd,
    usm3DESEDEPrivProtocol,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
    usmNoAuthProtocol,
    usmNoPrivProtocol,
    walk_cmd,
)

from app.models.snmp import SnmpAuthProtocol, SnmpPrivProtocol, SnmpSecurityLevel, SnmpVersion

# --- OIDs -----------------------------------------------------------------------

SYSTEM_OIDS = {
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysObjectID": "1.3.6.1.2.1.1.2.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysContact": "1.3.6.1.2.1.1.4.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
}

IF_COLUMNS = {
    "ifDescr": "1.3.6.1.2.1.2.2.1.2",
    "ifAdminStatus": "1.3.6.1.2.1.2.2.1.7",
    "ifOperStatus": "1.3.6.1.2.1.2.2.1.8",
    "ifInErrors": "1.3.6.1.2.1.2.2.1.14",
    "ifOutErrors": "1.3.6.1.2.1.2.2.1.20",
    "ifInDiscards": "1.3.6.1.2.1.2.2.1.13",
    "ifOutDiscards": "1.3.6.1.2.1.2.2.1.19",
    "ifAlias": "1.3.6.1.2.1.31.1.1.1.18",
}

# CISCO-SYSLOG-MIB::clogHistoryTable - the device's own log buffer.
SYSLOG_COLUMNS = {
    "facility": "1.3.6.1.4.1.9.9.41.1.2.3.1.2",
    "severity": "1.3.6.1.4.1.9.9.41.1.2.3.1.3",
    "msgName": "1.3.6.1.4.1.9.9.41.1.2.3.1.4",
    "msgText": "1.3.6.1.4.1.9.9.41.1.2.3.1.5",
    "timestamp": "1.3.6.1.4.1.9.9.41.1.2.3.1.6",
}

IF_STATUS = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}
SYSLOG_SEVERITY = {1: "emerg", 2: "alert", 3: "crit", 4: "err", 5: "warning", 6: "notice", 7: "info", 8: "debug"}

MAX_TABLE_ROWS = 5000
CONCURRENCY = 8


class SnmpError(Exception):
    """A poll that couldn't complete - no response, authentication rejected,
    bad community. Message is safe to store/display (never a secret)."""


@dataclass(frozen=True)
class SnmpAuth:
    """Plain (decrypted) SNMP settings for one poll."""

    version: SnmpVersion
    community: str | None = None
    username: str | None = None
    security_level: SnmpSecurityLevel | None = None
    auth_protocol: SnmpAuthProtocol | None = None
    auth_password: str | None = None
    priv_protocol: SnmpPrivProtocol | None = None
    priv_password: str | None = None
    context_name: str | None = None
    port: int = 161
    timeout_seconds: int = 3
    retries: int = 1

    def describe(self) -> str:
        if self.version == SnmpVersion.V2C:
            return "SNMPv2c"
        level = self.security_level.value if self.security_level else "noAuthNoPriv"
        return f"SNMPv3 user '{self.username}' ({level})"


_AUTH_PROTOCOLS = {
    SnmpAuthProtocol.MD5: usmHMACMD5AuthProtocol,
    SnmpAuthProtocol.SHA: usmHMACSHAAuthProtocol,
    SnmpAuthProtocol.SHA224: usmHMAC128SHA224AuthProtocol,
    SnmpAuthProtocol.SHA256: usmHMAC192SHA256AuthProtocol,
    SnmpAuthProtocol.SHA384: usmHMAC256SHA384AuthProtocol,
    SnmpAuthProtocol.SHA512: usmHMAC384SHA512AuthProtocol,
}
_PRIV_PROTOCOLS = {
    SnmpPrivProtocol.DES: usmDESPrivProtocol,
    SnmpPrivProtocol.TRIPLE_DES: usm3DESEDEPrivProtocol,
    SnmpPrivProtocol.AES128: usmAesCfb128Protocol,
    SnmpPrivProtocol.AES192: usmAesCfb192Protocol,
    SnmpPrivProtocol.AES256: usmAesCfb256Protocol,
}


def validate_auth(auth: SnmpAuth) -> None:
    """Raises ValueError with a user-facing message for an incomplete
    profile - shared by the API (on save) and the poller (on use)."""
    if auth.version == SnmpVersion.V2C:
        if not auth.community:
            raise ValueError("SNMPv2c needs a community string")
        return
    if not auth.username:
        raise ValueError("SNMPv3 needs a username")
    level = auth.security_level or SnmpSecurityLevel.NO_AUTH_NO_PRIV
    if level in (SnmpSecurityLevel.AUTH_NO_PRIV, SnmpSecurityLevel.AUTH_PRIV):
        if not auth.auth_protocol or not auth.auth_password:
            raise ValueError(f"SNMPv3 {level.value} needs an authentication protocol and password")
    if level == SnmpSecurityLevel.AUTH_PRIV:
        if not auth.priv_protocol or not auth.priv_password:
            raise ValueError("SNMPv3 authPriv needs a privacy protocol and password")


def build_auth_data(auth: SnmpAuth):
    """The pysnmp auth object for a profile."""
    validate_auth(auth)
    if auth.version == SnmpVersion.V2C:
        return CommunityData(auth.community, mpModel=1)
    level = auth.security_level or SnmpSecurityLevel.NO_AUTH_NO_PRIV
    if level == SnmpSecurityLevel.NO_AUTH_NO_PRIV:
        return UsmUserData(auth.username, authProtocol=usmNoAuthProtocol, privProtocol=usmNoPrivProtocol)
    if level == SnmpSecurityLevel.AUTH_NO_PRIV:
        return UsmUserData(
            auth.username,
            authKey=auth.auth_password,
            authProtocol=_AUTH_PROTOCOLS[auth.auth_protocol],
            privProtocol=usmNoPrivProtocol,
        )
    return UsmUserData(
        auth.username,
        authKey=auth.auth_password,
        privKey=auth.priv_password,
        authProtocol=_AUTH_PROTOCOLS[auth.auth_protocol],
        privProtocol=_PRIV_PROTOCOLS[auth.priv_protocol],
    )


# --- the two network primitives (swapped for fakes in tests) -----------------

Getter = Callable[[str, SnmpAuth, list[str]], Awaitable[dict[str, str | None]]]
Walker = Callable[[str, SnmpAuth, str], Awaitable[list[tuple[str, str]]]]


def _classify(error_indication) -> str:
    text = str(error_indication)
    lower = text.lower()
    if "timeout" in lower or "no snmp response" in lower:
        return (
            "No SNMP response (timed out). Check the device has SNMP enabled, that this machine is allowed by "
            "its SNMP access list, and that UDP port 161 isn't blocked. For v2c a wrong community string also "
            "looks exactly like this - the device just stays silent."
        )
    if "wrongdigest" in lower or "authentication" in lower:
        return "SNMPv3 authentication failed - wrong auth password or protocol for this user."
    if "unknownusername" in lower or "unknown user" in lower:
        return "SNMPv3 user is not known to the device."
    if "decryption" in lower or "privacy" in lower:
        return "SNMPv3 privacy (encryption) failed - wrong priv password or protocol."
    if "unsupportedseclevel" in lower or "security level" in lower:
        return "The device doesn't accept this security level for this user (e.g. it requires authPriv)."
    return f"SNMP error: {text}"


def _render(value) -> str:
    """A varbind value as text: OctetStrings decoded, everything else via
    pysnmp's own pretty-printer (Integers, OIDs, TimeTicks, Counters)."""
    try:
        if hasattr(value, "asOctets"):
            raw = value.asOctets()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return value.prettyPrint()
        return value.prettyPrint()
    except Exception:  # noqa: BLE001
        return str(value)


async def snmp_get(host: str, auth: SnmpAuth, oids: list[str]) -> dict[str, str | None]:
    """GET several scalar OIDs. Returns {oid: text} - None for an OID the
    device doesn't have. Raises SnmpError when the device didn't answer at
    all or rejected the request."""
    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create((host, auth.port), timeout=auth.timeout_seconds, retries=auth.retries)
        error_indication, error_status, error_index, var_binds = await get_cmd(
            engine,
            build_auth_data(auth),
            target,
            ContextData(contextName=auth.context_name or ""),
            *[ObjectType(ObjectIdentity(oid)) for oid in oids],
        )
        if error_indication:
            raise SnmpError(_classify(error_indication))
        result: dict[str, str | None] = {}
        for oid, (name, value) in zip(oids, var_binds):
            text = _render(value)
            result[oid] = None if text in ("No Such Object currently exists at this OID", "No Such Instance currently exists at this OID") else text
        if error_status:
            # e.g. noSuchName from a v1-ish agent: the offending varbind is unknown, the rest may be fine.
            bad = int(error_index) - 1 if error_index else None
            if bad is not None and 0 <= bad < len(oids):
                result[oids[bad]] = None
        return result
    finally:
        engine.close_dispatcher()


async def snmp_walk(host: str, auth: SnmpAuth, base_oid: str) -> list[tuple[str, str]]:
    """WALK one column/subtree. Returns [(index_suffix, text)] in table
    order, stopping at the end of the subtree or MAX_TABLE_ROWS. An empty
    list means the device has no such table (not an error). Raises
    SnmpError on no response / rejection."""
    engine = SnmpEngine()
    rows: list[tuple[str, str]] = []
    prefix = base_oid + "."
    try:
        target = await UdpTransportTarget.create((host, auth.port), timeout=auth.timeout_seconds, retries=auth.retries)
        async for error_indication, error_status, error_index, var_binds in walk_cmd(
            engine,
            build_auth_data(auth),
            target,
            ContextData(contextName=auth.context_name or ""),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False,
        ):
            if error_indication:
                raise SnmpError(_classify(error_indication))
            if error_status:
                break
            for name, value in var_binds:
                oid = str(name)
                if not oid.startswith(prefix):
                    return rows
                text = _render(value)
                if text.startswith("No Such") or text == "endOfMibView":
                    return rows
                rows.append((oid[len(prefix):], text))
            if len(rows) >= MAX_TABLE_ROWS:
                break
        return rows
    finally:
        engine.close_dispatcher()


# --- gathering + report -----------------------------------------------------------


@dataclass
class SnmpPollResult:
    system: dict[str, str | None] = field(default_factory=dict)
    interfaces: list[dict[str, str]] = field(default_factory=list)
    syslog: list[dict[str, str]] = field(default_factory=list)
    syslog_supported: bool = True
    extra: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _table(columns: dict[str, str], walked: dict[str, list[tuple[str, str]]]) -> list[dict[str, str]]:
    """Joins per-column walks into rows keyed by the shared index suffix,
    in the order the first column listed them."""
    by_index: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for column in columns:
        for index, text in walked.get(column, []):
            if index not in by_index:
                by_index[index] = {"index": index}
                order.append(index)
            by_index[index][column] = text
    return [by_index[i] for i in order]


async def poll_device(
    host: str,
    auth: SnmpAuth,
    extra_oids: list[str] | None = None,
    *,
    getter: Getter = snmp_get,
    walker: Walker = snmp_walk,
    on_output: Callable[[str], None] | None = None,
) -> SnmpPollResult:
    """Gathers everything for one device. The system group is fetched first
    and any failure there (no answer, bad auth) aborts the poll with
    SnmpError - there's no point walking tables the device won't answer.
    Later tables failing only add a warning, so a device that lacks the
    Cisco syslog MIB still produces a report."""

    def emit(text: str) -> None:
        if on_output is not None:
            on_output(text)

    result = SnmpPollResult()
    emit(f"Polling {host}:{auth.port} over {auth.describe()}...\n")
    system = await getter(host, auth, list(SYSTEM_OIDS.values()))
    result.system = {name: system.get(oid) for name, oid in SYSTEM_OIDS.items()}
    emit(f"System: {result.system.get('sysName') or '?'} - {(result.system.get('sysDescr') or '')[:80]}\n")

    walked: dict[str, list[tuple[str, str]]] = {}
    try:
        for column, oid in IF_COLUMNS.items():
            walked[column] = await walker(host, auth, oid)
        result.interfaces = _table(IF_COLUMNS, walked)
        emit(f"Interfaces: {len(result.interfaces)} rows\n")
    except SnmpError as exc:
        result.warnings.append(f"Interface table: {exc}")
        emit(f"Interface table unavailable: {exc}\n")

    walked = {}
    try:
        for column, oid in SYSLOG_COLUMNS.items():
            walked[column] = await walker(host, auth, oid)
        result.syslog = _table(SYSLOG_COLUMNS, walked)
        result.syslog_supported = bool(result.syslog)
        emit(
            f"Syslog history: {len(result.syslog)} entries\n"
            if result.syslog
            else "Syslog history: none (device has no CISCO-SYSLOG-MIB history table, or the buffer is empty)\n"
        )
    except SnmpError as exc:
        result.syslog_supported = False
        result.warnings.append(f"Syslog history: {exc}")
        emit(f"Syslog history unavailable: {exc}\n")

    for oid in extra_oids or []:
        try:
            result.extra[oid] = await walker(host, auth, oid)
            emit(f"{oid}: {len(result.extra[oid])} value(s)\n")
        except SnmpError as exc:
            result.warnings.append(f"{oid}: {exc}")
            emit(f"{oid} unavailable: {exc}\n")

    return result


def _uptime(ticks_text: str | None) -> str:
    if not ticks_text:
        return "?"
    try:
        ticks = int(str(ticks_text).strip())
    except ValueError:
        return ticks_text
    return f"{timedelta(seconds=ticks // 100)} ({ticks} ticks)"


def format_report(device_name: str, host: str, auth: SnmpAuth, result: SnmpPollResult, *, collected_at: str) -> str:
    """The human-readable report stored per device - plain text so it
    downloads, diffs and greps like a config snapshot."""
    s = result.system
    lines = [
        f"# SNMP report: {device_name} ({host})",
        f"# Collected {collected_at} via {auth.describe()}",
        "",
        "## System",
        f"sysName:     {s.get('sysName') or '-'}",
        f"sysDescr:    {s.get('sysDescr') or '-'}",
        f"sysObjectID: {s.get('sysObjectID') or '-'}",
        f"sysUpTime:   {_uptime(s.get('sysUpTime'))}",
        f"sysContact:  {s.get('sysContact') or '-'}",
        f"sysLocation: {s.get('sysLocation') or '-'}",
        "",
        f"## Interfaces ({len(result.interfaces)})",
    ]
    if result.interfaces:
        header = f"{'idx':>5}  {'interface':<28} {'alias':<24} {'admin':<6} {'oper':<14} {'inErr':>8} {'outErr':>8} {'inDisc':>8} {'outDisc':>8}"
        lines.append(header)
        lines.append("-" * len(header))
        for row in result.interfaces:
            admin = IF_STATUS.get(_int(row.get("ifAdminStatus")), row.get("ifAdminStatus", "-"))
            oper = IF_STATUS.get(_int(row.get("ifOperStatus")), row.get("ifOperStatus", "-"))
            lines.append(
                f"{row['index']:>5}  {row.get('ifDescr', '-')[:28]:<28} {row.get('ifAlias', '')[:24]:<24} "
                f"{admin:<6} {oper:<14} {row.get('ifInErrors', '-'):>8} {row.get('ifOutErrors', '-'):>8} "
                f"{row.get('ifInDiscards', '-'):>8} {row.get('ifOutDiscards', '-'):>8}"
            )
    else:
        lines.append("(none reported)")

    lines += ["", f"## Syslog history ({len(result.syslog)})"]
    if result.syslog:
        for row in result.syslog:
            # CISCO-SYSLOG-MIB severities are 1-based (emergency=1 ... debug=8);
            # the CLI shows the 0-based syslog level ("%LINK-3-UPDOWN"), so
            # render it the way an engineer would read it off the console,
            # with the name alongside.
            sev_num = _int(row.get("severity"))
            sev = f"{sev_num - 1}" if sev_num is not None else row.get("severity", "?")
            sev_name = SYSLOG_SEVERITY.get(sev_num, "")
            stamp = _uptime(row.get("timestamp")).split(" (")[0]
            lines.append(
                f"[{stamp:>16}] %{row.get('facility', '?')}-{sev}-{row.get('msgName', '?')}: {row.get('msgText', '')}"
                + (f"  ({sev_name})" if sev_name else "")
            )
    elif result.syslog_supported:
        lines.append("(buffer empty)")
    else:
        lines.append(
            "(not available - this device has no CISCO-SYSLOG-MIB history table; on Cisco IOS enable it with "
            "'logging history <size>' and 'logging history <level>', or collect logs over SSH with a custom "
            "device type/command instead)"
        )

    if result.extra:
        lines += ["", "## Extra OIDs"]
        for oid, rows in result.extra.items():
            lines.append(f"{oid}:")
            if rows:
                lines += [f"  .{index} = {text}" for index, text in rows]
            else:
                lines.append("  (no values)")

    if result.warnings:
        lines += ["", "## Warnings"] + [f"- {w}" for w in result.warnings]
    return "\n".join(lines) + "\n"


def _int(text: str | None) -> int | None:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


async def poll_many(specs: list[tuple[str, SnmpAuth, list[str] | None]], **kwargs) -> list[SnmpPollResult | Exception]:
    """Polls several devices concurrently (bounded by CONCURRENCY),
    returning each one's result or the exception it raised, in order."""
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(host, auth, extra):
        async with semaphore:
            try:
                return await poll_device(host, auth, extra, **kwargs)
            except Exception as exc:  # noqa: BLE001
                return exc

    return await asyncio.gather(*(one(*spec) for spec in specs))
