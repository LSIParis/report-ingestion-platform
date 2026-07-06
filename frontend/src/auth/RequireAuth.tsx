import { Navigate, Outlet } from "react-router-dom";

import { getClaims } from "./session";

export function RequireAuth({ adminOnly = false }: { adminOnly?: boolean }) {
  const claims = getClaims();
  if (!claims) return <Navigate to="/login" replace />;
  if (adminOnly && claims.role !== "platform_admin") return <Navigate to="/" replace />;
  return <Outlet />;
}
