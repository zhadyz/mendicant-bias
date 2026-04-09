"use client";

import { useEffect, useRef } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { BrainEvent } from "@/lib/useBrainSocket";
import {
  Activity,
  Cpu,
  Shield,
  Wrench,
  Zap,
  Users,
  BarChart3,
  Clock,
} from "lucide-react";

// ---------------------------------------------------------------------------
// ConnectionStatus
// ---------------------------------------------------------------------------

interface ConnectionStatusProps {
  connected: boolean;
}

export function ConnectionStatus({ connected }: ConnectionStatusProps) {
  return (
    <Badge
      variant={connected ? "success" : "destructive"}
      pulse={connected}
    >
      {connected ? "LIVE" : "OFFLINE"}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// CognitivePipeline
// ---------------------------------------------------------------------------

const PIPELINE_STAGES = [
  { key: "session_start", label: "Session", icon: Zap },
  { key: "classification", label: "Classify", icon: Cpu },
  { key: "rule_match", label: "Rules", icon: Activity },
  { key: "tool_use", label: "Tool", icon: Wrench },
  { key: "verification", label: "Verify", icon: Shield },
] as const;

interface CognitivePipelineProps {
  latestByType: Record<string, BrainEvent>;
}

export function CognitivePipeline({ latestByType }: CognitivePipelineProps) {
  return (
    <Card>
      <CardHeader>
        <Cpu className="h-4 w-4 text-cyan" strokeWidth={1.5} />
        <CardTitle>Cognitive Pipeline</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-1">
          {PIPELINE_STAGES.map((stage, i) => {
            const active = stage.key in latestByType;
            const latest = latestByType[stage.key];
            const recentMs = latest
              ? Date.now() - new Date(latest.timestamp).getTime()
              : Infinity;
            const hot = recentMs < 5000;

            return (
              <div key={stage.key} className="flex items-center gap-1">
                <div
                  className={cn(
                    "flex items-center gap-1.5 rounded-sm border px-3 py-2 font-mono text-[10px] uppercase tracking-wider transition-all duration-500",
                    hot
                      ? "border-cyan/40 bg-cyan/15 text-cyan shadow-[0_0_12px_oklch(0.78_0.14_195_/_15%)]"
                      : active
                        ? "border-cyan/20 bg-cyan/5 text-cyan/60"
                        : "border-border bg-muted/20 text-muted-foreground/40",
                  )}
                >
                  <stage.icon className="h-3 w-3" strokeWidth={1.5} />
                  {stage.label}
                </div>
                {i < PIPELINE_STAGES.length - 1 && (
                  <span className={cn(
                    "text-[10px]",
                    hot ? "text-cyan" : "text-muted-foreground/30",
                  )}>
                    &rarr;
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// HookTimeline
// ---------------------------------------------------------------------------

const EVENT_BADGE_VARIANT: Record<string, "default" | "cyan" | "success" | "destructive" | "warning"> = {
  session_start: "cyan",
  classification: "cyan",
  rule_match: "default",
  tool_use: "warning",
  verification: "success",
};

interface HookTimelineProps {
  events: BrainEvent[];
}

export function HookTimeline({ events }: HookTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const recent = events.slice(-50);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [recent.length]);

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <Activity className="h-4 w-4 text-cyan" strokeWidth={1.5} />
        <CardTitle>Hook Timeline</CardTitle>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          {events.length} events
        </span>
      </CardHeader>
      <CardContent className="flex-1">
        <div
          ref={scrollRef}
          className="max-h-[320px] space-y-1 overflow-y-auto pr-1"
        >
          {recent.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground/50">
              Awaiting events...
            </p>
          ) : (
            recent.map((ev, i) => {
              const time = new Date(ev.timestamp);
              const ts = time.toLocaleTimeString("en-US", { hour12: false });
              const variant = EVENT_BADGE_VARIANT[ev.type] ?? "default";
              const summary = formatPayloadSummary(ev);

              return (
                <div
                  key={`${ev.timestamp}-${i}`}
                  className="flex items-start gap-2 rounded-sm border border-transparent px-2 py-1 text-xs transition-colors hover:border-border hover:bg-muted/20"
                >
                  <span className="shrink-0 font-mono text-[10px] text-muted-foreground/60">
                    {ts}
                  </span>
                  <Badge variant={variant} className="shrink-0">
                    {ev.type}
                  </Badge>
                  <span className="truncate text-muted-foreground">
                    {summary}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// SessionMetrics
// ---------------------------------------------------------------------------

interface SessionMetricsProps {
  events: BrainEvent[];
  latestByType: Record<string, BrainEvent>;
  sessionCount?: number;
  ruleCount?: number;
}

export function SessionMetrics({
  events,
  latestByType,
  sessionCount = 0,
  ruleCount = 0,
}: SessionMetricsProps) {
  const toolEvents = events.filter((e) => e.type === "tool_use");
  const verifyEvents = events.filter((e) => e.type === "verification");
  const passCount = verifyEvents.filter(
    (e) => e.payload.verdict === "CORRECT" || e.payload.verdict === "PASS",
  ).length;
  const passRate =
    verifyEvents.length > 0
      ? Math.round((passCount / verifyEvents.length) * 100)
      : 0;

  const latestClassification = latestByType.classification;
  const taskType = latestClassification
    ? String(latestClassification.payload.task_type ?? "---")
    : "---";

  const metrics = [
    { label: "Sessions", value: sessionCount || 1, icon: Users },
    { label: "Tools", value: toolEvents.length, icon: Wrench },
    { label: "Verifications", value: verifyEvents.length, icon: Shield },
    { label: "Pass Rate", value: `${passRate}%`, icon: BarChart3 },
    { label: "Task Type", value: taskType, icon: Cpu },
    { label: "Rules", value: ruleCount, icon: Activity },
  ];

  return (
    <Card>
      <CardHeader>
        <BarChart3 className="h-4 w-4 text-cyan" strokeWidth={1.5} />
        <CardTitle>Session Metrics</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-3">
          {metrics.map((m) => (
            <div
              key={m.label}
              className="flex flex-col items-center rounded-sm border border-border bg-muted/20 px-2 py-3"
            >
              <m.icon
                className="mb-1 h-3.5 w-3.5 text-cyan/60"
                strokeWidth={1.5}
              />
              <span className="font-mono text-lg font-bold text-foreground">
                {m.value}
              </span>
              <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
                {m.label}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// AdaptationRules
// ---------------------------------------------------------------------------

interface AdaptationRule {
  id: string;
  category: string;
  trigger: string;
  action: string;
  confidence: number;
  apply_count: number;
  success_count: number;
}

interface AdaptationRulesProps {
  rules: AdaptationRule[];
}

const CATEGORY_VARIANT: Record<string, "default" | "cyan" | "success" | "warning"> = {
  PREFERENCE: "cyan",
  CORRECTION: "warning",
  STYLE: "success",
  TOOL: "default",
  PATTERN: "default",
  WORKFLOW: "cyan",
  AGENT: "success",
};

export function AdaptationRules({ rules }: AdaptationRulesProps) {
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <Activity className="h-4 w-4 text-cyan" strokeWidth={1.5} />
        <CardTitle>Adaptation Rules</CardTitle>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          {rules.length} active
        </span>
      </CardHeader>
      <CardContent className="flex-1">
        <div className="max-h-[320px] space-y-1.5 overflow-y-auto pr-1">
          {rules.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground/50">
              No adaptation rules loaded
            </p>
          ) : (
            rules.map((rule) => (
              <div
                key={rule.id}
                className="rounded-sm border border-border bg-muted/10 px-3 py-2"
              >
                <div className="mb-1 flex items-center gap-2">
                  <Badge variant={CATEGORY_VARIANT[rule.category] ?? "default"}>
                    {rule.category}
                  </Badge>
                  <span className="ml-auto font-mono text-[9px] text-muted-foreground">
                    {rule.apply_count} applies
                  </span>
                </div>
                <p className="text-[11px] text-foreground/80">{rule.trigger}</p>
                <p className="text-[10px] text-muted-foreground">
                  &rarr; {rule.action}
                </p>
                {/* Confidence bar */}
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted/40">
                    <div
                      className="h-full rounded-full bg-cyan/60 transition-all duration-300"
                      style={{ width: `${Math.round(rule.confidence * 100)}%` }}
                    />
                  </div>
                  <span className="font-mono text-[9px] text-muted-foreground">
                    {Math.round(rule.confidence * 100)}%
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// ThoughtStream
// ---------------------------------------------------------------------------

interface ThoughtStreamProps {
  events: BrainEvent[];
}

export function ThoughtStream({ events }: ThoughtStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const recent = events.slice(-80);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [recent.length]);

  return (
    <Card>
      <CardHeader>
        <Clock className="h-4 w-4 text-cyan" strokeWidth={1.5} />
        <CardTitle>Thought Stream</CardTitle>
      </CardHeader>
      <CardContent>
        <div
          ref={scrollRef}
          className="forerunner-grid max-h-[180px] overflow-y-auto rounded-sm bg-[#050507] p-3"
        >
          {recent.length === 0 ? (
            <p className="font-mono text-[11px] text-emerald-800/60">
              {">"} awaiting cognitive events...
            </p>
          ) : (
            recent.map((ev, i) => {
              const time = new Date(ev.timestamp);
              const ts = time.toLocaleTimeString("en-US", { hour12: false });
              const summary = formatPayloadSummary(ev);
              return (
                <div
                  key={`${ev.timestamp}-${i}`}
                  className="font-mono text-[11px] leading-5 text-emerald-500/80"
                >
                  <span className="text-emerald-800/60">[{ts}]</span>{" "}
                  <span className="uppercase text-emerald-400/90">
                    {ev.type}
                  </span>{" "}
                  {ev.session_id && (
                    <span className="text-emerald-800/50">
                      {ev.session_id.slice(0, 8)}
                    </span>
                  )}{" "}
                  <span className="text-emerald-600/70">{summary}</span>
                </div>
              );
            })
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatPayloadSummary(ev: BrainEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case "session_start":
      return `${p.rule_count ?? 0} rules, ${p.fact_count ?? 0} facts, gw=${p.gateway_status ?? "?"}`;
    case "classification":
      return `${p.task_type ?? "?"} [tier=${p.verification_tier ?? "?"}]`;
    case "rule_match":
      return `${p.rule_count ?? 0} rules matched [${(p.categories as string[] | undefined)?.join(", ") ?? ""}]`;
    case "tool_use":
      return `${p.tool_name ?? "?"} (${p.stage ?? "?"}) ${p.file_path ? String(p.file_path).split("/").pop() : ""}`;
    case "verification":
      return `${p.verdict ?? "?"} confidence=${p.confidence ?? "?"} [${p.tier ?? "?"}]`;
    default:
      return JSON.stringify(p).slice(0, 80);
  }
}
