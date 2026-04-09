import { Sidebar } from "@/components/workspace/sidebar";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="forerunner-scan pointer-events-none fixed inset-0 z-50" />
        {children}
      </main>
    </div>
  );
}
