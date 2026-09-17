import { useEffect, useRef } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal as XTerm } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

import { absoluteApiBase, getToken } from "../api/client";
import type { Device, TerminalVia } from "../api/types";

export type SessionState = "connecting" | "connected" | "closed";

export interface SessionHandle {
  disconnect: () => void;
  reconnect: (otp: string) => void;
  focus: () => void;
  sendBreak: () => void;
}

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
  return absoluteApiBase().replace(/^http/, "ws");
}

interface Props {
  sessionId: string;
  device: Device;
  /** The passcode to log in with on the first connection (empty if the
   * credential doesn't use one). Reconnects ask for a fresh one. */
  initialOtp: string;
  /** Management address (default) or the device's out-of-band console path. */
  via: TerminalVia;
  visible: boolean;
  onState: (sessionId: string, state: SessionState) => void;
  onHandle: (sessionId: string, handle: SessionHandle | null) => void;
}

/** One SSH session: its own xterm instance and WebSocket, kept alive while
 * hidden behind another tab. The terminal is created once on mount and
 * disposed on unmount (closing the tab); Reconnect reuses the same
 * terminal so the previous session's output stays scrolled above. */
export function TerminalSession({ sessionId, device, initialOtp, via, visible, onState, onHandle }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    // Creation is deferred by one frame: React StrictMode (development)
    // runs mount -> cleanup -> mount immediately, and cancelling a pending
    // frame is free, whereas opening and instantly disposing an xterm
    // (which schedules its own timers on open) throws in its viewport.
    let setupTeardown: (() => void) | null = null;
    const frame = requestAnimationFrame(() => {
      setupTeardown = setup(container);
    });
    return () => {
      cancelAnimationFrame(frame);
      setupTeardown?.();
    };
    // The session is created exactly once per tab; device/otp are fixed for its lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  function setup(container: HTMLDivElement): () => void {
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
    term.open(container);
    termRef.current = term;
    fitRef.current = fit;

    const sendResize = () => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
    };
    const refit = () => {
      // A hidden tab has no size; fitting it would shrink the PTY to
      // nothing. It's re-fitted the moment it becomes visible.
      if (!visibleRef.current || container.clientWidth === 0) return;
      fit.fit();
      sendResize();
    };

    const connect = (otp: string) => {
      wsRef.current?.close();
      onState(sessionId, "connecting");
      refit();
      const token = getToken() ?? "";
      const query = new URLSearchParams({ token, cols: String(term.cols), rows: String(term.rows) });
      if (otp.trim()) query.set("otp", otp.trim());
      if (via === "console") query.set("via", "console");
      const ws = new WebSocket(`${websocketBase()}/api/terminal/${device.id}?${query.toString()}`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      let opened = false;
      ws.onopen = () => {
        opened = true;
        onState(sessionId, "connected");
        if (visibleRef.current) term.focus();
        sendResize();
      };
      ws.onmessage = (event) => {
        if (typeof event.data === "string") term.write(event.data);
        else term.write(new Uint8Array(event.data as ArrayBuffer));
      };
      ws.onerror = () => {
        term.write("\r\n\x1b[31mConnection error - is the backend reachable?\x1b[0m\r\n");
      };
      ws.onclose = (event) => {
        if (wsRef.current !== ws) return; // superseded by a reconnect
        if (!opened) {
          // The handshake itself failed: nothing from the backend ever
          // arrived, so say where we tried to go.
          term.write(
            `\r\n\x1b[31mCould not open the terminal connection to ${websocketBase()} (code ${event.code}). ` +
              "Check that the backend is running and that nothing between the browser and it blocks WebSockets.\x1b[0m\r\n",
          );
        } else if (event.code !== 1000 && event.code !== 1005) {
          // The backend printed the reason just before closing; the code
          // helps match it to packrat-api.log (1008 refused, 1011 failed).
          term.write(`\r\n\x1b[90m[Session closed - code ${event.code}]\x1b[0m\r\n`);
        } else {
          term.write("\r\n\x1b[90m[Session closed]\x1b[0m\r\n");
        }
        onState(sessionId, "closed");
      };
    };

    const inputDisposable = term.onData((data) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data }));
    });

    const observer = new ResizeObserver(refit);
    observer.observe(container);

    onHandle(sessionId, {
      disconnect: () => wsRef.current?.close(),
      reconnect: (otp) => {
        term.write("\r\n");
        connect(otp);
      },
      focus: () => {
        refit();
        term.focus();
      },
      sendBreak: () => {
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "break" }));
      },
    });
    connect(initialOtp);

    return () => {
      onHandle(sessionId, null);
      observer.disconnect();
      inputDisposable.dispose();
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
      termRef.current = null;
      fitRef.current = null;
      term.dispose();
    };
  }

  // Coming to the front: the container now has a size, so fit and focus.
  useEffect(() => {
    if (!visible) return;
    const frame = requestAnimationFrame(() => {
      const fit = fitRef.current;
      const term = termRef.current;
      const container = containerRef.current;
      if (!fit || !term || !container || container.clientWidth === 0) return;
      fit.fit();
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      }
      term.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [visible]);

  return <div ref={containerRef} className="terminal-surface" hidden={!visible} data-session={sessionId} />;
}
