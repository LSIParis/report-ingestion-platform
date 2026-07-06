import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { getClaims, setSession } from "../auth/session";

export function Login() {
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const res = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      setSession(res.access_token);
      const claims = getClaims();
      // mono-tenant : fixe le tenant actif automatiquement
      if (claims && claims.tenant_ids.length === 1) {
        localStorage.setItem("active_tenant", claims.tenant_ids[0]);
      }
      nav("/");
    } catch {
      setError("Identifiants invalides");
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={submit} className="bg-white border rounded p-8 w-80 space-y-4">
        <h1 className="text-lg font-semibold">Connexion</h1>
        <input className="border rounded w-full px-3 py-2" placeholder="Email"
               value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="border rounded w-full px-3 py-2" type="password" placeholder="Mot de passe"
               value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button className="bg-blue-600 text-white rounded w-full py-2">Se connecter</button>
      </form>
    </div>
  );
}
