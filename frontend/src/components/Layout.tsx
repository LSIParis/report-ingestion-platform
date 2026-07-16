import { useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import logo from "../assets/logo-lsi.png";
import { useMe } from "../api/account";
import { isAdmin } from "../auth/session";
import { useTenant } from "../auth/tenant";
import { useIdleLogout } from "../auth/useIdleLogout";
import { AccountMenu } from "./AccountMenu";
import { About } from "./About";

export function Layout() {
  useIdleLogout();
  const admin = isAdmin();
  const [apropos, setApropos] = useState(false);

  return (
    <div className="flex min-h-screen">
      <nav className="flex w-56 shrink-0 flex-col border-r bg-white p-4">
        <Link to="/" className="block" title="LSI-Maintenance Mail Dispatch">
          <img src={logo} alt="LSI-Maintenance Mail Dispatch" className="mb-4 w-full h-auto" />
        </Link>
        <div className="space-y-1">
          <NavLink to="/">Vue d'ensemble</NavLink>
          {admin && <NavLink to="/admin/domains">Domaines</NavLink>}
          <NavLink to="/reports">Rapports</NavLink>
          <NavLink to="/metrics">Métriques</NavLink>
          {admin && <NavLink to="/quarantine">Quarantaine</NavLink>}
          {admin && <NavLink to="/admin/rules">Règles</NavLink>}
          {admin && <NavLink to="/alerts">Alertes</NavLink>}
          {admin && <NavLink to="/settings">Paramètres</NavLink>}
        </div>
        <button
          onClick={() => setApropos(true)}
          className="mt-auto pt-4 text-left text-sm text-gray-500 hover:text-gray-900"
        >
          À propos
        </button>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* La barre porte le compte à droite, et à gauche le domaine réellement affiché.
            Sans cette mention, rien à l'écran ne dit de quel client on regarde les
            chiffres — c'est ce qui rendait un changement de domaine sans effet
            impossible à repérer. */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b bg-white px-6">
          <CurrentTenant />
          <AccountMenu />
        </header>
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>

      {apropos && <About onClose={() => setApropos(false)} />}
    </div>
  );
}

function CurrentTenant() {
  const { tenant } = useTenant();
  const me = useMe();
  const domain = me.data?.tenants.find((t) => t.id === tenant)?.domain;
  const admin = me.data?.role === "platform_admin";

  if (!me.data) return <span />;

  if (!tenant) {
    return admin ? (
      <span className="flex items-center gap-2 text-sm">
        <span className="rounded bg-gray-900 px-1.5 py-0.5 text-xs text-white">Vue globale</span>
        <span className="text-gray-500">tous les domaines confondus</span>
      </span>
    ) : (
      <span className="text-sm text-amber-700">Aucun domaine sélectionné</span>
    );
  }

  return (
    <span className="flex items-center gap-2 text-sm">
      <span className="text-xs uppercase tracking-wide text-gray-400">Domaine</span>
      <span className="font-medium">{domain ?? "…"}</span>
    </span>
  );
}

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  const { pathname } = useLocation();
  const active = to === "/" ? pathname === "/" : pathname.startsWith(to);
  return (
    <Link
      to={to}
      className={`block rounded px-2 py-1 text-sm ${
        active ? "bg-gray-100 font-medium" : "text-gray-700 hover:bg-gray-50"
      }`}
    >
      {children}
    </Link>
  );
}
