import { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";

const DashboardPage = lazy(() =>
  import("@/features/dashboard/DashboardPage").then((m) => ({ default: m.DashboardPage }))
);
const ConfigPage = lazy(() =>
  import("@/features/config/ConfigPage").then((m) => ({ default: m.ConfigPage }))
);
const ToolsPage = lazy(() =>
  import("@/features/tools/ToolsPage").then((m) => ({ default: m.ToolsPage }))
);
const ChatPage = lazy(() =>
  import("@/features/chat/ChatPage").then((m) => ({ default: m.ChatPage }))
);
const PeersPage = lazy(() =>
  import("@/features/peers/PeersPage").then((m) => ({ default: m.PeersPage }))
);
const SecurityPage = lazy(() =>
  import("@/features/security/SecurityPage").then((m) => ({ default: m.SecurityPage }))
);
const GuidePage = lazy(() =>
  import("@/features/guide/GuidePage").then((m) => ({ default: m.GuidePage }))
);

function PageLoader() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[var(--primary)]" />
    </div>
  );
}

export function App() {
  return (
    <Suspense fallback={<PageLoader />}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/config" element={<ConfigPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/peers" element={<PeersPage />} />
          <Route path="/security" element={<SecurityPage />} />
          <Route path="/guide" element={<GuidePage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
