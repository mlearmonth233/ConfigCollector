"""Pushing a firmware image *file* onto a device's local storage - and
deliberately nothing more. No install, no boot-variable change, no reload:
the job ends with the image sitting on flash/bootflash, and whatever the
engineer does with it next (verify, schedule an install window, roll out
over a change process) stays entirely in their hands.

Mechanically that's one interactive "copy <transfer-url> <local-storage>"
dialogue per device, driven over the same authenticated Netmiko session
config collection uses (collector.open_device_session). Vendor copy
commands are interactive - they ask for a destination filename, confirm
overwrites, and (Cisco IOS, infamously) offer to *erase flash* first - so
rather than Netmiko's send_command() (which waits for the prompt and
never answers questions) push_file() runs an expect-style loop: read the
channel, answer whichever question is currently pending
(respond_to_copy_prompt), repeat until the device's prompt comes back.
"""

import re
import time
from collections.abc import Callable

from app.config import get_settings
from app.models.firmware import TransferProtocol
from app.services.collector import CollectionCancelled, CommandExecutionError, open_device_session
from app.services.device_types import parse_command_list

settings = get_settings()


class FirmwarePushError(CommandExecutionError):
    """Login succeeded, but the copy itself didn't - the device reported an
    error, or the transfer never finished within the timeout. A
    CommandExecutionError subclass since (like every post-login failure)
    it must never trigger a fallback-credential retry."""


# Per device type, how to copy a file from this app's transfer server onto
# the device's own local storage - exactly a copy, no install/activate
# step. Only types where the copy command is unambiguous and verified are
# listed; anything else (AireOS WLCs' multi-step "transfer download",
# FortiGate's "execute restore image" - which *installs and reboots*, so is
# specifically not a push - PDUs, Versa) has no default and the user must
# supply the command for it. "{url}" is the fully-formed, protocol-aware
# URL (see build_transfer_url); the finer-grained placeholders it's built
# from ("{protocol}", "{host}", "{port}", "{filename}") still work too.
DEFAULT_PUSH_COMMANDS: dict[str, str] = {
    "cisco_ios": "copy {url} flash:",
    # 9800s are IOS-XE: "flash:" is an alias for "bootflash:" there, but
    # bootflash: is the name the platform itself uses in "dir"/"show
    # version", so it's the less surprising one to see in the transcript.
    "cisco_wlc_9800": "copy {url} bootflash:",
    # NX-OS's mgmt0 lives in the "management" VRF on effectively every
    # deployment - without "vrf management" the copy tries the default VRF
    # and just times out reaching the transfer server. Editable per job for
    # the rare in-band case.
    "cisco_nxos": "copy {url} bootflash: vrf management",
}

# The username/password this app's transfer servers are addressed with in
# a URL. They accept any credentials (see services/transfer_servers.py -
# the security boundary is the bound interface + lifetime, not a login),
# so this is purely so the device never stops to *ask* for one - a
# "Password:" prompt mid-copy is one more thing to get wrong interactively.
TRANSFER_USERNAME = "anonymous"
TRANSFER_PASSWORD = "anonymous"

_STANDARD_PORTS = {TransferProtocol.TFTP: 69, TransferProtocol.FTP: 21, TransferProtocol.SCP: 22}


def transfer_port(protocol: TransferProtocol) -> int:
    return {
        TransferProtocol.TFTP: settings.firmware_tftp_port,
        TransferProtocol.FTP: settings.firmware_ftp_port,
        TransferProtocol.SCP: settings.firmware_scp_port,
    }[protocol]


def build_transfer_url(protocol: TransferProtocol, host: str, port: int, filename: str) -> str:
    """The URL a device is told to copy from, in the form vendor CLIs
    expect: bare "tftp://host/file" for TFTP (there's no login to give),
    "user:pass@" for FTP and SCP so the device never pauses at a password
    prompt, and an explicit ":port" only when it isn't the protocol's own
    well-known one (spelling out ":69" or ":21" trips up some older IOS
    URL parsers, and is redundant anyway)."""
    authority = host if port == _STANDARD_PORTS[protocol] else f"{host}:{port}"
    if protocol == TransferProtocol.TFTP:
        return f"tftp://{authority}/{filename}"
    return f"{protocol.value}://{TRANSFER_USERNAME}:{TRANSFER_PASSWORD}@{authority}/{filename}"


def render_push_commands(raw_commands: str, *, protocol: TransferProtocol, host: str, port: int, filename: str) -> list[str]:
    """Substitutes a job's transfer-server details into a comma-separated
    copy command template. Raises ValueError (naming the offending
    placeholder) for any "{...}" the template uses that isn't one of the
    supported ones, so a typo is caught when the job is created rather
    than sent to a device verbatim."""
    values = {
        "url": build_transfer_url(protocol, host, port, filename),
        "protocol": protocol.value,
        "host": host,
        "port": port,
        "filename": filename,
    }
    rendered = []
    for cmd in parse_command_list(raw_commands):
        try:
            rendered.append(cmd.format(**values))
        except (KeyError, IndexError, ValueError) as exc:
            placeholder = exc.args[0] if isinstance(exc, KeyError) else cmd
            raise ValueError(
                f"Unknown placeholder {{{placeholder}}} in '{cmd}' - use {{url}}, {{protocol}}, {{host}}, "
                "{port}, or {filename}"
            ) from exc
    return rendered


# --- Interactive copy dialogue ----------------------------------------------

# Cisco IOS's "copy" offers to wipe flash before writing the new image
# whenever the destination is flash: - answering with a bare Enter here
# ACCEPTS it. This is the one prompt in the whole dialogue where "just
# press Enter" is catastrophic, so it's matched first and explicitly.
_ERASE_PROMPT = re.compile(r"erase .* before copying", re.IGNORECASE)
# SSH host-key trust prompt (NX-OS scp, IOS-XE scp) - wants a literal yes.
_HOST_KEY_PROMPT = re.compile(r"continue connecting \(yes/no", re.IGNORECASE)
# A password prompt from the transfer server (FTP/SCP) - only appears if
# the device ignored the user:pass@ in the URL.
_PASSWORD_PROMPT = re.compile(r"password\s*:\s*$", re.IGNORECASE)
# NX-OS-style yes/no questions, e.g. "Do you want to overwrite (y/n)?[n]" -
# the copy's whole purpose is to land the file, so overwriting an old copy
# of the same filename is always yes.
_YES_NO_PROMPT = re.compile(r"\((y/n|yes/no)\)\s*\??\s*(\[[^\]]*\])?\s*$", re.IGNORECASE)
# IOS-style "[confirm]" and "Something [default]?" prompts: Enter accepts
# the shown default (destination filename, overwrite, remote host, etc.).
_CONFIRM_PROMPT = re.compile(r"\[confirm\]\s*$", re.IGNORECASE)
_DEFAULT_VALUE_PROMPT = re.compile(r"\[[^\]]*\]\s*\?\s*$")
# A bare question with no shown default ("Source filename?") still just
# wants Enter to take the device's own default. NX-OS's VRF question when
# the command didn't name one ("Enter vrf (If no input, current vrf
# 'default' is considered):") is the one colon-terminated prompt matched -
# a general "ends with ':'" rule would misfire on IOS's own progress line
# ("Loading ios.bin from 10.0.0.5 (via Vlan1): !!!") the instant it
# arrives, before the first "!" does.
_OPEN_QUESTION_PROMPT = re.compile(r"\?\s*$")
_NXOS_VRF_PROMPT = re.compile(r"Enter vrf.*:\s*$", re.IGNORECASE)

# Markers of a copy that finished but failed. Deliberately specific - the
# transcript also contains the command echo and the URL, so anything as
# broad as "error" alone risks matching an innocent filename.
_FAILURE_MARKERS = re.compile(
    r"%\s*Error|Error opening|Error reading|Error writing|Timed out|No such file|Permission denied|"
    r"Copy failed|copy failed|Connection refused|Connection timed out|Access denied|Login incorrect|"
    r"%\s*Invalid input|%\s*Ambiguous command|Not enough space|no space left|Invalid destination|"
    r"%\s*Bad|Host unreachable|Network is unreachable",
    re.IGNORECASE,
)


def respond_to_copy_prompt(text: str) -> str | None:
    """Given the tail of the device's output since its last answered
    prompt, returns the line to send back if the device is currently
    waiting on a question, or None if it isn't (still transferring, still
    printing, or already back at its prompt). Pure and side-effect free so
    every branch is unit-testable without a device."""
    last_line = text.rstrip().rsplit("\n", 1)[-1].strip()
    if not last_line:
        return None
    if _ERASE_PROMPT.search(last_line):
        return "n"
    if _HOST_KEY_PROMPT.search(last_line):
        return "yes"
    if _PASSWORD_PROMPT.search(last_line):
        return TRANSFER_PASSWORD
    if _YES_NO_PROMPT.search(last_line):
        return "yes" if "yes/no" in last_line.lower() else "y"
    if _CONFIRM_PROMPT.search(last_line) or _DEFAULT_VALUE_PROMPT.search(last_line):
        return ""
    if _OPEN_QUESTION_PROMPT.search(last_line) or _NXOS_VRF_PROMPT.search(last_line):
        return ""
    return None


def _ends_with_prompt(text: str, prompt: str) -> bool:
    last_line = text.rstrip().rsplit("\n", 1)[-1].strip()
    return bool(prompt) and last_line.endswith(prompt)


def run_copy_dialogue(
    conn,
    command: str,
    *,
    prompt: str,
    timeout_seconds: float,
    on_output: Callable[[str], None] | None = None,
    poll_interval: float = 0.5,
) -> str:
    """Sends one copy command and drives it to completion: everything the
    device prints is streamed to on_output as it arrives; any question it
    stops at is answered per respond_to_copy_prompt; and it's finished when
    the device's own prompt reappears on a line of its own. Each question
    is answered exactly once - only output that arrived *after* the last
    answer is ever inspected for a new one, so a slow device that hasn't
    yet echoed our answer can't be sent a second Enter it didn't ask for.
    Raises FirmwarePushError if `timeout_seconds` passes without the
    prompt coming back, or if the finished transcript carries one of the
    vendors' failure markers."""
    conn.write_channel(command + conn.RETURN)
    transcript = ""
    answered_up_to = 0
    deadline = time.monotonic() + timeout_seconds

    while True:
        chunk = conn.read_channel()
        if chunk:
            transcript += chunk
            if on_output is not None:
                on_output(chunk)
            # Only a prompt on its own line counts - the command echo
            # ("Switch#copy tftp://...") ends with the URL, not the prompt.
            if _ends_with_prompt(transcript, prompt):
                break
            answer = respond_to_copy_prompt(transcript[answered_up_to:])
            if answer is not None:
                conn.write_channel(answer + conn.RETURN)
                answered_up_to = len(transcript)
                if on_output is not None:
                    on_output(f"{answer or '<Enter>'}\n")
                continue
        if time.monotonic() >= deadline:
            raise FirmwarePushError(
                f"Timed out after {int(timeout_seconds)}s waiting for '{command}' to finish - the device never "
                "returned to its prompt. Check it can reach the transfer server on the chosen interface."
            )
        if not chunk:
            time.sleep(poll_interval)

    failure = _FAILURE_MARKERS.search(transcript)
    if failure:
        line = next((ln.strip() for ln in transcript.splitlines() if failure.group(0) in ln), failure.group(0))
        raise FirmwarePushError(f"The device reported a copy failure: {line}")
    return transcript


def push_file(
    *,
    host: str,
    port: int,
    device_type: str,
    username: str,
    password: str,
    secret: str | None,
    auth_timeout: int,
    commands: list[str],
    mfa_mode: str = "none",
    otp: str | None = None,
    otp_delimiter: str = ",",
    on_authenticated: Callable[[], None] | None = None,
    on_output: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """Logs in (same auth/enable/error classification as config
    collection) and runs each already-rendered copy command through
    run_copy_dialogue in turn. `should_cancel` is honoured between
    commands, never mid-copy - an in-flight transfer is left to finish
    rather than be aborted halfway through writing to flash."""

    def _emit(text: str) -> None:
        if on_output is not None:
            on_output(text)

    per_command_timeout = timeout_seconds if timeout_seconds is not None else settings.firmware_transfer_timeout_seconds
    transcripts = []
    with open_device_session(
        host=host,
        port=port,
        device_type=device_type,
        username=username,
        password=password,
        secret=secret,
        auth_timeout=auth_timeout,
        mfa_mode=mfa_mode,
        otp=otp,
        otp_delimiter=otp_delimiter,
        on_authenticated=on_authenticated,
        on_output=on_output,
    ) as conn:
        prompt = conn.find_prompt().strip()
        for command in commands:
            if should_cancel is not None and should_cancel():
                _emit("\nCancelled - stopping before the next command.\n")
                raise CollectionCancelled(f"File push to {host}:{port} was cancelled")
            _emit(f"\n$ {command}\n")
            transcripts.append(
                run_copy_dialogue(conn, command, prompt=prompt, timeout_seconds=per_command_timeout, on_output=on_output)
            )
        _emit("\nFile push complete - the image is on the device's storage. Nothing was installed or reloaded.\n")
    return "\n".join(transcripts)
