"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Shield, Route, Brain, Gauge, Zap, ChevronRight, Hexagon } from "lucide-react";

const FEATURES = [
  {
    id: "FR1",
    title: "Semantic Tool Router",
    desc: "Embedding-based tool selection that bypasses rigid schemas. Routes queries to optimal tools through semantic similarity.",
    icon: Route,
  },
  {
    id: "FR2",
    title: "Verification Gates",
    desc: "Multi-pass output validation with confidence scoring. Ensures factual accuracy before delivery.",
    icon: Shield,
  },
  {
    id: "FR3",
    title: "Adaptive Learning",
    desc: "Persistent pattern store that learns from task outcomes. Strategy recommendations improve over time.",
    icon: Brain,
  },
  {
    id: "FR4",
    title: "Context Budget",
    desc: "Intelligent token management that prevents context overflow. Prioritizes high-value information.",
    icon: Gauge,
  },
  {
    id: "FR5",
    title: "Smart Task Router",
    desc: "ML-based agent assignment using complexity analysis. Routes tasks to the optimal specialist agent.",
    icon: Zap,
  },
];

const AGENTS = [
  { name: "researcher", color: "#00ccdd" },
  { name: "coder", color: "#00dd88" },
  { name: "analyst", color: "#ddaa00" },
  { name: "writer", color: "#dd6600" },
  { name: "planner", color: "#aa44dd" },
];

export function Hero() {
  return (
    <div className="relative min-h-screen forerunner-grid">
      {/* Scan overlay */}
      <div className="forerunner-scan pointer-events-none fixed inset-0 z-50" />

      {/* Beam traces */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="animate-beam absolute left-0 top-[20%] h-px w-full bg-gradient-to-r from-transparent via-cyan/30 to-transparent" />
        <div
          className="animate-beam absolute left-0 top-[60%] h-px w-full bg-gradient-to-r from-transparent via-cyan/20 to-transparent"
          style={{ animationDelay: "2s" }}
        />
        <div
          className="animate-beam absolute left-0 top-[85%] h-px w-full bg-gradient-to-r from-transparent via-cyan/15 to-transparent"
          style={{ animationDelay: "3.5s" }}
        />
      </div>

      {/* Corner glyphs */}
      <div className="pointer-events-none absolute left-6 top-6 animate-flicker text-cyan/30">
        <Hexagon className="h-8 w-8" strokeWidth={1} />
      </div>
      <div className="pointer-events-none absolute right-6 top-6 animate-flicker text-cyan/30" style={{ animationDelay: "1s" }}>
        <Hexagon className="h-8 w-8" strokeWidth={1} />
      </div>

      {/* Main content */}
      <div className="relative z-10 mx-auto max-w-6xl px-6 pb-24 pt-32">
        {/* Header */}
        <div className="mb-20 text-center">
          <div className="mb-4 flex items-center justify-center gap-3">
            <div className="h-px w-16 bg-gradient-to-r from-transparent to-cyan/50" />
            <Badge variant="cyan" pulse>
              v5.0.0
            </Badge>
            <div className="h-px w-16 bg-gradient-to-l from-transparent to-cyan/50" />
          </div>

          <h1 className="forerunner-text forerunner-glow mb-4 text-5xl font-bold tracking-[0.2em] md:text-7xl">
            MENDICANT BIAS
          </h1>

          <p className="mb-2 text-sm uppercase tracking-[0.4em] text-cyan-dim">
            Contender-class Intelligence Middleware
          </p>

          <p className="mx-auto max-w-xl text-xs leading-relaxed text-muted-foreground">
            Five novel middleware capabilities wired into a LangGraph agent framework.
            Semantic routing, verification gates, adaptive learning, context budgeting,
            and smart task assignment -- capabilities no other framework provides.
          </p>

          <div className="mt-10 flex items-center justify-center gap-4">
            <Link href="/workspace">
              <Button size="lg">
                Enter Workspace
                <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </Link>
            <Link href="/workspace/chat">
              <Button variant="outline" size="lg">
                Open Chat
              </Button>
            </Link>
          </div>
        </div>

        {/* Feature cards */}
        <div className="mb-20 grid gap-4 md:grid-cols-5">
          {FEATURES.map((f, i) => (
            <Card key={f.id} glow className="animate-fade-in-up" style={{ animationDelay: `${i * 100}ms` } as React.CSSProperties}>
              <CardHeader>
                <f.icon className="h-4 w-4 text-cyan" strokeWidth={1.5} />
                <Badge variant="cyan">{f.id}</Badge>
              </CardHeader>
              <CardTitle className="mb-2">{f.title}</CardTitle>
              <CardContent>{f.desc}</CardContent>
            </Card>
          ))}
        </div>

        {/* Agent grid */}
        <div className="text-center">
          <p className="mb-6 text-xs uppercase tracking-[0.3em] text-cyan-dim">
            Specialist Agents
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3">
            {AGENTS.map((a) => (
              <div
                key={a.name}
                className="forerunner-border flex items-center gap-2 rounded-sm bg-card px-4 py-2 transition-all hover:bg-muted/50"
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: a.color, boxShadow: `0 0 8px ${a.color}60` }}
                />
                <span className="text-xs font-medium uppercase tracking-wider text-foreground">
                  {a.name}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Bottom decorative line */}
        <div className="mt-20 flex items-center justify-center gap-3 text-cyan-dim/40">
          <div className="h-px w-24 bg-gradient-to-r from-transparent to-cyan/20" />
          <Hexagon className="h-3 w-3" strokeWidth={1} />
          <span className="text-[9px] uppercase tracking-[0.5em]">Forerunner</span>
          <Hexagon className="h-3 w-3" strokeWidth={1} />
          <div className="h-px w-24 bg-gradient-to-l from-transparent to-cyan/20" />
        </div>
      </div>
    </div>
  );
}
