"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Users, RefreshCw, Hexagon, ChevronDown, ChevronUp, Wrench, Globe } from "lucide-react";
import { fetchAgents, fetchAgent } from "@/lib/api";

interface AgentProfile {
  name: string;
  description?: string;
  full_description?: string;
  domains?: string[];
  tools?: string[];
  color?: string;
  model?: string;
  system_prompt_preview?: string;
}

const FALLBACK_AGENTS: AgentProfile[] = [
  {
    name: "researcher",
    description: "Deep web research and information synthesis",
    domains: ["research", "analysis", "fact-checking"],
    tools: ["web_search", "crawl_tool"],
    color: "#00ccdd",
  },
  {
    name: "coder",
    description: "Code generation, debugging, and technical implementation",
    domains: ["coding", "debugging", "architecture"],
    tools: ["python_repl", "file_tools"],
    color: "#00dd88",
  },
  {
    name: "analyst",
    description: "Data analysis, visualization, and statistical reasoning",
    domains: ["data", "statistics", "visualization"],
    tools: ["python_repl", "chart_tools"],
    color: "#ddaa00",
  },
  {
    name: "writer",
    description: "Content creation, editing, and document structuring",
    domains: ["writing", "editing", "documentation"],
    tools: ["file_tools", "markdown_tools"],
    color: "#dd6600",
  },
  {
    name: "planner",
    description: "Task decomposition, project planning, and coordination",
    domains: ["planning", "decomposition", "coordination"],
    tools: ["task_tools"],
    color: "#aa44dd",
  },
];

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentProfile[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, AgentProfile>>({});
  const [loading, setLoading] = useState(true);

  async function loadAgents() {
    setLoading(true);
    try {
      const data = await fetchAgents();
      const list = data?.agents ?? (Array.isArray(data) ? data : Object.values(data ?? {}));
      setAgents(list.length > 0 ? list : FALLBACK_AGENTS);
    } catch {
      setAgents(FALLBACK_AGENTS);
    } finally {
      setLoading(false);
    }
  }

  async function toggleExpand(name: string) {
    if (expanded === name) {
      setExpanded(null);
      return;
    }
    setExpanded(name);
    if (!details[name]) {
      try {
        const data = await fetchAgent(name);
        setDetails((prev) => ({ ...prev, [name]: data }));
      } catch {
        // Use existing data
      }
    }
  }

  useEffect(() => {
    loadAgents();
  }, []);

  return (
    <div className="relative p-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <div className="mb-1 flex items-center gap-3">
            <Users className="h-5 w-5 text-cyan" strokeWidth={1.5} />
            <h1 className="forerunner-text text-2xl font-bold tracking-[0.15em]">
              AGENTS
            </h1>
          </div>
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            Specialist Agent Roster
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={loadAgents} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Agent grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {agents.map((agent, i) => {
          const isExpanded = expanded === agent.name;
          const detail = details[agent.name];
          const agentColor = agent.color || "#00ccdd";

          return (
            <Card
              key={agent.name}
              glow
              onClick={() => toggleExpand(agent.name)}
              className="animate-fade-in-up"
              style={{ animationDelay: `${i * 60}ms` } as React.CSSProperties}
            >
              <CardHeader>
                <span
                  className="h-3 w-3 rounded-full"
                  style={{
                    backgroundColor: agentColor,
                    boxShadow: `0 0 10px ${agentColor}60`,
                  }}
                />
                <CardTitle style={{ color: agentColor }}>{agent.name}</CardTitle>
                <div className="ml-auto">
                  {isExpanded ? (
                    <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </div>
              </CardHeader>

              <CardContent>
                <p className="mb-3">{agent.description || "Specialist agent"}</p>

                {/* Domains */}
                {agent.domains && agent.domains.length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                      <Globe className="h-3 w-3" />
                      Domains
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {agent.domains.map((d) => (
                        <Badge key={d} variant="default">{d}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                {/* Tools */}
                {agent.tools && agent.tools.length > 0 && (
                  <div className="mb-3">
                    <div className="mb-1.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                      <Wrench className="h-3 w-3" />
                      Tools
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {agent.tools.map((t) => (
                        <Badge key={t} variant="cyan">{t}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                {/* Expanded details */}
                {isExpanded && detail && (
                  <div className="mt-3 border-t border-border/50 pt-3 space-y-2">
                    {detail.full_description && (
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {detail.full_description}
                      </p>
                    )}
                    {detail.model && (
                      <div className="flex items-center justify-between text-[10px]">
                        <span className="text-muted-foreground">Model</span>
                        <Badge variant="default">{detail.model}</Badge>
                      </div>
                    )}
                    {detail.system_prompt_preview && (
                      <div className="mt-2 rounded-sm bg-muted/30 p-2">
                        <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                          System Prompt Preview
                        </p>
                        <p className="text-[11px] leading-relaxed text-foreground/80 line-clamp-4">
                          {detail.system_prompt_preview}
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Empty state */}
      {!loading && agents.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <Hexagon className="mx-auto mb-4 h-8 w-8 text-cyan-dim" strokeWidth={1} />
            <p className="text-xs text-muted-foreground">
              No agents registered. Start the Mendicant Bias gateway to populate.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
