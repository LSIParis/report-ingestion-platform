import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { IDLE_LIMIT_MS, estInactif } from "./idle";
import { clearSession } from "./session";

const CLE = "last_activity";
const CONTROLE_MS = 30_000;        // frequence du controle periodique

// Horodatage de derniere activite. En localStorage : survit au rechargement et vaut pour
// tous les onglets (une activite ici garde la session vivante la-bas). Repli en memoire si
// localStorage est inaccessible (navigation privee stricte) -- on ne casse jamais la
// session pour ca.
let memoire = Date.now();

function marquer(): void {
  memoire = Date.now();
  try {
    localStorage.setItem(CLE, String(memoire));
  } catch {
    /* localStorage indisponible : on garde la valeur en memoire */
  }
}

function dernier(): number {
  try {
    const v = localStorage.getItem(CLE);
    if (v) return Number(v);
  } catch {
    /* idem */
  }
  return memoire;
}

/** Deconnecte apres IDLE_MINUTES sans activite. A monter dans la coquille authentifiee. */
export function useIdleLogout(): void {
  const nav = useNavigate();

  useEffect(() => {
    marquer();      // on repart d'une activite fraiche a l'entree

    // Throttle : au plus une ecriture par seconde, sinon mousemove sature localStorage.
    let dernierMarquage = 0;
    const surActivite = () => {
      const t = Date.now();
      if (t - dernierMarquage > 1000) {
        dernierMarquage = t;
        marquer();
      }
    };

    const deconnecterSiInactif = () => {
      if (estInactif(dernier(), Date.now(), IDLE_LIMIT_MS)) {
        clearSession();
        nav("/login", { replace: true });
      }
    };

    const evenements = ["mousemove", "mousedown", "keydown", "scroll", "touchstart"] as const;
    evenements.forEach((e) => window.addEventListener(e, surActivite, { passive: true }));
    // Au retour sur l'onglet (reveil apres veille) : controle immediat, sans attendre le tic.
    document.addEventListener("visibilitychange", deconnecterSiInactif);
    const minuteur = window.setInterval(deconnecterSiInactif, CONTROLE_MS);

    return () => {
      evenements.forEach((e) => window.removeEventListener(e, surActivite));
      document.removeEventListener("visibilitychange", deconnecterSiInactif);
      window.clearInterval(minuteur);
    };
  }, [nav]);
}
