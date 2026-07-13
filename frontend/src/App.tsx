import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMemo } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./auth/RequireAuth";
import { TenantProvider, useTenant } from "./auth/tenant";
import { Layout } from "./components/Layout";
import { AdminRules } from "./pages/AdminRules";
import { Login } from "./pages/Login";
import { Metrics } from "./pages/Metrics";
import { Overview } from "./pages/Overview";
import { Quarantine } from "./pages/Quarantine";
import { ReportDetail } from "./pages/ReportDetail";
import { ReportsList } from "./pages/ReportsList";
import { Settings } from "./pages/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <TenantProvider>
        <TenantScopedApp />
      </TenantProvider>
    </BrowserRouter>
  );
}

/* Un QueryClient NEUF par domaine, et un remontage complet de l'arbre via `key`.

   C'est ce qui garantit qu'aucune donnée d'un domaine ne survit au passage à un autre,
   sur AUCUNE page — présente ou future. L'alternative (ajouter le tenant dans chaque
   queryKey) repose sur la vigilance : le jour où on l'oublie sur une nouvelle page,
   l'application affiche les données d'un client sous le nom d'un autre. Ce n'est pas
   une fuite (l'API et la RLS refusent l'accès), mais c'est indistinguable d'une fuite
   pour qui regarde l'écran. */
function TenantScopedApp() {
  const { tenant } = useTenant();
  const qc = useMemo(() => new QueryClient(), [tenant]);

  return (
    <QueryClientProvider client={qc} key={tenant ?? "__all__"}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route path="/" element={<Overview />} />
            <Route path="/reports" element={<ReportsList />} />
            <Route path="/reports/:id" element={<ReportDetail />} />
            <Route path="/metrics" element={<Metrics />} />
          </Route>
        </Route>
        <Route element={<RequireAuth adminOnly />}>
          <Route element={<Layout />}>
            <Route path="/quarantine" element={<Quarantine />} />
            <Route path="/admin/rules" element={<AdminRules />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Route>
      </Routes>
    </QueryClientProvider>
  );
}
