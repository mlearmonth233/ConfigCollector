import { Navigate, Route, Routes } from "react-router-dom";

import { BackToTop } from "./components/BackToTop";
import { NavBar } from "./components/NavBar";
import { PersistentTerminal, TerminalRoute } from "./components/PersistentTerminal";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AuthProvider } from "./context/AuthContext";
import { LicenceProvider } from "./context/LicenceContext";
import { Alerts } from "./pages/Alerts";
import { Commands } from "./pages/Commands";
import { Credentials } from "./pages/Credentials";
import { Devices } from "./pages/Devices";
import { DnsCheck } from "./pages/DnsCheck";
import { DnsCheckJobDetail } from "./pages/DnsCheckJobDetail";
import { Firmware } from "./pages/Firmware";
import { FirmwareJobDetail } from "./pages/FirmwareJobDetail";
import { JobDetail } from "./pages/JobDetail";
import { Jobs } from "./pages/Jobs";
import { Login } from "./pages/Login";
import { Monitor } from "./pages/Monitor";
import { Register } from "./pages/Register";
import { Schedules } from "./pages/Schedules";
import { Help } from "./pages/Help";
import { Inventory } from "./pages/Inventory";
import { Settings } from "./pages/Settings";
import { Snmp } from "./pages/Snmp";
import { SnmpJobDetail } from "./pages/SnmpJobDetail";

export function App() {
  return (
    <AuthProvider>
      <LicenceProvider>
      <NavBar />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route
          path="/monitor"
          element={
            <ProtectedRoute>
              <Monitor />
            </ProtectedRoute>
          }
        />
        <Route
          path="/alerts"
          element={
            <ProtectedRoute>
              <Alerts />
            </ProtectedRoute>
          }
        />
        <Route
          path="/devices"
          element={
            <ProtectedRoute>
              <Devices />
            </ProtectedRoute>
          }
        />
        <Route
          path="/inventory"
          element={
            <ProtectedRoute>
              <Inventory />
            </ProtectedRoute>
          }
        />
        <Route
          path="/credentials"
          element={
            <ProtectedRoute>
              <Credentials />
            </ProtectedRoute>
          }
        />
        <Route
          path="/commands"
          element={
            <ProtectedRoute>
              <Commands />
            </ProtectedRoute>
          }
        />
        <Route
          path="/jobs"
          element={
            <ProtectedRoute>
              <Jobs />
            </ProtectedRoute>
          }
        />
        <Route
          path="/jobs/:jobId"
          element={
            <ProtectedRoute>
              <JobDetail />
            </ProtectedRoute>
          }
        />
        <Route
          path="/firmware"
          element={
            <ProtectedRoute>
              <Firmware />
            </ProtectedRoute>
          }
        />
        <Route
          path="/firmware/jobs/:jobId"
          element={
            <ProtectedRoute>
              <FirmwareJobDetail />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dns-check"
          element={
            <ProtectedRoute>
              <DnsCheck />
            </ProtectedRoute>
          }
        />
        <Route
          path="/dns-check/jobs/:jobId"
          element={
            <ProtectedRoute>
              <DnsCheckJobDetail />
            </ProtectedRoute>
          }
        />
        <Route
          path="/terminal"
          element={
            <ProtectedRoute>
              <TerminalRoute />
            </ProtectedRoute>
          }
        />
        <Route
          path="/schedules"
          element={
            <ProtectedRoute>
              <Schedules />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <ProtectedRoute>
              <Settings />
            </ProtectedRoute>
          }
        />
        <Route
          path="/snmp"
          element={
            <ProtectedRoute>
              <Snmp />
            </ProtectedRoute>
          }
        />
        <Route
          path="/snmp/jobs/:jobId"
          element={
            <ProtectedRoute>
              <SnmpJobDetail />
            </ProtectedRoute>
          }
        />
        <Route
          path="/help"
          element={
            <ProtectedRoute>
              <Help />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/devices" replace />} />
      </Routes>
      <PersistentTerminal />
      <BackToTop />
      </LicenceProvider>
    </AuthProvider>
  );
}
