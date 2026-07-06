import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAssignTenant, useQuarantine } from "../api/emails";
import { useTenants } from "../api/admin";

export function Quarantine() {
  const [sp, setSp] = useSearchParams();
  const page = Number(sp.get("page") ?? 1);
  const { data, isLoading } = useQuarantine(page);
  const tenants = useTenants();
  const assign = useAssignTenant();
  const [choice, setChoice] = useState<Record<string, string>>({});

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-1">File de quarantaine</h1>
      <p className="text-sm text-gray-500 mb-4">
        Mails dont le domaine n'a pas pu être identifié automatiquement. Aucune donnée n'est
        visible d'un client tant que non résolue.
      </p>

      {isLoading ? (
        <p>Chargement…</p>
      ) : !data!.items.length ? (
        <p className="text-green-600">✓ Aucun mail en attente.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-gray-500 border-b">
            <tr>
              <th className="py-2">Reçu</th>
              <th>Expéditeur</th>
              <th>Objet</th>
              <th>Assigner à</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data!.items.map((e) => (
              <tr key={e.id} className="border-b align-top">
                <td className="py-2">{new Date(e.received_at).toLocaleString()}</td>
                <td>{e.from_address}</td>
                <td className="max-w-xs truncate" title={e.subject}>{e.subject}</td>
                <td>
                  <select className="border rounded px-2 py-1" value={choice[e.id] ?? ""}
                          onChange={(ev) => setChoice({ ...choice, [e.id]: ev.target.value })}>
                    <option value="">— choisir —</option>
                    {tenants.data?.map((t) => (
                      <option key={t.id} value={t.id}>{t.name} ({t.domain})</option>
                    ))}
                  </select>
                </td>
                <td>
                  <button
                    disabled={!choice[e.id] || assign.isPending}
                    onClick={() => assign.mutate({ id: e.id, tenant_id: choice[e.id] })}
                    className="bg-blue-600 text-white rounded px-3 py-1 disabled:opacity-40"
                  >
                    Assigner & rejouer
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex gap-2 mt-4 items-center">
        <button disabled={page <= 1} onClick={() => setSp({ page: String(page - 1) })}
                className="disabled:opacity-40">←</button>
        <span className="text-sm">Page {page} · {data?.total ?? 0} en attente</span>
        <button disabled={(data?.items.length ?? 0) < 50} onClick={() => setSp({ page: String(page + 1) })}
                className="disabled:opacity-40">→</button>
      </div>
    </div>
  );
}
