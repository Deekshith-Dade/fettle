// Tiny client for the fettle FastAPI backend.
// Default: the same host the dashboard was loaded from, port 8400 — so localhost works
// at the desk and the Mac's Tailscale name/IP works from a phone, with zero config.
// Set NEXT_PUBLIC_API_BASE only to override (e.g. backend on a different machine).
const BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8400`
    : "http://localhost:8400");

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
  kind: "trend" | "anomaly" | "record" | "streak" | "load" | "sleep_debt" | "correlation" | "llm";
  sentiment: "good" | "watch" | "bad" | "info";
  title: string;
  detail: string;
  metric?: string;
  priority: number;
  gauge?: { value: number; zones: number[]; max: number };
};

/** One exercise session — the exercise-* daily metrics aggregate these. */
export type Workout = {
  id: string;
  day: string;
  start_local: string;
  activity: string;
  duration_min: number | null;
  calories: number | null;
  distance_km: number | null;
  steps: number | null;
  avg_hr: number | null;
  azm: number | null;
};

/** Session drill-down: the summary row plus its intraday HR trace and time-in-zone. */
export type WorkoutDetail = Workout & {
  start_ts: string;
  end_ts: string | null;
  hr_trace: { ts: string; value: number }[];
  hr_max_session: number | null;
  hr_samples: number;
  zones_min: { light: number; fat_burn: number; cardio: number; peak: number };
  hr_max_basis: string;
};

/** LLM-synthesized briefing over the computed evidence (daily read or weekly retro). */
export type Briefing = {
  kind?: "daily" | "weekly";
  day: string;
  generated_at: string;
  model: string | null;
  headline: string;
  narrative: string;
  insights: Insight[];
};

/** A durable fact the chat coach saved (injury, schedule, preference, event). */
export type CoachMemory = {
  id: number;
  created_at: string;
  updated_at: string;
  category: string;
  content: string;
  active: number;
};

// --- peer benchmarks ---
export type BenchmarkBand = {
  name: string; lo: number; hi: number; tone: string; start: number; end: number;
};
export type Benchmark = {
  key: string; label: string; unit: string; better: "up" | "down" | "range";
  value: number; tier: string; tone: string; scale: [number, number]; position: number;
  bands: BenchmarkBand[];
  target: { label: string; value: number; comparator: "gte" | "lte" } | null;
  basis: string; caveat: string;
};
export type BenchmarksResponse = { as_of: string | null; cohort: string; benchmarks: Benchmark[] };

// --- sleep deep-dive ---
export type SleepStageTarget = {
  key: string; label: string; pct: number; target_lo: number; target_hi: number;
  tone: string; note: string;
};
export type SleepNight = {
  day: string; duration: number | null; score: number | null; efficiency: number | null;
  deep: number | null; rem: number | null; light: number | null; awake: number | null;
};
export type SleepDetail = {
  as_of: string; need_hours: number;
  need_basis: {
    hours: number; source: "personal" | "population"; median: number | null;
    nights: number; window: number; band: [number, number]; clamped: boolean;
  };
  tonight: {
    hours: number; need: number; debt_payback: number; load_bump: number;
    capped: boolean; tone: string; message: string;
  };
  last_night: {
    day: string; duration: number | null; efficiency: number | null; score: number | null;
    stages: Record<string, number | null>; stage_pct: Record<string, number>;
  };
  averages: Record<string, number | null>;
  stage_targets: SleepStageTarget[];
  debt: { hours: number; nights: number; tone: string; need: number; message: string };
  regularity: { std: number; nights: number; tone: string; message: string };
  trend: { metric: string; direction: string; change: number; tone: string; nights: number };
  nights: SleepNight[];
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// --- first-run setup -------------------------------------------------------------

export type SetupCredentials = {
  present: boolean;
  valid?: boolean;
  client_type?: "web" | "installed";
  client_id_hint?: string;
  redirect_uris?: string[];
  error?: string;
};

export type SetupStatus = {
  credentials: SetupCredentials;
  authenticated: boolean;
  token_days_left: number | null;
  has_data: boolean;
  redirect_uri: string;
  scopes: string[];
};

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
  setupStatus: () => get<SetupStatus>("/api/setup/status"),
  saveCredentials: async (jsonText: string) => {
    const res = await fetch(`${BASE}/api/setup/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ json_text: jsonText }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(data?.detail ?? `credentials -> ${res.status}`);
    return data as { ok: boolean; credentials: SetupCredentials; warnings: string[] };
  },
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
  workouts: (days = 60) => get<{ workouts: Workout[] }>(`/api/workouts?days=${days}`),
  workoutDetail: (id: string) =>
    get<WorkoutDetail>(`/api/workouts/detail?id=${encodeURIComponent(id)}`),
  briefing: () => get<{ briefing: Briefing | null }>("/api/briefing"),
  refreshBriefing: (): Promise<{ briefing: Briefing | null }> =>
    fetch(`${BASE}/api/briefing/refresh`, { method: "POST" }).then((r) => {
      if (!r.ok) throw new Error(`briefing refresh -> ${r.status}`);
      return r.json();
    }),
  weeklyBriefing: () => get<{ briefing: Briefing | null }>("/api/briefing/weekly"),
  refreshWeeklyBriefing: (): Promise<{ briefing: Briefing | null }> =>
    fetch(`${BASE}/api/briefing/weekly/refresh`, { method: "POST" }).then((r) => {
      if (!r.ok) throw new Error(`weekly refresh -> ${r.status}`);
      return r.json();
    }),
  coach: () => get<CoachResponse>("/api/coach"),
  benchmarks: () => get<BenchmarksResponse>("/api/benchmarks"),
  sleepDetail: () => get<SleepDetail>("/api/sleep/detail"),
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

// --- AI coach chat -------------------------------------------------------------

export type ChatModel = { id: string; label: string; recommended: boolean };
export type ChatToolCall = { name: string; label: string; input?: Record<string, unknown> };
export type ChatAttachment = { id: string; name: string };
/** A show_* tool call the coach made — the UI mounts the matching live widget. */
export type ChatWidgetSpec = { kind: string; params?: Record<string, unknown> };
/** Ordered answer content: prose interleaved with inline widgets. */
export type ChatBlock =
  | { type: "text"; text: string }
  | { type: "widget"; widget: ChatWidgetSpec };

export type Conversation = {
  id: number;
  title: string;
  model: string | null;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  parts?: {
    tools?: ChatToolCall[];
    blocks?: ChatBlock[];
    attachments?: { name: string }[];
    model?: string;
    tokens?: number;
  } | null;
  created_at: string;
};

/** Handlers for the SSE events a chat turn streams back. */
export type ChatEvents = {
  meta?: (m: { conversation_id: number; title: string; model: string }) => void;
  tool?: (t: ChatToolCall) => void;
  widget?: (w: ChatWidgetSpec) => void;
  text?: (t: { text: string }) => void;
  done?: (d: { message_id: number; tokens: number; model: string }) => void;
  error?: (e: { message: string }) => void;
};

export const chatApi = {
  models: () => get<ChatModel[]>("/api/chat/models"),
  conversations: () => get<Conversation[]>("/api/chat/conversations"),
  conversation: (id: number) =>
    get<Conversation & { messages: ChatMessage[] }>(`/api/chat/conversations/${id}`),
  rename: (id: number, title: string) =>
    fetch(`${BASE}/api/chat/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then((r) => r.json()),
  remove: (id: number) =>
    fetch(`${BASE}/api/chat/conversations/${id}`, { method: "DELETE" }).then((r) => r.json()),
  upload: async (file: File): Promise<ChatAttachment> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${BASE}/api/chat/attachments`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(`upload failed (${res.status})`);
    return res.json();
  },
  memories: () => get<{ memories: CoachMemory[] }>("/api/coach/memory"),
  forgetMemory: (id: number) =>
    fetch(`${BASE}/api/coach/memory/${id}`, { method: "DELETE" }).then((r) => r.json()),
};

/** POST a message and dispatch the SSE stream to `on.*` until the turn ends. */
export async function streamChat(
  body: {
    message: string;
    conversation_id?: number | null;
    model?: string;
    attachments?: ChatAttachment[];
  },
  on: ChatEvents,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`/api/chat -> ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!event || !data) continue;
      try {
        const payload = JSON.parse(data);
        (on as Record<string, ((p: unknown) => void) | undefined>)[event]?.(payload);
      } catch {
        /* malformed block — skip */
      }
    }
  }
}
