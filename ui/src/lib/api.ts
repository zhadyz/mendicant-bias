const BASE = "/api/mendicant";

export async function fetchStatus() {
  const res = await fetch(`${BASE}/status`);
  return res.json();
}

export async function fetchAgents() {
  const res = await fetch(`${BASE}/agents`);
  return res.json();
}

export async function fetchAgent(name: string) {
  const res = await fetch(`${BASE}/agents/${name}`);
  return res.json();
}

export async function fetchMiddleware() {
  const res = await fetch(`${BASE}/middleware`);
  return res.json();
}

export async function fetchPatternStats() {
  const res = await fetch(`${BASE}/patterns/stats`);
  return res.json();
}

export async function classifyTask(taskText: string) {
  const res = await fetch(`${BASE}/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_text: taskText }),
  });
  return res.json();
}

export async function routeTools(query: string) {
  const res = await fetch(`${BASE}/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  return res.json();
}

export async function verifyOutput(task: string, output: string) {
  const res = await fetch(`${BASE}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, output }),
  });
  return res.json();
}

export async function recommendStrategy(taskText: string) {
  const res = await fetch(`${BASE}/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_text: taskText }),
  });
  return res.json();
}

export async function checkHealth() {
  const res = await fetch("/health");
  return res.json();
}
