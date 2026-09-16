"""A minimal telnet client channel for reverse-telnet console ports.

Console servers and Cisco async lines expose each serial port as a TCP
port speaking telnet (RFC 854). The device's console prompt appears the
moment the socket opens - there is no login to the server itself - so all
this has to do is: negotiate the handful of options a terminal cares
about, strip the IAC sequences out of the byte stream, escape 0xFF on the
way out, and send a real BREAK (IAC BRK) when asked, which is what a
ROMMON recovery needs.

TelnetChannel exposes the same small surface as a paramiko Channel
(recv_ready / recv / closed / exit_status_ready / sendall / resize_pty /
close) so api/terminal.py's bridge drives both the same way.
"""

from __future__ import annotations

import select
import socket
import struct

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
BRK = 243
OPT_ECHO, OPT_SGA, OPT_TTYPE, OPT_NAWS = 1, 3, 24, 31
TTYPE_IS, TTYPE_SEND = 0, 1

# Options we agree to when the server offers them (WILL -> DO).
ACCEPT_FROM_SERVER = {OPT_ECHO, OPT_SGA}
# Options we agree to provide when the server asks (DO -> WILL).
PROVIDE = {OPT_NAWS, OPT_TTYPE, OPT_SGA}


class TelnetNegotiator:
    """Pure byte-stream state machine: feed() returns (clean_data,
    replies_to_send). Separate from the socket so it can be unit-tested."""

    def __init__(self, cols: int = 120, rows: int = 32, term: str = "xterm-256color") -> None:
        self.cols, self.rows, self.term = cols, rows, term
        self._pending = b""
        self.naws_enabled = False

    def feed(self, data: bytes) -> tuple[bytes, bytes]:
        buf = self._pending + data
        self._pending = b""
        out = bytearray()
        reply = bytearray()
        i = 0
        while i < len(buf):
            b = buf[i]
            if b != IAC:
                out.append(b)
                i += 1
                continue
            if i + 1 >= len(buf):
                self._pending = buf[i:]
                break
            cmd = buf[i + 1]
            if cmd == IAC:
                out.append(IAC)
                i += 2
            elif cmd in (DO, DONT, WILL, WONT):
                if i + 2 >= len(buf):
                    self._pending = buf[i:]
                    break
                opt = buf[i + 2]
                reply += self._answer(cmd, opt)
                i += 3
            elif cmd == SB:
                end = buf.find(bytes([IAC, SE]), i + 2)
                if end < 0:
                    self._pending = buf[i:]
                    break
                reply += self._subnegotiate(buf[i + 2 : end])
                i = end + 2
            else:
                i += 2  # NOP, GA, etc. - a two-byte command with nothing to do
        return bytes(out), bytes(reply)

    def _answer(self, cmd: int, opt: int) -> bytes:
        if cmd == WILL:
            return bytes([IAC, DO if opt in ACCEPT_FROM_SERVER else DONT, opt])
        if cmd == DO:
            if opt in PROVIDE:
                if opt == OPT_NAWS:
                    self.naws_enabled = True
                    return bytes([IAC, WILL, opt]) + self.naws()
                return bytes([IAC, WILL, opt])
            return bytes([IAC, WONT, opt])
        if cmd == DONT and opt == OPT_NAWS:
            self.naws_enabled = False
        return b""  # WONT / DONT need no reply

    def _subnegotiate(self, body: bytes) -> bytes:
        if len(body) >= 2 and body[0] == OPT_TTYPE and body[1] == TTYPE_SEND:
            return bytes([IAC, SB, OPT_TTYPE, TTYPE_IS]) + self.term.encode() + bytes([IAC, SE])
        return b""

    def naws(self) -> bytes:
        payload = struct.pack("!HH", self.cols, self.rows).replace(bytes([IAC]), bytes([IAC, IAC]))
        return bytes([IAC, SB, OPT_NAWS]) + payload + bytes([IAC, SE])

    @staticmethod
    def escape(data: bytes) -> bytes:
        return data.replace(bytes([IAC]), bytes([IAC, IAC]))


class TelnetChannel:
    """Paramiko-Channel-shaped wrapper over a connected telnet socket."""

    def __init__(self, sock: socket.socket, cols: int, rows: int) -> None:
        self._sock = sock
        self._sock.setblocking(False)
        self._neg = TelnetNegotiator(cols, rows)
        self.closed = False

    def recv_ready(self) -> bool:
        if self.closed:
            return False
        readable, _, _ = select.select([self._sock], [], [], 0)
        return bool(readable)

    def recv(self, n: int) -> bytes:
        try:
            raw = self._sock.recv(n)
        except (BlockingIOError, InterruptedError):
            return b""  # nothing right now
        except OSError:
            self.closed = True
            return b""
        if not raw:
            self.closed = True
            return b""
        data, reply = self._neg.feed(raw)
        if reply:
            self._send_raw(reply)
        return data

    def exit_status_ready(self) -> bool:
        return self.closed

    def sendall(self, data: bytes) -> None:
        self._send_raw(TelnetNegotiator.escape(data))

    def send_break(self) -> None:
        self._send_raw(bytes([IAC, BRK]))

    def resize_pty(self, width: int, height: int) -> None:
        self._neg.cols, self._neg.rows = width, height
        if self._neg.naws_enabled:
            self._send_raw(self._neg.naws())

    def _send_raw(self, data: bytes) -> None:
        if self.closed:
            return
        try:
            self._sock.setblocking(True)
            self._sock.sendall(data)
        except OSError:
            self.closed = True
        finally:
            try:
                self._sock.setblocking(False)
            except OSError:
                pass

    def close(self) -> None:
        self.closed = True
        try:
            self._sock.close()
        except OSError:
            pass


def open_telnet(host: str, port: int, cols: int, rows: int, timeout: float = 10.0) -> TelnetChannel:
    """Blocking connect; raises OSError (with a readable message) on failure."""
    sock = socket.create_connection((host, port), timeout=timeout)
    return TelnetChannel(sock, cols, rows)
