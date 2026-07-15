import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { GuidePanel } from "@/components/guide/GuidePanel";
import { GuideTour } from "@/components/guide/GuideTour";
import { DriftBanner } from "@/features/config/DriftBanner";
import { useUIStore } from "@/stores/uiStore";

export function Layout() {
  const { darkMode } = useUIStore();

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-auto p-6">
          {/* Mounted globally (not just on the Config page) so overlay drift
              is visible no matter where in the app the operator is working --
              a tool/peer edit made from the Tools or Peers page can drift the
              running instance from Git just as a Config-page save can.
              DriftBanner renders null when there's nothing to show, and
              `space-y-6 > * + *` only margins an element with a preceding
              DOM sibling, so no gap appears when the banner is hidden. */}
          <div className="space-y-6">
            <DriftBanner />
            <Outlet />
          </div>
        </main>
      </div>
      <GuidePanel />
      <GuideTour />
    </div>
  );
}
