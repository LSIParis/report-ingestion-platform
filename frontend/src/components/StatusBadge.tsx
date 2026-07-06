const MAP: Record<string, { label: string; cls: string }> = {
  ok: { label: "OK", cls: "bg-green-100 text-green-800" },
  parsed_ok: { label: "OK", cls: "bg-green-100 text-green-800" },
  partial: { label: "Partiel", cls: "bg-orange-100 text-orange-800" },
  parsed_partial: { label: "Partiel", cls: "bg-orange-100 text-orange-800" },
  failed: { label: "Échec", cls: "bg-red-100 text-red-800" },
  needs_review: { label: "À revoir", cls: "bg-gray-200 text-gray-700" },
  processing: { label: "En cours", cls: "bg-blue-100 text-blue-800" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = MAP[status] ?? { label: status, cls: "bg-gray-100 text-gray-700" };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${s.cls}`}>{s.label}</span>;
}
