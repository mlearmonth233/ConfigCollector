import { useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";
import { Terminal } from "../pages/Terminal";

/** The Terminal page lives here, outside the router's page switching, so
 * its SSH sessions survive a visit to another page: leaving /terminal only
 * hides it, coming back shows it again with every tab still connected.
 * Logging out unmounts it, which closes the sessions. */
export function PersistentTerminal() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  if (!user) return null;
  const active = pathname === "/terminal";
  return (
    <div hidden={!active} data-persistent-terminal="">
      <Terminal active={active} />
    </div>
  );
}

/** What the /terminal route itself renders: nothing, the persistent copy
 * above is the visible page. Keeping the route means the catch-all
 * redirect never fires for it and ProtectedRoute still guards it. */
export function TerminalRoute() {
  return null;
}
