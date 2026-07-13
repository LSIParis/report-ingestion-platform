import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { getActiveTenant } from "./session";

/* Le domaine affiché est un ÉTAT REACT, pas seulement une entrée de localStorage.

   Sinon : changer de domaine écrit dans localStorage, mais aucun composant ne se
   re-rend et les requêtes en cache gardent la même clé — TanStack Query resert alors
   les données du domaine précédent. C'est le bug observé : l'admin changeait de
   domaine et voyait les mêmes chiffres.

   La correction ne consiste PAS à ajouter le tenant dans chaque clé de requête : on
   l'oublierait à la première page ajoutée, et l'oubli serait invisible — des données
   d'un autre client affichées sous le nom du bon. On repart d'un QueryClient neuf à
   chaque changement (voir App.tsx) : aucune donnée d'un domaine ne peut survivre au
   passage à un autre, quelle que soit la page. */

interface Ctx {
  tenant: string | null; // null = admin en vue transverse (aucun X-Tenant-Id envoyé)
  setTenant: (id: string | null) => void;
}

const TenantCtx = createContext<Ctx>({ tenant: null, setTenant: () => {} });

export function TenantProvider({ children }: { children: React.ReactNode }) {
  const [tenant, setState] = useState<string | null>(getActiveTenant());

  const setTenant = useCallback((id: string | null) => {
    // localStorage reste la source de vérité pour api/client.ts (qui lit l'en-tête à
    // chaque appel) et pour survivre à un rechargement de page.
    if (id) localStorage.setItem("active_tenant", id);
    else localStorage.removeItem("active_tenant");
    setState(id);
  }, []);

  const value = useMemo(() => ({ tenant, setTenant }), [tenant, setTenant]);
  return <TenantCtx.Provider value={value}>{children}</TenantCtx.Provider>;
}

export const useTenant = () => useContext(TenantCtx);
