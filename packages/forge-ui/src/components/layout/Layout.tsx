import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { GuidePanel } from "@/components/guide/GuidePanel";
import { GuideTour } from "@/components/guide/GuideTour";
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
          <Outlet />
        </main>
      </div>
      <GuidePanel />
      <GuideTour />
    </div>
  );
}
