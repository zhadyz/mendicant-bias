"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  MessageSquare,
  Users,
  Layers,
  Hexagon,
  ChevronLeft,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/workspace", label: "Dashboard", icon: LayoutDashboard },
  { href: "/workspace/chat", label: "Chat", icon: MessageSquare },
  { href: "/workspace/agents", label: "Agents", icon: Users },
  { href: "/workspace/middleware", label: "Middleware", icon: Layers },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-screen w-56 flex-col border-r border-border bg-card">
      {/* Logo */}
      <div className="flex items-center gap-3 border-b border-border px-4 py-4">
        <Hexagon className="h-5 w-5 text-cyan" strokeWidth={1.5} />
        <div>
          <p className="forerunner-text text-xs font-bold tracking-[0.15em]">MENDICANT</p>
          <p className="text-[9px] uppercase tracking-[0.2em] text-cyan-dim">Workspace</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-4">
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.href === "/workspace"
                ? pathname === "/workspace"
                : pathname.startsWith(item.href);

            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={cn(
                    "flex items-center gap-3 rounded-sm px-3 py-2 text-xs font-medium uppercase tracking-wider transition-all duration-200",
                    isActive
                      ? "border border-cyan/20 bg-cyan/10 text-cyan shadow-[0_0_12px_oklch(0.78_0.14_195_/_8%)]"
                      : "border border-transparent text-muted-foreground hover:border-border hover:bg-muted/30 hover:text-foreground"
                  )}
                >
                  <item.icon className="h-3.5 w-3.5" strokeWidth={1.5} />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer */}
      <div className="border-t border-border px-4 py-3">
        <Link
          href="/"
          className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground transition-colors hover:text-cyan"
        >
          <ChevronLeft className="h-3 w-3" />
          Back to Landing
        </Link>
      </div>
    </aside>
  );
}
