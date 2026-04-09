"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export interface BrainEvent {
  type: string;
  timestamp: string;
  session_id: string | null;
  payload: Record<string, unknown>;
}

interface BrainSocketState {
  events: BrainEvent[];
  connected: boolean;
  latestByType: Record<string, BrainEvent>;
}

function getWsUrl(): string {
  if (typeof window === "undefined") return "ws://localhost:8001/ws/brain";
  // In production, UI is served from the gateway — use same origin.
  // In dev, UI is on :3000, gateway on :8001.
  const host =
    window.location.port === "3000" ? "localhost:8001" : window.location.host;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${host}/ws/brain`;
}
const WS_URL = getWsUrl();
const MAX_EVENTS = 200;
const HEARTBEAT_TIMEOUT_MS = 10_000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15_000;

export function useBrainSocket(): BrainSocketState {
  const [events, setEvents] = useState<BrainEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [latestByType, setLatestByType] = useState<Record<string, BrainEvent>>(
    {},
  );

  const wsRef = useRef<WebSocket | null>(null);
  const lastHeartbeatRef = useRef<number>(0);
  const reconnectDelayRef = useRef(RECONNECT_BASE_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const handleMessage = useCallback((data: string) => {
    try {
      const event: BrainEvent = JSON.parse(data);

      if (event.type === "heartbeat") {
        lastHeartbeatRef.current = Date.now();
        setConnected(true);
        return;
      }

      setEvents((prev) => {
        const next = [...prev, event];
        return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
      });

      setLatestByType((prev) => ({ ...prev, [event.type]: event }));
    } catch {
      // Ignore malformed messages
    }
  }, []);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(WS_URL);

      ws.onopen = () => {
        if (!mountedRef.current) {
          ws.close();
          return;
        }
        wsRef.current = ws;
        lastHeartbeatRef.current = Date.now();
        reconnectDelayRef.current = RECONNECT_BASE_MS;
        setConnected(true);
      };

      ws.onmessage = (e) => {
        handleMessage(e.data);
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (!mountedRef.current) return;
        setConnected(false);
        scheduleReconnect();
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      scheduleReconnect();
    }
  }, [handleMessage]);

  const scheduleReconnect = useCallback(() => {
    if (!mountedRef.current) return;
    if (reconnectTimerRef.current) return;

    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      reconnectDelayRef.current = Math.min(
        reconnectDelayRef.current * 2,
        RECONNECT_MAX_MS,
      );
      connect();
    }, reconnectDelayRef.current);
  }, [connect]);

  // Heartbeat health check
  useEffect(() => {
    const interval = setInterval(() => {
      if (
        lastHeartbeatRef.current > 0 &&
        Date.now() - lastHeartbeatRef.current > HEARTBEAT_TIMEOUT_MS
      ) {
        setConnected(false);
      }
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Connect on mount
  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { events, connected, latestByType };
}
