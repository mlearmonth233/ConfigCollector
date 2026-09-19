import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { extractErrorMessage } from "../api/client";
import { credentialsApi, devicesApi } from "../api/resources";
import type { Credential, Device, TerminalVia } from "../api/types";
import { TerminalSession, type SessionHandle, type SessionState } from "../components/TerminalSession";
import { sortByDeviceName } from "../utils/deviceNameSort";

interface Tab {
  id: string;
  device: Device;
  initialOtp: string;
  via: TerminalVia;
  state: SessionState;
}

let tabCounter = 0;

const STATE_LABEL: Record<SessionState, string> = {
  connecting: "Connecting…",
  connected: "Connected",
  closed: "Disconnected",
};

/** `active` is false while the user is on another page: the component
 * stays mounted (see components/PersistentTerminal) so sessions survive,
 * and refreshes its device list and refits the terminal when shown again. */
export function Terminal({ active = true }: { active?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [devices, setDevices] = useState<Device[]>([]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState(searchParams.get("device") ?? "");
  const [via, setVia] = useState<TerminalVia>(searchParams.get("via") === "console" ? "console" : "management");
  const [otp, setOtp] = useState("");
  const [reconnectOtp, setReconnectOtp] = useState("");
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const handles = useRef(new Map<string, SessionHandle>());

  // Device and credential lists: loaded on mount and again each time the
  // page is shown, since devices may have been added in between.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    (async () => {
      try {
        const [devicesRes, credsRes] = await Promise.all([devicesApi.list(), credentialsApi.list()]);
        if (cancelled) return;
        setDevices(sortByDeviceName(devicesRes.data, (d) => d.name));
        setCredentials(credsRes.data);
        setLoadError(null);
      } catch (err) {
        if (!cancelled) setLoadError(extractErrorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [active]);

  // "SSH" on the Devices or Monitor page navigates here with ?device=...:
  // pick that device up whenever the address changes while we are shown.
  useEffect(() => {
    if (!active) return;
    const device = searchParams.get("device");
    if (!device) return;
    setSelectedId(device);
    setVia(searchParams.get("via") === "console" ? "console" : "management");
  }, [active, searchParams]);

  // Shown again after another page: the surface has a size once more, so
  // the live tab is refitted and focused.
  useEffect(() => {
    if (!active || !activeId) return;
    const frame = requestAnimationFrame(() => handles.current.get(activeId)?.focus());
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const credentialFor = useCallback(
    (device: Device | undefined): Credential | undefined => {
      if (!device) return undefined;
      return (
        (device.credential_id ? credentials.find((c) => c.id === device.credential_id) : undefined) ??
        credentials.find((c) => c.is_default)
      );
    },
    [credentials],
  );

  const selectedDevice = useMemo(() => devices.find((d) => d.id === selectedId), [devices, selectedId]);
  const hasConsole = Boolean(selectedDevice?.console_host);
  const effectiveVia: TerminalVia = hasConsole ? via : "management";
  const consoleIsTelnet = effectiveVia === "console" && selectedDevice?.console_protocol === "telnet";
  const consoleCredential =
    selectedDevice?.console_credential_id ? credentials.find((c) => c.id === selectedDevice.console_credential_id) : undefined;
  const selectedCredential = effectiveVia === "console" ? consoleCredential ?? credentialFor(selectedDevice) : credentialFor(selectedDevice);
  const needsOtp = !consoleIsTelnet && selectedCredential?.mfa_mode === "passcode";

  const activeTab = tabs.find((t) => t.id === activeId) ?? null;
  const activeTelnet = activeTab?.via === "console" && activeTab.device.console_protocol === "telnet";
  const activeNeedsOtp = !activeTelnet && credentialFor(activeTab?.device)?.mfa_mode === "passcode";
  const liveCount = tabs.filter((t) => t.state !== "closed").length;

  // Tabs for the same device are told apart by a counter: "HQ-CORE-SW01 (2)".
  const labels = useMemo(() => {
    const seen = new Map<string, number>();
    const result = new Map<string, { name: string; count: number }>();
    for (const tab of tabs) {
      const n = (seen.get(tab.device.id) ?? 0) + 1;
      seen.set(tab.device.id, n);
      result.set(tab.id, { name: tab.device.name, count: n });
    }
    return result;
  }, [tabs]);

  function openSession() {
    if (!selectedDevice) return;
    tabCounter += 1;
    const id = `session-${tabCounter}-${Date.now()}`;
    setTabs((prev) => [...prev, { id, device: selectedDevice, initialOtp: needsOtp ? otp.trim() : "", via: effectiveVia, state: "connecting" }]);
    setActiveId(id);
    setOtp("");
  }

  const onState = useCallback((id: string, state: SessionState) => {
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, state } : t)));
  }, []);

  const onHandle = useCallback((id: string, handle: SessionHandle | null) => {
    if (handle) handles.current.set(id, handle);
    else handles.current.delete(id);
  }, []);

  function closeTab(id: string) {
    const index = tabs.findIndex((t) => t.id === id);
    const remaining = tabs.filter((t) => t.id !== id);
    setTabs(remaining);
    if (activeId === id) {
      const neighbour = remaining[Math.min(index, remaining.length - 1)];
      setActiveId(neighbour?.id ?? null);
    }
  }

  function closeAll() {
    setTabs([]);
    setActiveId(null);
  }

  function disconnectActive() {
    if (activeId) handles.current.get(activeId)?.disconnect();
  }

  function breakActive() {
    if (activeId) handles.current.get(activeId)?.sendBreak();
  }

  function reconnectActive() {
    if (!activeId) return;
    handles.current.get(activeId)?.reconnect(activeNeedsOtp ? reconnectOtp.trim() : "");
    setReconnectOtp("");
  }

  function handleDeviceChange(id: string) {
    setSelectedId(id);
    setSearchParams(id ? (via === "console" ? { device: id, via } : { device: id }) : {}, { replace: true });
  }

  function handleViaChange(next: TerminalVia) {
    setVia(next);
    if (selectedId) setSearchParams(next === "console" ? { device: selectedId, via: next } : { device: selectedId }, { replace: true });
  }

  function onTabKey(event: KeyboardEvent<HTMLDivElement>, index: number) {
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      if (next) setActiveId(next.id);
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      closeTab(tabs[index].id);
    }
  }

  function onTabAuxClick(event: MouseEvent<HTMLDivElement>, id: string) {
    if (event.button === 1) {
      event.preventDefault();
      closeTab(id);
    }
  }

  const canOpen = Boolean(selectedDevice) && (!needsOtp || otp.trim().length > 0);

  return (
    <div className="page page-wide">
      <h1>Terminal</h1>
      <p className="page-subtitle">
        Interactive SSH sessions in the browser, logging in with the same credential a collection would use
        (the device's own, or the org default), including TACACS+/MFA handling. Open as many devices as you
        need - each one gets its own tab and stays connected while you work in another. A device with a
        console path can be reached out of band through its console server when its management address is
        down. Nothing typed here is recorded by this app.
      </p>
      {loadError && <div className="error-banner">{loadError}</div>}

      <div className="terminal-toolbar">
        <label>
          Device
          <select value={selectedId} onChange={(e) => handleDeviceChange(e.target.value)}>
            <option value="">— pick a device —</option>
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} · {d.host}
              </option>
            ))}
          </select>
        </label>
        {hasConsole && selectedDevice && (
          <label>
            Connect via
            <select value={effectiveVia} onChange={(e) => handleViaChange(e.target.value as TerminalVia)}>
              <option value="management">Management · {selectedDevice.host}:{selectedDevice.port}</option>
              <option value="console">
                Console · {selectedDevice.console_protocol} {selectedDevice.console_host}:{selectedDevice.console_port} (out of band)
              </option>
            </select>
          </label>
        )}
        {needsOtp && (
          <label>
            Passcode for "{selectedCredential?.name}"
            <input
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              placeholder="e.g. 123456"
              onKeyDown={(e) => {
                if (e.key === "Enter" && canOpen) openSession();
              }}
            />
          </label>
        )}
        <div className="terminal-toolbar-actions">
          <button onClick={openSession} disabled={!canOpen}>
            {tabs.length ? "Open in new tab" : "Connect"}
          </button>
          {activeTab && (
            <>
              <span className={`terminal-state terminal-state-${activeTab.state}`}>
                {STATE_LABEL[activeTab.state]}
                {activeTab.state === "connected" && ` to ${activeTab.device.name}`}
              </span>
              {activeTab.state === "closed" ? (
                <>
                  {activeNeedsOtp && (
                    <input
                      className="terminal-otp-inline"
                      value={reconnectOtp}
                      onChange={(e) => setReconnectOtp(e.target.value)}
                      placeholder="New passcode"
                      aria-label="Passcode for reconnect"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && reconnectOtp.trim()) reconnectActive();
                      }}
                    />
                  )}
                  <button className="button-like" onClick={reconnectActive} disabled={activeNeedsOtp && !reconnectOtp.trim()}>
                    Reconnect
                  </button>
                </>
              ) : (
                <>
                  {activeTab.via === "console" && (
                    <button
                      className="button-like"
                      onClick={breakActive}
                      disabled={activeTab.state !== "connected"}
                      title="Send a serial BREAK (for ROMMON / boot interrupts). Telnet sends IAC BRK; SSH asks the console server to break."
                    >
                      Send break
                    </button>
                  )}
                  <button className="button-like" onClick={disconnectActive}>
                    Disconnect
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>

      <div className="terminal-shell">
        {tabs.length > 0 && (
          <div className="terminal-tabs" role="tablist" aria-label="Open sessions">
            {tabs.map((tab, index) => {
              const label = labels.get(tab.id)!;
              const base = label.count > 1 ? `${label.name} (${label.count})` : label.name;
              const text = tab.via === "console" ? `${base} · console` : base;
              return (
                <div
                  key={tab.id}
                  role="tab"
                  tabIndex={0}
                  aria-selected={tab.id === activeId}
                  title={`${tab.device.name} · ${tab.via === "console" ? `console ${tab.device.console_protocol} ${tab.device.console_host}:${tab.device.console_port}` : tab.device.host} - ${STATE_LABEL[tab.state]}`}
                  className={`terminal-tab terminal-tab-${tab.state}${tab.id === activeId ? " active" : ""}`}
                  onClick={() => setActiveId(tab.id)}
                  onAuxClick={(e) => onTabAuxClick(e, tab.id)}
                  onKeyDown={(e) => onTabKey(e, index)}
                >
                  <span className="terminal-tab-dot" aria-hidden="true" />
                  <span className="terminal-tab-name">{text}</span>
                  <button
                    type="button"
                    className="terminal-tab-close"
                    aria-label={`Close ${text}`}
                    title="Close tab"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
                    }}
                  >
                    ×
                  </button>
                </div>
              );
            })}
            <div className="terminal-tabs-actions">
              <span className="terminal-tabs-summary">
                {liveCount} of {tabs.length} live
              </span>
              {tabs.length > 1 && (
                <button type="button" className="terminal-tabs-closeall" onClick={closeAll}>
                  Close all
                </button>
              )}
            </div>
          </div>
        )}
        {tabs.length === 0 && (
          <div className="terminal-placeholder">
            <span className="terminal-placeholder-prompt">$</span> Pick a device and connect to open a session. Connect
            again with another device to add a tab.
          </div>
        )}
        {tabs.length === 0 && <div className="terminal-surface" aria-hidden="true" />}
        {tabs.map((tab) => (
          <TerminalSession
            key={tab.id}
            sessionId={tab.id}
            device={tab.device}
            initialOtp={tab.initialOtp}
            via={tab.via}
            visible={tab.id === activeId}
            onState={onState}
            onHandle={onHandle}
          />
        ))}
      </div>
    </div>
  );
}
