"use client";

import { useEffect, useState } from "react";
import { Brain } from "lucide-react";
import { useBrainSocket } from "@/lib/useBrainSocket";
import { fetchBrainMahoraga, fetchBrainState } from "@/lib/api";
import { MahoragaWheel } from "@/components/brain/MahoragaWheel";
import {
  ConnectionStatus,
  CognitivePipeline,
  HookTimeline,
  SessionMetrics,
  AdaptationRules,
  ThoughtStream,
} from "@/components/brain/BrainPanels";

interface MahoragaRule {
  id: string;
  category: string;
  trigger: string;
  action: string;
  confidence: number;
  apply_count: number;
  success_count: number;
}

export default function BrainDashboard() {
  const { events, connected, latestByType } = useBrainSocket();
  const [spinTrigger, setSpinTrigger] = useState(0);
  const [rules, setRules] = useState<MahoragaRule[]>([]);
  const [sessionCount, setSessionCount] = useState(0);
  const [ruleCount, setRuleCount] = useState(0);

  // REST hydration on mount
  useEffect(() => {
    const load = async () => {
      const results = await Promise.allSettled([
        fetchBrainMahoraga(),
        fetchBrainState(),
      ]);

      if (results[0].status === "fulfilled") {
        const data = results[0].value;
        setRules(data.rules ?? []);
        setRuleCount(data.stats?.active_rules ?? data.rules?.length ?? 0);
      }

      if (results[1].status === "fulfilled") {
        const data = results[1].value;
        setSessionCount(data.session_count ?? 0);
      }
    };
    load();
  }, []);

  // Spin wheel on rule_match events
  useEffect(() => {
    const latest = latestByType.rule_match;
    if (latest) {
      setSpinTrigger((prev) => prev + 1);
    }
  }, [latestByType.rule_match]);

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Brain className="h-6 w-6 text-cyan" strokeWidth={1.5} />
          <div>
            <h1 className="forerunner-text text-2xl font-bold tracking-[0.15em]">
              BRAIN
            </h1>
            <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
              Cognitive Pipeline Monitor
            </p>
          </div>
        </div>
        <ConnectionStatus connected={connected} />
      </div>

      {/* Grid layout */}
      <div className="grid gap-4">
        {/* Row 1: Pipeline (full width) */}
        <CognitivePipeline latestByType={latestByType} />

        {/* Row 2: Timeline + Wheel */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
          <HookTimeline events={events} />
          <MahoragaWheel spinTrigger={spinTrigger} />
        </div>

        {/* Row 3: Metrics + Rules */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_2fr]">
          <SessionMetrics
            events={events}
            latestByType={latestByType}
            sessionCount={sessionCount}
            ruleCount={ruleCount}
          />
          <AdaptationRules rules={rules} />
        </div>

        {/* Row 4: Thought Stream (full width) */}
        <ThoughtStream events={events} />
      </div>
    </div>
  );
}
