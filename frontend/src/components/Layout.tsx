import { Link, Outlet, useLocation } from "react-router-dom";

import { isAdmin } from "../auth/session";
import { AccountMenu } from "./AccountMenu";

export function Layout() {
  const admin = isAdmin();

  return (
    <div className="flex min-h-screen">
      <nav className="w-56 shrink-0 border-r bg-white p-4">
        <h2 className="mb-4 font-semibold">DMARC</h2>
        <div className="space-y-1">
          <NavLink to="/">Vue d'ensemble</NavLink>
          <NavLink to="/reports">Rapports</NavLink>
          <NavLink to="/metrics">Métriques</NavLink>
          {admin && <NavLink to="/quarantine">Quarantaine</NavLink>}
          {admin && <NavLink to="/admin/rules">Règles</NavLink>}
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* La barre porte le compte : sa place est en haut à droite, pas noyée en bas
            de la navigation à côté des liens de contenu. */}
        <header className="flex h-14 shrink-0 items-center justify-end border-b bg-white px-6">
          <AccountMenu />
        </header>
        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
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
