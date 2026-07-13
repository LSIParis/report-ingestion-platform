import { getActiveTenant, getToken } from "../auth/session";

const BASE = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {
  constructor(public status: number, msg: string) {
    super(msg);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${getToken()}`);
  const tenant = getActiveTenant();
  if (tenant) headers.set("X-Tenant-Id", tenant); // validé côté serveur ⊂ claims JWT

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    window.location.href = "/login";
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    // FastAPI renvoie {"detail": "..."} : on extrait le message, sinon les écrans
    // afficheraient le JSON brut à l'utilisateur.
    const body = await res.text();
    let detail = body;
    try {
      detail = JSON.parse(body).detail ?? body;
    } catch {
      /* réponse non JSON : on garde le texte tel quel */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}
