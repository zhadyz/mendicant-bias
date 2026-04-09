"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Layers,
  Route,
  Shield,
  Brain,
  Gauge,
  Zap,
  RefreshCw,
  Settings,
  BarChart3,
} from "lucide-react";
import { fetchMiddleware, fetchPatternStats } from "@/lib/api";

interface MiddlewareDef {
  id: string;
  name: string;
  icon: typeof Route;
  description: string;
  configKeys: string[];
}

const MW_DEFS: MiddlewareDef[] = [
  {
    id: "FR1",
    name: "Semantic Tool Router",
    icon: Route,
    description:
      "Uses embedding vectors to match user queries to the most appropriate tools. Bypasses rigid tool-name matching with cosine similarity over a semantic index. Supports dynamic tool registration and fallback chains.",
    configKeys: ["similarity_threshold", "embedding_model", "fallback_enabled", "max_candidates"],
  },
  {
    id: "FR2",
    name: "Verification Gates",
    icon: Shield,
    description:
      "Multi-pass verification pipeline that validates agent outputs before delivery. Checks factual consistency, source attribution, confidence scoring, and format compliance. Catches hallucinations and unsupported claims.",
    configKeys: ["confidence_threshold", "max_retries", "strict_mode", "verification_model"],
  },
  {
    id: "FR3",
    name: "Adaptive Learning",
    icon: Brain,
    description:
      "Persistent pattern store backed by a local database. Records task type, strategy chosen, outcome quality, and timing. Recommends proven strategies for similar future tasks. Improves over time without retraining.",
    configKeys: ["store_backend", "learning_rate", "pattern_ttl_days", "min_samples"],
  },
  {
    id: "FR4",
    name: "Context Budget",
    icon: Gauge,
    description:
      "Monitors token consumption across the agent pipeline and enforces budget limits. Prioritizes high-value context, compresses verbose sections, and prevents context window overflow before it degrades performance.",
    configKeys: ["max_tokens", "compression_ratio", "priority_decay", "reserve_tokens"],
  },
  {
    id: "FR5",
    name: "Smart Task Router",
    icon: Zap,
    description:
      "Classifies incoming tasks by complexity, domain, and required capabilities. Routes each task to the best-suited specialist agent. Uses heuristic scoring with optional ML reranking for ambiguous tasks.",
    configKeys: ["classifier_model", "complexity_threshold", "multi_agent_enabled", "routing_strategy"],
  },
];

export default function MiddlewarePage() {
  const [middleware, setMiddleware] = useState<Record<string, unknown> | null>(null);
  const [patterns, setPatterns] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadData() {
    setLoading(true);
    try {
      const [mw, p] = await Promise.allSettled([fetchMiddleware(), fetchPatternStats()]);
      setMiddleware(mw.status === "fulfilled" ? mw.value : null);
      setPatterns(p.status === "fulfilled" ? p.value : null);
    } catch {
      // silently fail
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  const taskTypes = (patterns as Record<string, Record<string, number>> | null)?.task_types;

  return (
    <div className="relative p-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <div className="mb-1 flex items-center gap-3">
            <Layers className="h-5 w-5 text-cyan" strokeWidth={1.5} />
            <h1 className="forerunner-text text-2xl font-bold tracking-[0.15em]">
              MIDDLEWARE
            </h1>
          </div>
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            Intelligence Pipeline Configuration
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={loadData} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Pipeline visualization */}
      <div className="mb-8 flex items-center gap-2 overflow-x-auto pb-2">
        {MW_DEFS.map((mw, i) => (
          <div key={mw.id} className="flex items-center gap-2">
            <div className="forerunner-border flex items-center gap-2 whitespace-nowrap rounded-sm bg-card px-3 py-1.5">
              <mw.icon className="h-3 w-3 text-cyan" strokeWidth={1.5} />
              <span className="text-[10px] font-semibold uppercase tracking-wider text-cyan">
                {mw.id}
              </span>
            </div>
            {i < MW_DEFS.length - 1 && (
              <div className="h-px w-6 bg-gradient-to-r from-cyan/40 to-cyan/10" />
            )}
          </div>
        ))}
      </div>

      {/* Middleware detail cards */}
      <div className="mb-10 space-y-4">
        {MW_DEFS.map((mw, i) => {
          const mwData =
            middleware &&
            ((middleware as Record<string, Record<string, unknown>>)?.[mw.id.toLowerCase()] ??
              (middleware as Record<string, Record<string, unknown>>)?.middleware?.[mw.id.toLowerCase()]);

          return (
            <Card
              key={mw.id}
              glow
              className="animate-fade-in-up"
              style={{ animationDelay: `${i * 80}ms` } as React.CSSProperties}
            >
              <div className="flex flex-col gap-4 md:flex-row md:items-start">
                {/* Left: info */}
                <div className="flex-1">
                  <CardHeader>
                    <mw.icon className="h-5 w-5 text-cyan" strokeWidth={1.5} />
                    <Badge variant="cyan">{mw.id}</Badge>
                    <CardTitle className="text-sm">{mw.name}</CardTitle>
                    <Badge variant="success" pulse className="ml-auto">
                      Active
                    </Badge>
                  </CardHeader>
                  <CardContent>
                    <p className="mb-4 leading-relaxed">{mw.description}</p>
                  </CardContent>
                </div>

                {/* Right: config */}
                <div className="w-full rounded-sm bg-muted/20 p-4 md:w-72">
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                    <Settings className="h-3 w-3" />
                    Configuration
                  </div>
                  <div className="space-y-2">
                    {mw.configKeys.map((key) => {
                      const val =
                        mwData && typeof mwData === "object"
                          ? (mwData as Record<string, unknown>)[key]
                          : undefined;
                      return (
                        <div key={key} className="flex items-center justify-between text-[11px]">
                          <span className="text-muted-foreground">{key}</span>
                          <span className="font-medium text-foreground">
                            {val !== undefined ? String(val) : "--"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      {/* Pattern store visualization */}
      <div>
        <div className="mb-4 flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-cyan" strokeWidth={1.5} />
          <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-dim">
            Pattern Store Distribution
          </h2>
        </div>

        {taskTypes && Object.keys(taskTypes).length > 0 ? (
          <Card>
            <CardContent className="py-5">
              <div className="space-y-4">
                {Object.entries(taskTypes)
                  .sort(([, a], [, b]) => b - a)
                  .map(([type, count]) => {
                    const total = Object.values(taskTypes).reduce((a, b) => a + b, 0);
                    const pct = total > 0 ? (count / total) * 100 : 0;
                    const maxCount = Math.max(...Object.values(taskTypes));
                    const barPct = maxCount > 0 ? (count / maxCount) * 100 : 0;

                    return (
                      <div key={type}>
                        <div className="mb-1.5 flex items-center justify-between text-xs">
                          <span className="font-medium uppercase tracking-wider text-foreground">
                            {type}
                          </span>
                          <div className="flex items-center gap-3">
                            <span className="text-muted-foreground">{count} patterns</span>
                            <Badge variant="cyan">{pct.toFixed(1)}%</Badge>
                          </div>
                        </div>
                        <div className="h-2 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-cyan-dim to-cyan transition-all duration-1000"
                            style={{ width: `${barPct}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
              </div>
              <div className="mt-4 flex items-center justify-between border-t border-border/50 pt-3 text-[10px] text-muted-foreground">
                <span>
                  Total: {Object.values(taskTypes).reduce((a, b) => a + b, 0)} patterns
                </span>
                <span>{Object.keys(taskTypes).length} task types</span>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="py-8 text-center">
              <p className="text-xs text-muted-foreground">
                {loading
                  ? "Loading pattern data..."
                  : "No pattern data available. Run tasks through Mendicant Bias to populate the pattern store."}
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
