interface Claims {
  sub: string;
  role: string;
  tenant_ids: string[];
}

export const getToken = () => localStorage.getItem("token") ?? "";
export const getActiveTenant = () => localStorage.getItem("active_tenant");

export function getClaims(): Claims | null {
  const t = getToken();
  if (!t) return null;
  try {
    return JSON.parse(atob(t.split(".")[1]));
  } catch {
    return null;
  }
}

export const isAdmin = () => getClaims()?.role === "platform_admin";

export function setSession(token: string, activeTenant?: string) {
  localStorage.setItem("token", token);
  if (activeTenant) localStorage.setItem("active_tenant", activeTenant);
}

export function clearSession() {
  localStorage.removeItem("token");
  localStorage.removeItem("active_tenant");
}
