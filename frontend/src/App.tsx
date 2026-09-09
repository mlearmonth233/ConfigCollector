import { Navigate, Route, Routes } from "react-router-dom";

import { NavBar } from "./components/NavBar";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AuthProvider } from "./context/AuthContext";
import { Commands } from "./pages/Commands";
import { Credentials } from "./pages/Credentials";
import { Devices } from "./pages/Devices";
import { JobDetail } from "./pages/JobDetail";
import { Jobs } from "./pages/Jobs";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";

export function App() {
  return (
    <AuthProvider>
      <NavBar />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route
          path="/devices"
          element={
            <ProtectedRoute>
              <Devices />
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
        <Route path="*" element={<Navigate to="/devices" replace />} />
      </Routes>
    </AuthProvider>
  );
}
