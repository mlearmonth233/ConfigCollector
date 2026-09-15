import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { API_BASE_URL, extractErrorMessage, getToken } from "../api/client";
import { credentialsApi, devicesApi } from "../api/resources";
import type { Credential, Device } from "../api/types";
import { sortByDeviceName } from "../utils/deviceNameSort";

type SessionState = "idle" | "connecting" | "connected" | "closed";

const TERMINAL_THEME = {
  background: "#0b1220",
  foreground: "#d7dee9",
  cursor: "#22d3ee",
  cursorAccent: "#0b1220",
  selectionBackground: "rgba(34, 211, 238, 0.28)",
  black: "#0b1220",
  brightBlack: "#475569",
  red: "#f87171",
  green: "#4ade80",
  yellow: "#facc15",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#22d3ee",
  white: "#e2e8f0",
};

function websocketBase(): string {
  return API_BASE_URL.replace(/^http/, "ws");
}

export function Terminal() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [devices, setDevices] = useState<Device[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState(searchParams.get("device") ?? "");
  const [otp, setOtp] = useState("");
  const [state, setState] = useState<SessionState>("idle");

  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [devicesRes, credsRes] = await Promise.all([devicesApi.list(), credentialsApi.list()]);
        if (cancelled) return;
        setDevices(sortByDeviceName(devicesRes.data, (d) => d.name));
        setCredentials(credsRes.data);
      } catch (err) {
        if (!cancelled) setLoadError(extractErrorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedDevice = useMemo(() => devices.find((d) => d.id === selectedId), [devices, selectedId]);
  const effectiveCredential = useMemo(() => {
    if (!selectedDevice) return undefined;
    return (
      (selectedDevice.credential_id ? credentials.find((c) => c.id === selectedDevice.credential_id) : undefined) ??
      credentials.find((c) => c.is_default)
    );
  }, [selectedDevice, credentials]);
  const needsOtp = effectiveCredential?.mfa_mode === "passcode";

  function teardown() {
    observerRef.current?.disconnect();
    observerRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    termRef.current?.dispose();
    termRef.current = null;
    fitRef.current = null;
  }

  // Dispose of any live session if the page is left.
  useEffect(() => teardown, []);

  function sendResize(ws: WebSocket, term: XTerm) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    }
  }

  function connect() {
    if (!selectedDevice || !containerRef.current) return;
    teardown();
    setState("connecting");

    const term = new XTerm({
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      fontSize: 13,
      lineHeight: 1.2,
      scrollback: 5000,
      theme: TERMINAL_THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const token = getToken() ?? "";
    const query = new URLSearchParams({ token, cols: String(term.cols), rows: String(term.rows) });
    if (needsOtp && otp.trim()) query.set("otp", otp.trim());
    const ws = new WebSocket(`${websocketBase()}/api/terminal/${selectedDevice.id}?${query.toString()}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setState("connected");
      term.focus();
      sendResize(ws, term);
    };
    ws.onmessage = (event) => {
      if (typeof event.data === "string") term.write(event.data);
      else term.write(new Uint8Array(event.data as ArrayBuffer));
    };
    ws.onerror = () => {
      term.write("\r\n\x1b[31mConnection error - is the backend reachable?\x1b[0m\r\n");
    };
    ws.onclose = () => {
      term.write("\r\n\x1b[90m[Session closed]\x1b[0m\r\n");
      setState("closed");
      setOtp("");
    };
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data }));
    });

    const observer = new ResizeObserver(() => {
      fit.fit();
      sendResize(ws, term);
    });
    observer.observe(containerRef.current);
    observerRef.current = observer;
  }

  function disconnect() {
    wsRef.current?.close();
  }

  function handleDeviceChange(id: string) {
    setSelectedId(id);
    setSearchParams(id ? { device: id } : {}, { replace: true });
  }

  const live = state === "connecting" || state === "connected";

  return (
    <div className="page page-wide">
      <h1>Terminal</h1>
      <p className="page-subtitle">
        An interactive SSH session to a device, right here in the browser - logging in with the same
        credential a collection would use (the device's own, or the org default), including
        TACACS+/MFA handling. Nothing typed here is recorded by this app.
      </p>
      {loadError && <div className="error-banner">{loadError}</div>}

      <div className="terminal-toolbar">
        <label>
          Device
          <select value={selectedId} onChange={(e) => handleDeviceChange(e.target.value)} disabled={live}>
            <option value="">— pick a device —</option>
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} · {d.host}
              </option>
            ))}
          </select>
        </label>
        {needsOtp && (
          <label>
            Passcode for "{effectiveCredential?.name}"
            <input value={otp} onChange={(e) => setOtp(e.target.value)} placeholder="e.g. 123456" disabled={live} />
          </label>
        )}
        <div className="terminal-toolbar-actions">
          {!live ? (
            <button onClick={connect} disabled={!selectedDevice || (needsOtp && !otp.trim())}>
              {state === "closed" ? "Reconnect" : "Connect"}
            </button>
          ) : (
            <button className="button-like" onClick={disconnect}>
              Disconnect
            </button>
          )}
          <span className={`terminal-state terminal-state-${state}`}>
            {state === "idle" && "Not connected"}
            {state === "connecting" && "Connecting…"}
            {state === "connected" && `Connected to ${selectedDevice?.name ?? "device"}`}
            {state === "closed" && "Disconnected"}
          </span>
        </div>
      </div>

      <div className="terminal-shell">
        {state === "idle" && (
          <div className="terminal-placeholder">
            <span className="terminal-placeholder-prompt">$</span> Pick a device and connect to open a session.
          </div>
        )}
        <div ref={containerRef} className="terminal-surface" />
      </div>
    </div>
  );
}
