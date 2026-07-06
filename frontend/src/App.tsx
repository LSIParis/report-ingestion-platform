import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { RequireAuth } from "./auth/RequireAuth";
import { Layout } from "./components/Layout";
import { AdminRules } from "./pages/AdminRules";
import { Login } from "./pages/Login";
import { Metrics } from "./pages/Metrics";
import { Overview } from "./pages/Overview";
import { Quarantine } from "./pages/Quarantine";
import { ReportDetail } from "./pages/ReportDetail";
import { ReportsList } from "./pages/ReportsList";

const qc = new QueryClient();

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
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
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
