import { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import { RequirePermission } from "@/components/layout/RequirePermission";
import { LoginPage } from "@/features/login/LoginPage";
import { useAuth } from "@/api/auth";

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
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return <PageLoader />;
  }

  // Unauthenticated visitors landing on any app route see the login page
  // (client-rendered, no automatic navigation to the IdP -- the user must
  // click "Sign in with GitHub" themselves). A live session that expires
  // mid-use is handled separately, by the API client's 401 interceptor,
  // which does perform a full navigation (see api/client.ts).
  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <Suspense fallback={<PageLoader />}>
      <Routes>
        <Route element={<Layout />}>
          <Route
            path="/"
            element={
              <RequirePermission permission="config:read">
                <DashboardPage />
              </RequirePermission>
            }
          />
          <Route
            path="/config"
            element={
              <RequirePermission permission="config:read">
                <ConfigPage />
              </RequirePermission>
            }
          />
          <Route
            path="/tools"
            element={
              <RequirePermission permission="config:read">
                <ToolsPage />
              </RequirePermission>
            }
          />
          <Route
            path="/chat"
            element={
              <RequirePermission permission="agent:invoke">
                <ChatPage />
              </RequirePermission>
            }
          />
          <Route
            path="/peers"
            element={
              <RequirePermission permission="config:read">
                <PeersPage />
              </RequirePermission>
            }
          />
          <Route
            path="/security"
            element={
              <RequirePermission permission="config:read">
                <SecurityPage />
              </RequirePermission>
            }
          />
          <Route path="/guide" element={<GuidePage />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
