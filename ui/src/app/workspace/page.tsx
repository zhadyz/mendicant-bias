"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Activity,
  Shield,
  Route,
  Brain,
  Gauge,
  Zap,
  Users,
  Database,
  Hexagon,
  RefreshCw,
} from "lucide-react";
import { fetchStatus, fetchAgents, fetchPatternStats, checkHealth } from "@/lib/api";

interface MiddlewareInfo {
  name: string;
  id: string;
  enabled: boolean;
  description: string;
  icon: typeof Activity;
}

const MIDDLEWARE_DEFS: MiddlewareInfo[] = [
  {
    name: "Semantic Tool Router",
    id: "FR1",
    enabled: true,
    description: "Embedding-based tool selection via semantic similarity",
    icon: Route,
  },
  {
    name: "Verification Gates",
    id: "FR2",
    enabled: true,
    description: "Multi-pass output validation with confidence scoring",
    icon: Shield,
  },
  {
    name: "Adaptive Learning",
    id: "FR3",
    enabled: true,
    description: "Persistent pattern store for strategy optimization",
    icon: Brain,
  },
  {
    name: "Context Budget",
    id: "FR4",
    enabled: true,
    description: "Intelligent token management preventing overflow",
    icon: Gauge,
  },
  {
    name: "Smart Task Router",
    id: "FR5",
    enabled: true,
    description: "ML-based agent assignment via complexity analysis",
    icon: Zap,
  },
];

export default function DashboardPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [agents, setAgents] = useState<Record<string, unknown>[] | null>(null);
  const [patterns, setPatterns] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [h, s, a, p] = await Promise.allSettled([
        checkHealth(),
        fetchStatus(),
        fetchAgents(),
        fetchPatternStats(),
      ]);
      setHealth(h.status === "fulfilled" ? h.value : null);
      setStatus(s.status === "fulfilled" ? s.value : null);
      setAgents(a.status === "fulfilled" ? (a.value?.agents ?? Object.values(a.value ?? {})) : null);
      setPatterns(p.status === "fulfilled" ? p.value : null);
    } catch {
      setError("Failed to connect to Mendicant Bias gateway");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  const systemOnline = health && (health as Record<string, unknown>).system === "mendicant-bias";

  return (
    <div className="relative p-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <div className="mb-1 flex items-center gap-3">
            <Hexagon className="h-5 w-5 text-cyan" strokeWidth={1.5} />
            <h1 className="forerunner-text text-2xl font-bold tracking-[0.15em]">
              DASHBOARD
            </h1>
          </div>
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            System Overview & Middleware Status
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={loadData} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Status bar */}
      <div className="mb-8 grid grid-cols-4 gap-4">
        <Card>
          <CardHeader>
            <Activity className="h-4 w-4 text-cyan" strokeWidth={1.5} />
            <CardTitle>System</CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <Badge variant="warning" pulse>Loading</Badge>
            ) : systemOnline ? (
              <Badge variant="success" pulse>Online</Badge>
            ) : (
              <Badge variant="destructive">Offline</Badge>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <Users className="h-4 w-4 text-cyan" strokeWidth={1.5} />
            <CardTitle>Agents</CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-lg font-bold text-foreground">
              {agents ? (Array.isArray(agents) ? agents.length : Object.keys(agents).length) : "--"}
            </span>
            <span className="ml-2 text-muted-foreground">registered</span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <Database className="h-4 w-4 text-cyan" strokeWidth={1.5} />
            <CardTitle>Patterns</CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-lg font-bold text-foreground">
              {patterns ? (patterns as Record<string, unknown>).total_patterns?.toString() ?? "--" : "--"}
            </span>
            <span className="ml-2 text-muted-foreground">stored</span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <Gauge className="h-4 w-4 text-cyan" strokeWidth={1.5} />
            <CardTitle>Middleware</CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-lg font-bold text-foreground">5</span>
            <span className="ml-2 text-muted-foreground">active layers</span>
          </CardContent>
        </Card>
      </div>

      {/* Middleware cards */}
      <div className="mb-4">
        <h2 className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-dim">
          Middleware Pipeline
        </h2>
        <div className="grid gap-4 md:grid-cols-5">
          {MIDDLEWARE_DEFS.map((mw, i) => {
            const statusData = status as Record<string, Record<string, unknown>> | null;
            const mwStatus = statusData?.middleware?.[mw.id.toLowerCase()] ?? statusData?.[mw.id.toLowerCase()];

            return (
              <Card
                key={mw.id}
                glow
                className="animate-fade-in-up"
                style={{ animationDelay: `${i * 80}ms` } as React.CSSProperties}
              >
                <CardHeader>
                  <mw.icon className="h-4 w-4 text-cyan" strokeWidth={1.5} />
                  <Badge variant="cyan">{mw.id}</Badge>
                </CardHeader>
                <CardTitle className="mb-2">{mw.name}</CardTitle>
                <CardContent>
                  <p className="mb-3">{mw.description}</p>
                  <Badge variant={mw.enabled ? "success" : "destructive"} pulse={mw.enabled}>
                    {mw.enabled ? "Active" : "Inactive"}
                  </Badge>
                  {mwStatus && typeof mwStatus === "object" && (
                    <div className="mt-2 space-y-1 border-t border-border/50 pt-2">
                      {Object.entries(mwStatus as Record<string, unknown>).slice(0, 3).map(([k, v]) => (
                        <div key={k} className="flex justify-between text-[10px]">
                          <span className="text-muted-foreground">{k}</span>
                          <span className="text-foreground">{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Pattern distribution */}
      {patterns && (patterns as Record<string, Record<string, number>>).task_types && (
        <div className="mt-8">
          <h2 className="mb-4 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-dim">
            Pattern Distribution
          </h2>
          <Card>
            <CardContent className="py-4">
              <div className="space-y-3">
                {Object.entries((patterns as Record<string, Record<string, number>>).task_types).map(([type, count]) => {
                  const total = Object.values((patterns as Record<string, Record<string, number>>).task_types).reduce((a, b) => a + b, 0);
                  const pct = total > 0 ? (count / total) * 100 : 0;
                  return (
                    <div key={type}>
                      <div className="mb-1 flex justify-between text-xs">
                        <span className="uppercase tracking-wider text-foreground">{type}</span>
                        <span className="text-muted-foreground">{count} ({pct.toFixed(0)}%)</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-cyan-dim to-cyan transition-all duration-700"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Error state */}
      {error && (
        <div className="mt-8">
          <Card>
            <CardContent className="py-6 text-center">
              <p className="text-xs text-muted-foreground">
                {error}. The gateway may not be running on <span className="text-cyan">localhost:8001</span>.
              </p>
              <Button variant="ghost" size="sm" className="mt-3" onClick={loadData}>
                Retry Connection
              </Button>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
