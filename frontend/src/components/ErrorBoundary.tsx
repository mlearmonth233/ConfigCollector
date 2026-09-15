import { Component, type ErrorInfo, type ReactNode } from "react";

import { reportClientError } from "../errorReporting";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Catches a render crash anywhere in the page tree, reports it to the
 * server log, and shows a recoverable message instead of a blank screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportClientError("render", `${error.name}: ${error.message}`, `${error.stack ?? ""}\nComponent stack:${info.componentStack ?? ""}`);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="page">
          <h1>Something went wrong</h1>
          <div className="error-banner">
            This page hit an error and stopped: {this.state.error.message}. The details have been written to the
            Packrat log (Settings → Troubleshooting).
          </div>
          <button type="button" onClick={() => this.setState({ error: null })}>
            Try again
          </button>{" "}
          <button type="button" className="secondary" onClick={() => window.location.assign("/devices")}>
            Back to Devices
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
