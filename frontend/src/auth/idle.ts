// La seule regle metier de l'inactivite, isolee en fonction pure : evidente a lire, et
// testable telle quelle si un runner de test est ajoute un jour. Le reste (ecoute des
// evenements, minuteur, redirection) est de la plomberie de hook.
export const IDLE_MINUTES = 10;
export const IDLE_LIMIT_MS = IDLE_MINUTES * 60 * 1000;

/** Vrai si aucune activite depuis plus de `limiteMs`. */
export function estInactif(dernierMs: number, maintenantMs: number, limiteMs: number): boolean {
  return maintenantMs - dernierMs > limiteMs;
}
