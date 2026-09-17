import { NavLink } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

export function NavBar() {
  const { user, logout } = useAuth();
  if (!user) return null;

  return (
    <header className="navbar">
      <div className="navbar-brand">Packrat</div>
      <nav className="navbar-links">
        <NavLink to="/devices" className={({ isActive }) => (isActive ? "active" : "")}>
          Devices
        </NavLink>
        <NavLink to="/monitor" className={({ isActive }) => (isActive ? "active" : "")}>
          Monitor
        </NavLink>
        <NavLink to="/alerts" className={({ isActive }) => (isActive ? "active" : "")}>
          Alerts
        </NavLink>
        <NavLink to="/inventory" className={({ isActive }) => (isActive ? "active" : "")}>
          Inventory
        </NavLink>
        <NavLink to="/credentials" className={({ isActive }) => (isActive ? "active" : "")}>
          Credentials
        </NavLink>
        <NavLink to="/commands" className={({ isActive }) => (isActive ? "active" : "")}>
          Commands
        </NavLink>
        <NavLink to="/jobs" className={({ isActive }) => (isActive ? "active" : "")}>
          Jobs
        </NavLink>
        <NavLink to="/firmware" className={({ isActive }) => (isActive ? "active" : "")}>
          Firmware
        </NavLink>
        <NavLink to="/dns-check" className={({ isActive }) => (isActive ? "active" : "")}>
          DNS Check
        </NavLink>
        <NavLink to="/snmp" className={({ isActive }) => (isActive ? "active" : "")}>
          SNMP
        </NavLink>
        <NavLink to="/terminal" className={({ isActive }) => (isActive ? "active" : "")}>
          Terminal
        </NavLink>
        <NavLink to="/schedules" className={({ isActive }) => (isActive ? "active" : "")}>
          Schedules
        </NavLink>
        <NavLink to="/settings" className={({ isActive }) => (isActive ? "active" : "")}>
          Settings
        </NavLink>
        <NavLink to="/help" className={({ isActive }) => (isActive ? "active" : "")}>
          Help
        </NavLink>
      </nav>
      <div className="navbar-user">
        <span className="navbar-user-email" title={`${user.email} · ${user.org_name}`}>
          {user.email}
          <span className="navbar-user-org"> · {user.org_name}</span>
        </span>
        <button onClick={logout} title={`Log out ${user.email}`}>
          Log out
        </button>
      </div>
    </header>
  );
}
