import { NavLink } from "react-router-dom";

import { useAuth } from "../context/AuthContext";

export function NavBar() {
  const { user, logout } = useAuth();
  if (!user) return null;

  return (
    <header className="navbar">
      <div className="navbar-brand">ConfigCollector</div>
      <nav className="navbar-links">
        <NavLink to="/devices" className={({ isActive }) => (isActive ? "active" : "")}>
          Devices
        </NavLink>
        <NavLink to="/credentials" className={({ isActive }) => (isActive ? "active" : "")}>
          Credentials
        </NavLink>
        <NavLink to="/jobs" className={({ isActive }) => (isActive ? "active" : "")}>
          Jobs
        </NavLink>
      </nav>
      <div className="navbar-user">
        <span>
          {user.email} · {user.org_name}
        </span>
        <button onClick={logout}>Log out</button>
      </div>
    </header>
  );
}
