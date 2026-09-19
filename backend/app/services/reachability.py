import asyncio
import os
import platform
import subprocess
from dataclasses import dataclass

# Pings alone are an unreliable signal - plenty of real, reachable devices sit
# behind a firewall that drops ICMP but still answers SSH fine, and a name
# that isn't in DNS doesn't mean the device itself is down (it might only be
# reachable by IP, or resolved some other way). So this never labels a device
# "offline": it just reports the two raw signals (ping, DNS) and leaves the
# caller to highlight anything that didn't answer, as one data point among
# others - not a verdict.

_PING_TIMEOUT_SECONDS = 2.0


@dataclass
class ReachabilityResult:
    host: str
    ping_ok: bool
    dns_ok: bool
    resolved_ip: str | None


def _is_windows() -> bool:
    return platform.system() == "Windows"


def hidden_subprocess_kwargs() -> dict:
    """Extra subprocess.run() arguments so a console tool (ping) started
    from a process without a console of its own - the desktop build's
    worker and API - does not pop up a black window for each call. A
    no-op elsewhere."""
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return {}
    startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    startupinfo.wShowWindow = subprocess.SW_HIDE  # type: ignore[attr-defined]
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}  # type: ignore[attr-defined]


def _run_ping(args: list[str]) -> bool:
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=_PING_TIMEOUT_SECONDS + 2,
            **hidden_subprocess_kwargs(),
        )
    except Exception:  # noqa: BLE001
        # Deliberately broad: ping is a best-effort diagnostic, so anything
        # that stops it from running (the binary missing, a timeout, a
        # permissions issue) is reported as "didn't respond" rather than
        # crashing the whole reachability check.
        return False
    if result.returncode != 0:
        return False
    if _is_windows():
        # Windows ping exits 0 whenever *something* answered - including a
        # router saying "Destination host unreachable" - so a real reply
        # is recognised by its TTL, which only an echo reply carries.
        output = result.stdout or b""
        text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        return "TTL=" in text.upper()
    return True


async def _ping(host: str) -> bool:
    if _is_windows():
        args = ["ping", "-n", "1", "-w", str(int(_PING_TIMEOUT_SECONDS * 1000)), host]
    else:
        args = ["ping", "-c", "1", "-W", str(int(_PING_TIMEOUT_SECONDS)), host]
    # A plain blocking subprocess.run() in a worker thread, not
    # asyncio.create_subprocess_exec() - the latter needs the event loop's
    # own subprocess transport, which a SelectorEventLoop doesn't provide on
    # Windows (raises NotImplementedError) - and that's exactly the loop
    # uvicorn's --reload supervisor forces on Windows so its file-watcher IPC
    # works. Running the subprocess from a thread instead sidesteps the
    # event loop entirely, so this works the same regardless of loop type.
    return await asyncio.to_thread(_run_ping, args)


async def _resolve_dns(host: str) -> tuple[bool, str | None]:
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
        return True, (infos[0][4][0] if infos else None)
    except Exception:  # noqa: BLE001 - same "never crash the batch" reasoning as _ping above
        return False, None


async def check_reachability(host: str) -> ReachabilityResult:
    """Pings `host`, then resolves it via DNS - both best-effort, neither
    treated as proof the device is actually offline (see module docstring)."""
    ping_ok = await _ping(host)
    dns_ok, resolved_ip = await _resolve_dns(host)
    return ReachabilityResult(host=host, ping_ok=ping_ok, dns_ok=dns_ok, resolved_ip=resolved_ip)
