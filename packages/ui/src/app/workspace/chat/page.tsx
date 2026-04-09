"use client";

import { useState, useRef, useEffect, type FormEvent, type KeyboardEvent } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  MessageSquare,
  Send,
  Route,
  Shield,
  Brain,
  Zap,
  Hexagon,
  Loader2,
  AlertTriangle,
} from "lucide-react";
import { classifyTask, routeTools, recommendStrategy, verifyOutput } from "@/lib/api";

type MessageRole = "user" | "system";

interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: Date;
  data?: {
    classification?: Record<string, unknown>;
    tools?: Record<string, unknown>;
    recommendation?: Record<string, unknown>;
    verification?: Record<string, unknown>;
    error?: string;
  };
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "system",
      content:
        "Mendicant Bias middleware interface active. Enter a task description to see classification, tool routing, and strategy recommendations from the five middleware layers.",
      timestamp: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSubmit(e?: FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      // Run all middleware queries in parallel
      const [classResult, toolsResult, recResult] = await Promise.allSettled([
        classifyTask(text),
        routeTools(text),
        recommendStrategy(text),
      ]);

      // Build response
      const data: ChatMessage["data"] = {};
      const sections: string[] = [];

      // Classification (FR5 - Smart Task Router)
      if (classResult.status === "fulfilled") {
        data.classification = classResult.value;
        const c = classResult.value;
        sections.push(
          `TASK CLASSIFICATION [FR5]\n` +
            `  Type: ${c.task_type ?? c.classification ?? "unknown"}\n` +
            `  Complexity: ${c.complexity ?? "N/A"}\n` +
            `  Agent: ${c.recommended_agent ?? c.agent ?? "N/A"}\n` +
            `  Confidence: ${c.confidence != null ? (c.confidence * 100).toFixed(0) + "%" : "N/A"}`
        );
      }

      // Tool routing (FR1 - Semantic Tool Router)
      if (toolsResult.status === "fulfilled") {
        data.tools = toolsResult.value;
        const t = toolsResult.value;
        const toolList =
          t.tools ?? t.recommended_tools ?? t.routes ?? [];
        sections.push(
          `TOOL ROUTING [FR1]\n` +
            `  Matched tools: ${Array.isArray(toolList) ? toolList.map((x: Record<string, string>) => x.name ?? x.tool ?? x).join(", ") : JSON.stringify(toolList)}`
        );
      }

      // Strategy recommendation (FR3 - Adaptive Learning)
      if (recResult.status === "fulfilled") {
        data.recommendation = recResult.value;
        const r = recResult.value;
        sections.push(
          `STRATEGY RECOMMENDATION [FR3]\n` +
            `  Strategy: ${r.strategy ?? r.recommended_strategy ?? "N/A"}\n` +
            `  Based on: ${r.pattern_count ?? r.similar_patterns ?? 0} similar patterns\n` +
            `  Historical success: ${r.success_rate != null ? (r.success_rate * 100).toFixed(0) + "%" : "N/A"}`
        );
      }

      const allFailed =
        classResult.status === "rejected" &&
        toolsResult.status === "rejected" &&
        recResult.status === "rejected";

      const systemMsg: ChatMessage = {
        id: `system-${Date.now()}`,
        role: "system",
        content: allFailed
          ? "All middleware endpoints unreachable. Ensure the Mendicant Bias gateway is running on localhost:8001."
          : sections.join("\n\n"),
        timestamp: new Date(),
        data: allFailed ? { error: "Connection failed" } : data,
      };

      setMessages((prev) => [...prev, systemMsg]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: `error-${Date.now()}`,
          role: "system",
          content: "Failed to process request. The gateway may be offline.",
          timestamp: new Date(),
          data: { error: "Connection failed" },
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-border px-6 py-4">
        <div className="flex items-center gap-3">
          <MessageSquare className="h-5 w-5 text-cyan" strokeWidth={1.5} />
          <h1 className="forerunner-text text-lg font-bold tracking-[0.15em]">
            MIDDLEWARE CHAT
          </h1>
          <Badge variant="cyan" pulse>
            Live
          </Badge>
        </div>
        <p className="mt-1 text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
          Interactive middleware pipeline query interface
        </p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] ${
                msg.role === "user"
                  ? "rounded-sm border border-cyan/20 bg-cyan/5 px-4 py-3"
                  : "rounded-sm border border-border bg-card px-4 py-3"
              }`}
            >
              {/* Role indicator */}
              <div className="mb-2 flex items-center gap-2">
                {msg.role === "system" ? (
                  <Hexagon className="h-3 w-3 text-cyan" strokeWidth={1.5} />
                ) : null}
                <span className="text-[9px] uppercase tracking-[0.2em] text-muted-foreground">
                  {msg.role === "user" ? "You" : "Mendicant Bias"}
                </span>
                <span className="text-[9px] text-muted-foreground/50">
                  {msg.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>

              {/* Content */}
              {msg.data?.error ? (
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 text-destructive" />
                  <p className="text-xs leading-relaxed text-destructive/80">{msg.content}</p>
                </div>
              ) : (
                <pre className="whitespace-pre-wrap text-xs leading-relaxed text-foreground/90 font-mono">
                  {msg.content}
                </pre>
              )}

              {/* Data badges */}
              {msg.data && !msg.data.error && (
                <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border/30 pt-2">
                  {msg.data.classification && (
                    <Badge variant="cyan">
                      <Zap className="h-2.5 w-2.5" /> FR5
                    </Badge>
                  )}
                  {msg.data.tools && (
                    <Badge variant="cyan">
                      <Route className="h-2.5 w-2.5" /> FR1
                    </Badge>
                  )}
                  {msg.data.recommendation && (
                    <Badge variant="cyan">
                      <Brain className="h-2.5 w-2.5" /> FR3
                    </Badge>
                  )}
                  {msg.data.verification && (
                    <Badge variant="cyan">
                      <Shield className="h-2.5 w-2.5" /> FR2
                    </Badge>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 rounded-sm border border-border bg-card px-4 py-3">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan" />
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                Processing through middleware pipeline...
              </span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border px-6 py-4">
        <form onSubmit={handleSubmit} className="flex items-end gap-3">
          <div className="relative flex-1">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Describe a task to analyze through the middleware pipeline..."
              rows={2}
              className="w-full resize-none rounded-sm border border-border bg-muted/30 px-4 py-3 font-mono text-xs text-foreground placeholder:text-muted-foreground/50 focus:border-cyan/40 focus:outline-none focus:ring-1 focus:ring-cyan/20"
            />
          </div>
          <Button type="submit" size="md" disabled={loading || !input.trim()}>
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Send className="h-3.5 w-3.5" />
            )}
            Send
          </Button>
        </form>
        <p className="mt-2 text-[9px] text-muted-foreground/50">
          Press Enter to send. Shift+Enter for new line. Queries hit FR1, FR3, FR5 endpoints.
        </p>
      </div>
    </div>
  );
}
