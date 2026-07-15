import logo from "../assets/logo-lsi.png";

/** Boite « A propos » : nom, version, SHA du build, copyright. Ouverte depuis la barre
 *  laterale. Le SHA est tronque a 7 caracteres (comme un `git log --oneline`). */
export function About({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-sm rounded border bg-white p-6 text-center"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <img src={logo} alt="LSI-Maintenance Mail Dispatch" className="mx-auto w-40 h-auto" />
        <dl className="mt-4 space-y-1 text-sm">
          <div className="flex justify-between">
            <dt className="text-gray-500">Version</dt>
            <dd className="font-mono">{__APP_VERSION__}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">Build</dt>
            <dd className="font-mono">{__BUILD_SHA__.slice(0, 7)}</dd>
          </div>
        </dl>
        <p className="mt-4 text-xs text-gray-500">© LSI-Maintenance {new Date().getFullYear()}</p>
        <button
          onClick={onClose}
          className="mt-4 w-full rounded border py-2 text-sm"
        >
          Fermer
        </button>
      </div>
    </div>
  );
}
