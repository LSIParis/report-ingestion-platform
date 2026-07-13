import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useMe } from "../api/account";
import { clearSession } from "../auth/session";
import { useTenant } from "../auth/tenant";
import { PasswordDialog } from "./PasswordDialog";

const MIN_PASSWORD = 12;

export function AccountMenu() {
  const nav = useNavigate();
  const { tenant: active, setTenant } = useTenant();
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Fermer au clic extérieur et à Échap : sans ça, le menu reste ouvert derrière
  // la navigation et masque le contenu.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const tenants = me.data?.tenants ?? [];
  const current = tenants.find((t) => t.id === active);
  const isAdmin = me.data?.role === "platform_admin";

  function switchTenant(id: string | null) {
    // id === null : administrateur repassant en vue transverse (aucun X-Tenant-Id
    // envoyé, le serveur bascule alors en accès global).
    // setTenant remonte tout l'arbre de requêtes avec un QueryClient neuf (App.tsx) :
    // aucune donnée du domaine précédent ne peut rester à l'écran.
    setTenant(id);
    setOpen(false);
  }

  function logout() {
    clearSession();
    setTenant(null);
    nav("/login");
  }

  return (
    <div className="relative" ref={box}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2 rounded border bg-white px-3 py-1.5 text-sm hover:bg-gray-50"
      >
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-900 text-xs font-medium text-white">
          {(me.data?.email ?? "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="hidden sm:block max-w-[16rem] truncate text-gray-700">
          {me.data?.email ?? "…"}
        </span>
        <span className="text-xs text-gray-400">▾</span>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-72 rounded border bg-white shadow-lg"
        >
          <div className="border-b px-4 py-3">
            <div className="truncate text-sm font-medium">{me.data?.email}</div>
            <div className="mt-0.5 text-xs text-gray-500">
              {isAdmin ? "Administrateur — accès à tous les domaines" : "Accès en lecture"}
            </div>
          </div>

          <div className="border-b px-4 py-3">
            <div className="mb-2 text-xs uppercase tracking-wide text-gray-500">
              Domaine affiché
            </div>
            {tenants.length === 0 && <div className="text-sm text-gray-500">Aucun domaine</div>}
            {tenants.length === 1 && (
              /* Un seul domaine : il n'y a rien à choisir. Un sélecteur à une option
                 laisse croire qu'on pourrait en voir d'autres. */
              <div className="text-sm">{tenants[0].domain}</div>
            )}
            {tenants.length > 1 && (
              <ul className="-mx-1 max-h-56 space-y-0.5 overflow-y-auto">
                {isAdmin && (
                  <li>
                    <button
                      onClick={() => switchTenant(null)}
                      className={`flex w-full items-center justify-between rounded px-3 py-1.5 text-left text-sm hover:bg-gray-100 ${
                        !active ? "font-medium" : "text-gray-700"
                      }`}
                    >
                      <span>Tous les domaines</span>
                      {!active && <span className="text-xs text-emerald-600">✓</span>}
                    </button>
                  </li>
                )}
                {tenants.map((t) => (
                  <li key={t.id}>
                    <button
                      onClick={() => switchTenant(t.id)}
                      className={`flex w-full items-center justify-between rounded px-3 py-1.5 text-left text-sm hover:bg-gray-100 ${
                        t.id === active ? "font-medium" : "text-gray-700"
                      }`}
                    >
                      <span className="truncate">{t.domain}</span>
                      {t.id === active && <span className="text-xs text-emerald-600">✓</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {tenants.length > 1 && !isAdmin && !current && (
              /* Un lecteur multi-domaines sans sélection : l'API refuse la requête
                 (400, choix ambigu). On le dit ici plutôt que de laisser des pages vides. */
              <p className="mt-2 text-xs text-amber-700">
                Aucun domaine sélectionné — choisissez-en un pour afficher les données.
              </p>
            )}
          </div>

          <div className="py-1">
            {isAdmin && (
              <MenuItem
                onClick={() => {
                  setOpen(false);
                  nav("/settings");
                }}
              >
                Paramètres — comptes
              </MenuItem>
            )}
            <MenuItem
              onClick={() => {
                setOpen(false);
                setPwOpen(true);
              }}
            >
              Changer mon mot de passe
            </MenuItem>
            <MenuItem onClick={logout} tone="text-red-600">
              Se déconnecter
            </MenuItem>
          </div>
        </div>
      )}

      {pwOpen && <PasswordDialog minLength={MIN_PASSWORD} onClose={() => setPwOpen(false)} />}
    </div>
  );
}

function MenuItem({
  children,
  onClick,
  tone,
}: {
  children: React.ReactNode;
  onClick: () => void;
  tone?: string;
}) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className={`block w-full px-4 py-2 text-left text-sm hover:bg-gray-100 ${tone ?? ""}`}
    >
      {children}
    </button>
  );
}
