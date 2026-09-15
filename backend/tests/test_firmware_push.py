"""services/firmware_push.py: the interactive copy dialogue, driven against
a scripted fake of a device's channel - no device, DB, or network."""

import pytest

from app.models.firmware import TransferProtocol
from app.services import collector as collector_module
from app.services.collector import CollectionCancelled, CommandExecutionError
from app.services.firmware_push import (
    DEFAULT_PUSH_COMMANDS,
    FirmwarePushError,
    build_transfer_url,
    push_file,
    respond_to_copy_prompt,
    run_copy_dialogue,
)


# --- URL building ------------------------------------------------------------


def test_tftp_url_has_no_credentials_and_omits_the_standard_port():
    assert build_transfer_url(TransferProtocol.TFTP, "10.0.0.5", 69, "ios.bin") == "tftp://10.0.0.5/ios.bin"


def test_tftp_url_includes_a_non_standard_port():
    assert build_transfer_url(TransferProtocol.TFTP, "10.0.0.5", 6969, "ios.bin") == "tftp://10.0.0.5:6969/ios.bin"


def test_ftp_and_scp_urls_carry_credentials_so_the_device_never_prompts():
    assert build_transfer_url(TransferProtocol.FTP, "10.0.0.5", 21, "ios.bin") == "ftp://anonymous:anonymous@10.0.0.5/ios.bin"
    assert (
        build_transfer_url(TransferProtocol.SCP, "10.0.0.5", 2222, "ios.bin")
        == "scp://anonymous:anonymous@10.0.0.5:2222/ios.bin"
    )


def test_default_commands_only_copy_never_install():
    for device_type, template in DEFAULT_PUSH_COMMANDS.items():
        assert template.startswith("copy {url} "), device_type
        assert "," not in template, f"{device_type}: a default must be a single copy command"


# --- Prompt classification ---------------------------------------------------


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        # The dangerous one: Enter would erase flash. Must answer no.
        ("copy tftp://10.0.0.5/ios.bin flash:\nErase flash: before copying? [confirm]", "n"),
        ("Erase bootflash: before copying? [confirm]", "n"),
        # Accept the shown default / confirm the action.
        ("Destination filename [ios.bin]? ", ""),
        ("Address or name of remote host [10.0.0.5]? ", ""),
        ("Source filename [ios.bin]? ", ""),
        ("Do you want to over write? [confirm]", ""),
        ("%Warning: There is a file already existing with this name\nDo you want to over write? [confirm]", ""),
        ("Source username [anonymous]? ", ""),
        ("Enter vrf (If no input, current vrf 'default' is considered): ", ""),
        # NX-OS style yes/no.
        ("Warning: There is already a file existing with this name. Do you want to overwrite (y/n)?[n] ", "y"),
        ("Are you sure you want to continue connecting (yes/no)? ", "yes"),
        ("anonymous@10.0.0.5's password: ", "anonymous"),
        ("Password: ", "anonymous"),
    ],
)
def test_respond_to_copy_prompt_answers_each_question(tail, expected):
    assert respond_to_copy_prompt(tail) == expected


@pytest.mark.parametrize(
    "tail",
    [
        "",
        "Accessing tftp://10.0.0.5/ios.bin...",
        # IOS's progress line, as it first arrives - ends with ": " but is
        # not a question, and answering it would send a stray Enter.
        "Loading ios.bin from 10.0.0.5 (via Vlan1): ",
        "Loading ios.bin from 10.0.0.5 (via Vlan1): !!!!!!!!!!!!!!!!",
        "[OK - 12345 bytes]\n",
        "12345 bytes copied in 3.012 secs (4099 bytes/sec)",
        "Switch#",
    ],
)
def test_respond_to_copy_prompt_stays_quiet_when_nothing_is_asked(tail):
    assert respond_to_copy_prompt(tail) is None


# --- The dialogue itself -----------------------------------------------------


class ScriptedChannel:
    """A device channel that plays back a scripted copy dialogue: each
    entry in `script` is either output to emit on the next read, or
    ("expect", answer) - the device is waiting and emits nothing more until
    exactly that answer is written. Anything else written is recorded as an
    unexpected write and fails the copy the way a real device would."""

    RETURN = "\n"

    def __init__(self, script: list, prompt: str = "Switch#"):
        self.script = list(script)
        self.prompt = prompt
        self.writes: list[str] = []
        self.unexpected: list[str] = []
        self.waiting_for: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def find_prompt(self) -> str:
        return self.prompt

    def write_channel(self, text: str) -> None:
        self.writes.append(text)
        if self.waiting_for is not None:
            if text == self.waiting_for + self.RETURN:
                self.waiting_for = None
            else:
                self.unexpected.append(text)
                self.waiting_for = None
                self.script = [f"\n%Error: unexpected input {text!r}\n{self.prompt}"]

    def read_channel(self) -> str:
        if self.waiting_for is not None or not self.script:
            return ""
        step = self.script.pop(0)
        # An output step immediately followed by ("expect", answer) is a
        # question: the device is waiting from the moment it printed it.
        if self.script and isinstance(self.script[0], tuple):
            self.waiting_for = self.script.pop(0)[1]
        return step


def _ios_tftp_script(prompt: str = "Switch#") -> list:
    return [
        f"{prompt}copy tftp://10.0.0.5/ios.bin flash:\n",
        "Destination filename [ios.bin]? ",
        ("expect", ""),
        "\nErase flash: before copying? [confirm]",
        ("expect", "n"),
        "\nAccessing tftp://10.0.0.5/ios.bin...\n",
        "Loading ios.bin from 10.0.0.5 (via Vlan1): ",
        "!!!!!!!!",
        "!!!!\n[OK - 12345 bytes]\n\n",
        f"12345 bytes copied in 3.012 secs (4099 bytes/sec)\n{prompt}",
    ]


def test_run_copy_dialogue_answers_every_prompt_and_declines_the_erase():
    channel = ScriptedChannel(_ios_tftp_script())
    chunks: list[str] = []

    transcript = run_copy_dialogue(
        channel,
        "copy tftp://10.0.0.5/ios.bin flash:",
        prompt="Switch#",
        timeout_seconds=5,
        on_output=chunks.append,
        poll_interval=0,
    )

    assert channel.unexpected == []
    assert channel.writes == ["copy tftp://10.0.0.5/ios.bin flash:\n", "\n", "n\n"]
    assert "12345 bytes copied" in transcript
    # Live output streams the device's text and marks each answer sent.
    assert "<Enter>\n" in chunks
    assert "n\n" in chunks


def test_run_copy_dialogue_answers_a_prompt_only_once():
    # The device is slow to echo/continue after our answer: several empty
    # reads in a row still show the same prompt as the last line, which
    # must NOT be answered again (a second stray Enter is exactly how the
    # erase-flash question gets accepted by accident).
    channel = ScriptedChannel(
        [
            "Switch#copy tftp://10.0.0.5/ios.bin flash:\n",
            "Destination filename [ios.bin]? ",
            ("expect", ""),
            "",
            "",
            "",
            "\n[OK - 5 bytes]\n5 bytes copied in 0.1 secs\nSwitch#",
        ]
    )
    run_copy_dialogue(channel, "copy tftp://10.0.0.5/ios.bin flash:", prompt="Switch#", timeout_seconds=5, poll_interval=0)
    assert channel.writes == ["copy tftp://10.0.0.5/ios.bin flash:\n", "\n"]


def test_run_copy_dialogue_reports_the_device_error_line():
    channel = ScriptedChannel(
        [
            "Switch#copy tftp://10.0.0.5/ios.bin flash:\n",
            "Destination filename [ios.bin]? ",
            ("expect", ""),
            "\nAccessing tftp://10.0.0.5/ios.bin...\n",
            "%Error opening tftp://10.0.0.5/ios.bin (Timed out)\nSwitch#",
        ]
    )
    with pytest.raises(FirmwarePushError, match=r"Error opening tftp://10.0.0.5/ios.bin \(Timed out\)"):
        run_copy_dialogue(channel, "copy tftp://10.0.0.5/ios.bin flash:", prompt="Switch#", timeout_seconds=5, poll_interval=0)


def test_run_copy_dialogue_times_out_if_the_prompt_never_returns():
    channel = ScriptedChannel(["Switch#copy tftp://10.0.0.5/ios.bin flash:\n", "Loading ios.bin from 10.0.0.5 (via Vlan1): !"])
    with pytest.raises(FirmwarePushError, match="Timed out"):
        run_copy_dialogue(channel, "copy tftp://10.0.0.5/ios.bin flash:", prompt="Switch#", timeout_seconds=0.05, poll_interval=0.01)


def test_run_copy_dialogue_does_not_mistake_the_command_echo_for_completion():
    # The echo line "Switch#copy ..." contains the prompt but doesn't end
    # with it - the dialogue must keep going until a bare prompt line.
    channel = ScriptedChannel(_ios_tftp_script())
    transcript = run_copy_dialogue(channel, "copy tftp://10.0.0.5/ios.bin flash:", prompt="Switch#", timeout_seconds=5, poll_interval=0)
    assert transcript.rstrip().endswith("Switch#")
    assert "bytes copied" in transcript


def test_nxos_overwrite_and_vrf_prompts_are_handled():
    channel = ScriptedChannel(
        [
            "nx1# copy scp://anonymous:anonymous@10.0.0.5:2222/nxos.bin bootflash: vrf management\n",
            "Warning: There is already a file existing with this name. Do you want to overwrite (y/n)?[n] ",
            ("expect", "y"),
            "\nAre you sure you want to continue connecting (yes/no)? ",
            ("expect", "yes"),
            "\nnxos.bin    100%  500MB  50.0MB/s   00:10\n",
            "Copy complete, now saving to disk (please wait)...\nCopy complete.\nnx1# ",
        ],
        prompt="nx1#",
    )
    transcript = run_copy_dialogue(
        channel,
        "copy scp://anonymous:anonymous@10.0.0.5:2222/nxos.bin bootflash: vrf management",
        prompt="nx1#",
        timeout_seconds=5,
        poll_interval=0,
    )
    assert channel.unexpected == []
    assert "Copy complete." in transcript


# --- push_file end to end (fake Netmiko session) ----------------------------


def test_push_file_runs_the_copy_over_an_authenticated_session(monkeypatch):
    channel = ScriptedChannel(_ios_tftp_script())
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: channel)
    events: list[str] = []

    transcript = push_file(
        host="10.0.0.9",
        port=22,
        device_type="cisco_ios",
        username="admin",
        password="cisco123",
        secret=None,
        auth_timeout=5,
        commands=["copy tftp://10.0.0.5/ios.bin flash:"],
        on_authenticated=lambda: events.append("authenticated"),
        on_output=events.append,
        timeout_seconds=5,
    )

    assert events[0].startswith("Connecting to 10.0.0.9:22")
    assert "authenticated" in events
    assert channel.unexpected == []
    assert "12345 bytes copied" in transcript
    assert any("Nothing was installed or reloaded" in e for e in events)


def test_push_file_failure_is_a_command_error_not_an_auth_error(monkeypatch):
    # Login worked; only the copy failed - so callers must never retry
    # this with a fallback credential.
    channel = ScriptedChannel(
        ["Switch#copy tftp://10.0.0.5/ios.bin flash:\n", "%Error opening tftp://10.0.0.5/ios.bin (No such file)\nSwitch#"]
    )
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: channel)

    with pytest.raises(CommandExecutionError) as excinfo:
        push_file(
            host="10.0.0.9",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            auth_timeout=5,
            commands=["copy tftp://10.0.0.5/ios.bin flash:"],
            timeout_seconds=5,
        )
    assert isinstance(excinfo.value, FirmwarePushError)
    assert "No such file" in str(excinfo.value)


def test_push_file_honours_cancel_between_commands(monkeypatch):
    channel = ScriptedChannel(_ios_tftp_script())
    monkeypatch.setattr(collector_module, "ConnectHandler", lambda **kwargs: channel)

    with pytest.raises(CollectionCancelled):
        push_file(
            host="10.0.0.9",
            port=22,
            device_type="cisco_ios",
            username="admin",
            password="cisco123",
            secret=None,
            auth_timeout=5,
            commands=["copy tftp://10.0.0.5/ios.bin flash:"],
            should_cancel=lambda: True,
            timeout_seconds=5,
        )
    assert channel.writes == []
