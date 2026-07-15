import { useState } from "react";
import { useNavigate } from "react-router-dom";

import logo from "../assets/logo-lsi.png";
import { api } from "../api/client";
import { getClaims, setSession } from "../auth/session";
import { useTenant } from "../auth/tenant";

export function Login() {
  const nav = useNavigate();
  const { setTenant } = useTenant();
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
      // Un seul domaine rattaché : il n'y a rien à choisir, on le fixe. Sinon on laisse
      // à null — l'admin obtient la vue transverse, le lecteur multi-domaines devra
      // choisir (l'API refuse un choix ambigu). On passe par le contexte, pas par
      // localStorage : sinon l'état React et le stockage divergent dès la connexion.
      setTenant(claims && claims.tenant_ids.length === 1 ? claims.tenant_ids[0] : null);
      nav("/");
    } catch {
      setError("Identifiants invalides");
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <form onSubmit={submit} className="bg-white border rounded p-8 w-80 space-y-4">
        <img src={logo} alt="LSI-Maintenance Mail Dispatch"
             className="mx-auto w-48 h-auto" />
        <h1 className="text-lg font-semibold text-center">Connexion</h1>
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
