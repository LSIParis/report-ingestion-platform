import { Link, Outlet, useNavigate } from "react-router-dom";

import { clearSession, isAdmin } from "../auth/session";

export function Layout() {
  const nav = useNavigate();
  const admin = isAdmin();

  return (
    <div className="min-h-screen flex">
      <nav className="w-56 bg-white border-r p-4 space-y-1">
        <h2 className="font-semibold mb-4">Rapports</h2>
        <NavLink to="/">Vue d'ensemble</NavLink>
        <NavLink to="/reports">Rapports</NavLink>
        <NavLink to="/metrics">Métriques</NavLink>
        {admin && <NavLink to="/quarantine">Quarantaine</NavLink>}
        {admin && <NavLink to="/admin/rules">Règles</NavLink>}
        <button
          onClick={() => {
            clearSession();
            nav("/login");
          }}
          className="mt-6 text-sm text-gray-500 hover:text-gray-800"
        >
          Déconnexion
        </button>
      </nav>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="block px-2 py-1 rounded hover:bg-gray-100 text-sm">
      {children}
    </Link>
  );
}
