// Tiny client for the fitbit-plus FastAPI backend.
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type DataTypeInfo = {
  name: string;
  label: string;
  unit: string;
  scope: string;
  intraday: boolean;
  group: string;
};

export type Point = { day?: string; ts?: string; value: number | null; unit: string };

export type SyncReport = {
  ok?: boolean;
  total_rows?: number;
  results?: { data_type: string; daily_rows: number; intraday_rows: number; error: string | null }[];
  detail?: string; // FastAPI error body (e.g. expired token 401)
};

export type Goal = {
  id: number;
  data_type: string;
  label: string;
  unit: string;
  comparator: "gte" | "lte";
  target: number;
  latest: number | null;
  latest_day: string | null;
  met_now: boolean;
  adherence: number;
  streak: number;
  trend: "improving" | "slipping" | "flat";
  trend_good: boolean;
  status: "met" | "on-track" | "off-track" | "no-data";
  days: number;
  spark: number[];
};

export type GoalsResponse = {
  goals: Goal[];
  summary: { overall: number; on_track: number; total: number; scored: number; narrative: string };
};

export type Recommendation = {
  id: string;
  category: string;
  tone: "push" | "rest" | "improve" | "steady";
  title: string;
  detail: string;
  priority: number;
  metric?: string;
};

export type CoachResponse = { date: string; recommendations: Recommendation[] };

export type Insight = {
  id: string;
  kind: "trend" | "anomaly" | "record" | "streak" | "load" | "sleep_debt" | "correlation";
  sentiment: "good" | "watch" | "bad" | "info";
  title: string;
  detail: string;
  metric?: string;
  priority: number;
  gauge?: { value: number; zones: number[]; max: number };
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  health: () =>
    get<{ status: string; authenticated: boolean; token_days_left: number | null }>(
      "/api/health"
    ),
  dataTypes: () => get<DataTypeInfo[]>("/api/data-types"),
  dailyBulk: () => get<{ series: Record<string, Point[]> }>("/api/data/daily"),
  syncStatus: () =>
    get<{ data_type: string; kind: string; last_day: string; last_sync_at: string }[]>(
      "/api/sync/status"
    ),
  daily: (type: string, start?: string, end?: string) => {
    const q = new URLSearchParams();
    if (start) q.set("start", start);
    if (end) q.set("end", end);
    return get<{ label: string; unit: string; points: Point[] }>(
      `/api/data/${type}/daily?${q}`
    );
  },
  intraday: (type: string, start?: string, end?: string, maxPoints?: number) => {
    const q = new URLSearchParams();
    if (start) q.set("start", start);
    if (end) q.set("end", end);
    if (maxPoints) q.set("max_points", String(maxPoints));
    return get<{ label: string; unit: string; points: Point[] }>(
      `/api/data/${type}/intraday?${q}`
    );
  },
  loginUrl: () => `${BASE}/auth/login`,
  triggerSync: (): Promise<SyncReport> =>
    fetch(`${BASE}/api/sync`, { method: "POST" }).then((r) => r.json()),
  readiness: () =>
    get<{
      date: string;
      score: number;
      tone: string;
      narrative: string;
      components: {
        key: string; label: string; score: number;
        value: number; unit: string; delta: number | null; good: boolean;
      }[];
    }>("/api/readiness"),
  insights: () => get<{ insights: Insight[] }>("/api/insights"),
  coach: () => get<CoachResponse>("/api/coach"),
  goals: () => get<GoalsResponse>("/api/goals"),
  createGoal: (body: { data_type: string; comparator: "gte" | "lte"; target: number }) =>
    fetch(`${BASE}/api/goals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
  patchGoal: (id: number, body: { target?: number; comparator?: "gte" | "lte" }) =>
    fetch(`${BASE}/api/goals/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
  deleteGoal: (id: number) =>
    fetch(`${BASE}/api/goals/${id}`, { method: "DELETE" }).then((r) => r.json()),
};
