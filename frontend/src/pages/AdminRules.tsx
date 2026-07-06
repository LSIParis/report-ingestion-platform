import { useState } from "react";

import { useAddRule, useRules, useTenants } from "../api/admin";

export function AdminRules() {
  const tenants = useTenants();
  const [tenantId, setTenantId] = useState("");
  const rules = useRules(tenantId);
  const addRule = useAddRule(tenantId);
  const [form, setForm] = useState({ rule_type: "sender", pattern: "", priority: 100 });

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-1">Règles d'identification des domaines</h1>
      <p className="text-sm text-gray-500 mb-4">
        Ajouter une marque ou une variante d'objet = une règle. Aucun déploiement requis.
      </p>

      <select className="border rounded px-2 py-1 mb-4" value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}>
        <option value="">— sélectionner un domaine —</option>
        {tenants.data?.map((t) => (
          <option key={t.id} value={t.id}>{t.name} ({t.domain})</option>
        ))}
      </select>

      {tenantId && (
        <>
          <table className="w-full text-sm mb-6">
            <thead className="text-left text-gray-500 border-b">
              <tr>
                <th className="py-2">Priorité</th>
                <th>Type</th>
                <th>Pattern</th>
                <th>Actif</th>
              </tr>
            </thead>
            <tbody>
              {rules.data?.map((r) => (
                <tr key={r.id} className="border-b">
                  <td className="py-1">{r.priority}</td>
                  <td><code>{r.rule_type}</code></td>
                  <td><code>{r.pattern}</code></td>
                  <td>{r.is_active ? "✓" : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex gap-2 items-end bg-gray-50 border rounded p-4">
            <label className="flex flex-col text-sm">
              Type
              <select className="border rounded px-2 py-1" value={form.rule_type}
                      onChange={(e) => setForm({ ...form, rule_type: e.target.value })}>
                <option value="sender">sender</option>
                <option value="subject_regex">subject_regex</option>
                <option value="keyword">keyword</option>
                <option value="alias">alias</option>
              </select>
            </label>
            <label className="flex flex-col text-sm flex-1">
              Pattern
              <input className="border rounded px-2 py-1" value={form.pattern}
                     placeholder="ex: reports@acme.com  ou  ^\[ACME\]"
                     onChange={(e) => setForm({ ...form, pattern: e.target.value })} />
            </label>
            <label className="flex flex-col text-sm w-24">
              Priorité
              <input type="number" className="border rounded px-2 py-1" value={form.priority}
                     onChange={(e) => setForm({ ...form, priority: +e.target.value })} />
            </label>
            <button disabled={!form.pattern || addRule.isPending}
                    onClick={() => addRule.mutate(form)}
                    className="bg-blue-600 text-white rounded px-4 py-2 disabled:opacity-40">
              Ajouter
            </button>
          </div>
        </>
      )}
    </div>
  );
}
