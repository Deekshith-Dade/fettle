"use client";

/**
 * Chat widgets — the live visuals the coach renders inline in its answers.
 *
 * The agent's show_* tools stream only a {kind, params} spec; each widget here fetches
 * its own fresh data from the existing API (with a short-lived cache so re-rendering
 * history doesn't refetch). Charts mirror the dashboard's Recharts idiom — and since
 * Recharts writes SVG presentation attributes that can't resolve CSS var(), colors are
 * picked from the live theme and re-picked when it changes (useThemeAttr).
 */
import { useEffect, useId, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type ChatWidgetSpec, type Goal, type Point } from "../lib/api";
import { BenchmarkRow } from "./insights-views";

/* ————— tiny shared helpers (mirrors the dashboard's) ————— */

const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));

function seriesVals(pts?: Point[]): number[] {
  return (pts ?? []).map((p) => p.value).filter((v): v is number => v != null);
}
function latestVal(pts?: Point[]): number | null {
  const v = seriesVals(pts);
  return v.length ? v[v.length - 1] : null;
}
function baseline(pts?: Point[], n = 28): number | null {
  const v = seriesVals(pts);
  if (v.length < 2) return v[0] ?? null;
  const hist = v.slice(0, -1).slice(-n);
  return hist.length ? hist.reduce((a, b) => a + b, 0) / hist.length : v[0];
}
// Roll a daily series into 7-day means, binned back from the most recent day.
function bucketWeekly(pts: Point[]): { x: string; y: number }[] {
  const withVal = pts.filter((p) => p.value != null && p.day);
  if (!withVal.length) return [];
  const last = new Date(withVal[withVal.length - 1].day + "T12:00:00").getTime();
  const bins = new Map<number, number[]>();
  for (const p of withVal) {
    const daysAgo = Math.floor((last - new Date(p.day + "T12:00:00").getTime()) / 86400000);
    const wk = Math.floor(daysAgo / 7);
    (bins.get(wk) ?? bins.set(wk, []).get(wk)!).push(p.value as number);
  }
  return [...bins.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([wk, vals]) => {
      const start = new Date(last);
      start.setDate(start.getDate() - wk * 7 - 6);
      return { x: start.toLocaleDateString("en-CA"), y: vals.reduce((a, b) => a + b, 0) / vals.length };
    });
}
function formatNum(n: number): string {
  if (Math.abs(n) >= 100) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
function fmtDay(iso?: string): string {
  if (!iso) return "";
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function fmtDayLong(iso: string): string {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
const localToday = () => new Date().toLocaleDateString("en-CA");
const isoDaysAgo = (days: number) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toLocaleDateString("en-CA");
};

const GOOD_WHEN_DOWN = new Set(["daily-resting-heart-rate", "heart-rate", "sleep-awake", "sedentary-period"]);
const CUMULATIVE = new Set([
  "steps", "distance", "floors", "active-zone-minutes", "total-calories",
  "active-energy-burned", "active-minutes", "sedentary-period", "swim-lengths-data",
  "time-in-heart-rate-zone", "calories-in-heart-rate-zone", "cardio-load",
  "exercise-minutes", "exercise-count", "exercise-distance", "exercise-calories",
]);

/* ————— theme-aware chart colors (Recharts can't resolve CSS vars) ————— */

function useThemeAttr(): "light" | "dark" {
  const [t, setT] = useState<"light" | "dark">("dark");
  useEffect(() => {
    const el = document.documentElement;
    const read = () => setT(el.getAttribute("data-theme") === "light" ? "light" : "dark");
    read();
    const mo = new MutationObserver(read);
    mo.observe(el, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return t;
}

function chartInk(light: boolean) {
  return light
    ? { grid: "rgba(18,22,28,0.07)", refFaint: "rgba(18,22,28,0.22)", axis: "#838a94", lime: "#5f8c0f", cyan: "#1f8ea4" }
    : { grid: "rgba(255,255,255,0.05)", refFaint: "rgba(255,255,255,0.22)", axis: "#646b78", lime: "#cdf24e", cyan: "#7fe3ef" };
}
function scoreColor(score: number, light: boolean): string {
  if (score >= 75) return light ? "#5f8c0f" : "#cdf24e";
  if (score >= 55) return light ? "#a9710c" : "#f4c257";
  return light ? "#cf4d63" : "#f47a8f";
}
const tooltipStyle = {
  background: "#131519",
  border: "1px solid rgba(255,255,255,0.14)",
  borderRadius: 10,
  fontSize: 12,
} as const;

/* ————— fetch-once cache (60s) so history re-renders don't refetch ————— */

const cache = new Map<string, { t: number; p: Promise<unknown> }>();

/** Drop a cached fetch so the next widget mount refetches — e.g. after the coach
 * mutates goals, the goals widget must show the post-change state, not the cache. */
export function bustWidgetCache(key: string) {
  cache.delete(key);
}

function useCached<T>(key: string, fn: () => Promise<T>): { data?: T; err?: string } {
  const [state, set] = useState<{ data?: T; err?: string }>({});
  useEffect(() => {
    let on = true;
    const hit = cache.get(key);
    const p =
      hit && Date.now() - hit.t < 60_000
        ? (hit.p as Promise<T>)
        : (() => {
            const np = fn();
            cache.set(key, { t: Date.now(), p: np });
            np.catch(() => cache.delete(key)); // don't cache failures
            return np;
          })();
    p.then((d) => on && set({ data: d })).catch((e) => on && set({ err: String(e) }));
    return () => { on = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- key identifies the request
  }, [key]);
  return state;
}

/* ————— chrome shared by every widget ————— */

function Skel({ h }: { h: number }) {
  return <div className="cw cw-skel" style={{ height: h }} aria-hidden />;
}
function WErr({ msg }: { msg: string }) {
  return <div className="cw cw-err">{msg}</div>;
}
function Head({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="cw-head">
      <span className="cw-title">{title}</span>
      {sub && <span className="cw-sub">{sub}</span>}
    </div>
  );
}

/* ————— chart: one metric over time ————— */

function ChartWidget({ metric, days }: { metric: string; days: number }) {
  const light = useThemeAttr() === "light";
  const gid = useId();
  const { data, err } = useCached(`daily:${metric}:${days}`, () => api.daily(metric, isoDaysAgo(days)));
  if (err) return <WErr msg={`Couldn't load ${metric}.`} />;
  if (!data) return <Skel h={236} />;
  const pts = data.points;
  if (!seriesVals(pts).length) return <WErr msg={`No ${data.label} data in the last ${days} days.`} />;
  const weekly = pts.length > 45;
  const rows = weekly ? bucketWeekly(pts) : pts.map((p) => ({ x: p.day!, y: p.value }));
  const base = baseline(pts);
  const ci = chartInk(light);
  return (
    <div className="cw">
      <Head title={data.label} sub={`${weekly ? "weekly avg" : "daily"} · last ${days}d${data.unit ? ` · ${data.unit}` : ""}`} />
      <ResponsiveContainer width="100%" height={196}>
        <AreaChart data={rows} margin={{ left: -8, right: 8, top: 6 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={ci.lime} stopOpacity={0.35} />
              <stop offset="100%" stopColor={ci.lime} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={ci.grid} vertical={false} />
          <XAxis dataKey="x" stroke={ci.axis} fontSize={11} minTickGap={46}
            tickLine={false} axisLine={false} tickFormatter={(d) => fmtDay(String(d))} />
          <YAxis stroke={ci.axis} fontSize={11} width={42} domain={["auto", "auto"]}
            tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#9aa1ab" }}
            labelFormatter={(d) => fmtDayLong(String(d))}
            formatter={(val) => [`${formatNum(Number(val))}${data.unit ? ` ${data.unit}` : ""}`, data.label]} />
          {base != null && (
            <ReferenceLine y={base} stroke={ci.refFaint} strokeDasharray="5 5"
              label={{ value: "28d avg", position: "insideTopRight", fill: ci.axis, fontSize: 10 }} />
          )}
          <Area type="monotone" dataKey="y" stroke={ci.lime} fill={`url(#${gid})`}
            strokeWidth={2} connectNulls dot={false} activeDot={{ r: 3.5, strokeWidth: 0 }}
            isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ————— intraday: one metric's sub-daily trace ————— */

function fmtClock(ts: string): string {
  return new Date(ts).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function IntradayWidget({ metric, day }: { metric: string; day: string }) {
  const light = useThemeAttr() === "light";
  const gid = useId();
  const { data, err } = useCached(`intraday:${metric}:${day}`, () =>
    api.intraday(metric, day, day, 900));
  if (err) return <WErr msg={`Couldn't load the intraday ${metric} stream.`} />;
  if (!data) return <Skel h={236} />;
  const rows = data.points
    .filter((p) => p.value != null && p.ts)
    .map((p) => ({ x: p.ts!, y: p.value }));
  if (rows.length < 5) return <WErr msg={`No intraday ${data.label} readings on ${fmtDayLong(day)}.`} />;
  const ci = chartInk(light);
  return (
    <div className="cw">
      <Head title={`${data.label} — through the day`}
        sub={`${fmtDayLong(day)}${data.unit ? ` · ${data.unit}` : ""} · ${rows.length} samples`} />
      <ResponsiveContainer width="100%" height={196}>
        <AreaChart data={rows} margin={{ left: -8, right: 8, top: 6 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={ci.cyan} stopOpacity={0.3} />
              <stop offset="100%" stopColor={ci.cyan} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke={ci.grid} vertical={false} />
          <XAxis dataKey="x" stroke={ci.axis} fontSize={11} minTickGap={56}
            tickLine={false} axisLine={false} tickFormatter={(t) => fmtClock(String(t))} />
          <YAxis stroke={ci.axis} fontSize={11} width={42} domain={["auto", "auto"]}
            tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#9aa1ab" }}
            labelFormatter={(t) => fmtClock(String(t))}
            formatter={(val) => [`${formatNum(Number(val))}${data.unit ? ` ${data.unit}` : ""}`, data.label]} />
          <Area type="monotone" dataKey="y" stroke={ci.cyan} fill={`url(#${gid})`}
            strokeWidth={1.75} connectNulls dot={false} activeDot={{ r: 3, strokeWidth: 0 }}
            isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ————— comparison: two metrics, dual axes ————— */

function CompareWidget({ a, b, days }: { a: string; b: string; days: number }) {
  const light = useThemeAttr() === "light";
  const start = isoDaysAgo(days);
  const ra = useCached(`daily:${a}:${days}`, () => api.daily(a, start));
  const rb = useCached(`daily:${b}:${days}`, () => api.daily(b, start));
  if (ra.err || rb.err) return <WErr msg="Couldn't load the comparison series." />;
  if (!ra.data || !rb.data) return <Skel h={248} />;
  const merged = new Map<string, { x: string; a?: number | null; b?: number | null }>();
  for (const p of ra.data.points) if (p.day) merged.set(p.day, { x: p.day, a: p.value });
  for (const p of rb.data.points) {
    if (!p.day) continue;
    const row = merged.get(p.day) ?? { x: p.day };
    row.b = p.value;
    merged.set(p.day, row);
  }
  const rows = [...merged.values()].sort((x, y) => x.x.localeCompare(y.x));
  if (rows.length < 3) return <WErr msg="Not enough overlapping days to compare." />;
  const ci = chartInk(light);
  return (
    <div className="cw">
      <Head title={`${ra.data.label} vs ${rb.data.label}`} sub={`daily · last ${days}d`} />
      <div className="cw-legend">
        <span className="cw-lg"><i style={{ background: ci.lime }} />{ra.data.label}{ra.data.unit ? ` (${ra.data.unit})` : ""}</span>
        <span className="cw-lg"><i style={{ background: ci.cyan }} />{rb.data.label}{rb.data.unit ? ` (${rb.data.unit})` : ""}</span>
      </div>
      <ResponsiveContainer width="100%" height={196}>
        <LineChart data={rows} margin={{ left: -8, right: -8, top: 6 }}>
          <CartesianGrid stroke={ci.grid} vertical={false} />
          <XAxis dataKey="x" stroke={ci.axis} fontSize={11} minTickGap={46}
            tickLine={false} axisLine={false} tickFormatter={(d) => fmtDay(String(d))} />
          <YAxis yAxisId="a" stroke={ci.lime} fontSize={10.5} width={42}
            domain={["auto", "auto"]} tickLine={false} axisLine={false} />
          <YAxis yAxisId="b" orientation="right" stroke={ci.cyan} fontSize={10.5} width={42}
            domain={["auto", "auto"]} tickLine={false} axisLine={false} />
          <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#9aa1ab" }}
            labelFormatter={(d) => fmtDayLong(String(d))}
            formatter={(val, name) => [formatNum(Number(val)), String(name)]} />
          <Line yAxisId="a" type="monotone" dataKey="a" name={ra.data.label} stroke={ci.lime}
            strokeWidth={2} connectNulls dot={false} isAnimationActive={false} />
          <Line yAxisId="b" type="monotone" dataKey="b" name={rb.data.label} stroke={ci.cyan}
            strokeWidth={2} connectNulls dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ————— stat tile ————— */

function StatWidget({ metric }: { metric: string }) {
  const { data, err } = useCached(`daily:${metric}:90`, () => api.daily(metric, isoDaysAgo(90)));
  if (err) return <WErr msg={`Couldn't load ${metric}.`} />;
  if (!data) return <Skel h={92} />;
  const pts = data.points;
  const v = latestVal(pts);
  if (v == null) return <WErr msg={`No recent ${data.label} data.`} />;
  const lastDay = [...pts].reverse().find((p) => p.value != null)?.day;
  const partialToday = CUMULATIVE.has(metric) && lastDay === localToday();
  const base = baseline(pts);
  const delta = base != null && !partialToday ? v - base : null;
  const good = delta == null ? false : GOOD_WHEN_DOWN.has(metric) ? delta < 0 : delta > 0;
  const spark = seriesVals(pts.slice(-21));
  const min = Math.min(...spark), max = Math.max(...spark), range = max - min || 1;
  const line = spark
    .map((y, i) => `${((i / (spark.length - 1)) * 116 + 2).toFixed(1)},${(3 + (1 - (y - min) / range) * 30).toFixed(1)}`)
    .join(" ");
  return (
    <div className="cw cw-stat">
      <div className="cw-stat-main">
        <span className="cw-stat-label">{data.label}</span>
        <span className="cw-stat-val">
          {formatNum(v)}{data.unit && <span className="u">{data.unit}</span>}
        </span>
        {delta != null && Math.abs(delta) > 0.01 ? (
          <span className={`cw-stat-delta ${good ? "up" : "down"}`}>
            {delta > 0 ? "▲" : "▼"} {formatNum(Math.abs(delta))} vs 28d
          </span>
        ) : (
          <span className="cw-stat-delta">{partialToday ? "today still accruing" : `${lastDay ? fmtDay(lastDay) : ""}`}</span>
        )}
      </div>
      {spark.length > 1 && (
        <svg className="cw-stat-spark" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden>
          <polyline points={line} fill="none" stroke="currentColor" strokeWidth={1.75}
            strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        </svg>
      )}
    </div>
  );
}

/* ————— readiness ring + drivers ————— */

function ReadinessWidget() {
  const light = useThemeAttr() === "light";
  const { data, err } = useCached("readiness", () => api.readiness());
  if (err) return <WErr msg="Not enough data for a readiness score yet." />;
  if (!data) return <Skel h={168} />;
  const color = scoreColor(data.score, light);
  const r = 40, c = 2 * Math.PI * r;
  return (
    <div className="cw cw-ready">
      <div className="cw-ring" title={`Readiness ${data.score} — ${data.tone}`}>
        <svg width="104" height="104" viewBox="0 0 104 104">
          <circle cx="52" cy="52" r={r} fill="none" stroke="rgba(130,130,130,0.16)" strokeWidth="8" />
          <circle cx="52" cy="52" r={r} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
            strokeDasharray={c} strokeDashoffset={c * (1 - clamp(data.score, 0, 100) / 100)}
            transform="rotate(-90 52 52)" style={{ filter: `drop-shadow(0 0 7px ${color}59)` }} />
        </svg>
        <span className="cw-ring-score" style={{ color }}>{data.score}</span>
      </div>
      <div className="cw-ready-body">
        <p className="cw-ready-tone">You're <em style={{ color }}>{data.tone}</em> · {fmtDayLong(data.date)}</p>
        <div className="cw-drivers">
          {data.components.map((comp) => (
            <div className="cw-driver" key={comp.key} title={`${comp.label} · ${comp.score}/100`}>
              <span className="cw-d-label">{comp.label}</span>
              <span className="cw-d-track">
                <span className="cw-d-fill" style={{ width: `${comp.score}%`, background: scoreColor(comp.score, light) }} />
              </span>
              <span className="cw-d-val">{formatNum(comp.value)}<span className="u"> {comp.unit}</span></span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ————— sleep stage bars ————— */

const STAGES = [
  { key: "deep", label: "Deep", color: "#b3a4f5" },
  { key: "light", label: "Light", color: "#cdf24e" },
  { key: "rem", label: "REM", color: "#7fe3ef" },
  { key: "awake", label: "Awake", color: "#5a616d" },
] as const;

function SleepWidget({ nights }: { nights: number }) {
  const { data, err } = useCached("sleep", () => api.sleepDetail());
  if (err) return <WErr msg="Not enough sleep data yet." />;
  if (!data) return <Skel h={190} />;
  const rows = data.nights.slice(-nights).filter((n) => (n.duration ?? 0) > 0);
  if (rows.length < 3) return <WErr msg="Not enough recent nights to plot." />;
  const max = Math.max(...rows.map((n) => n.duration ?? 0)) || 1;
  return (
    <div className="cw">
      <Head title="Sleep stages" sub={`last ${rows.length} nights · hours`} />
      <div className="cw-lgrow">
        {STAGES.map((s) => (
          <span key={s.key} className="cw-lg"><i style={{ background: s.color }} />{s.label}</span>
        ))}
      </div>
      <div className="cw-stg-plot">
        {rows.map((n) => {
          const total = (n.deep ?? 0) + (n.light ?? 0) + (n.rem ?? 0) + (n.awake ?? 0) || (n.duration ?? 0);
          return (
            <div key={n.day} className="cw-stg-col"
              title={`${fmtDayLong(n.day)} · ${(n.duration ?? 0).toFixed(1)}h${n.score != null ? ` · score ${n.score}` : ""}`}>
              <div className="cw-stg-stack" style={{ height: `${((n.duration ?? 0) / max) * 100}%` }}>
                {STAGES.map((s) => {
                  const val = n[s.key as keyof typeof n] as number | null;
                  return val && total ? (
                    <span key={s.key} className="cw-stg-seg" style={{ flexGrow: val, background: s.color }} />
                  ) : null;
                })}
              </div>
              <span className="cw-stg-x">{new Date(n.day + "T12:00:00").getDate()}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ————— benchmark standing (reuses the dashboard's BenchmarkRow wholesale) ————— */

function BenchmarkWidget({ metric }: { metric: string }) {
  const { data, err } = useCached("benchmarks", () => api.benchmarks());
  if (err) return <WErr msg="Couldn't load benchmarks." />;
  if (!data) return <Skel h={200} />;
  const bm = data.benchmarks.find((b) => b.key === metric);
  if (!bm) return <WErr msg={`No benchmark data for ${metric} yet.`} />;
  return (
    <div className="cw cw-bench">
      <BenchmarkRow b={bm} />
    </div>
  );
}

/* ————— goals overview ————— */

function goalTone(status: Goal["status"], light: boolean): string {
  if (status === "met") return light ? "#5f8c0f" : "#cdf24e";
  if (status === "on-track") return light ? "#1f8ea4" : "#7fe3ef";
  if (status === "off-track") return light ? "#a9710c" : "#f4c257";
  return light ? "#838a94" : "#646b78";
}

function GoalsWidget() {
  const light = useThemeAttr() === "light";
  const { data, err } = useCached("goals", () => api.goals());
  if (err) return <WErr msg="Couldn't load goals." />;
  if (!data) return <Skel h={150} />;
  if (!data.goals.length) return <WErr msg="No active goals yet — set one on the dashboard." />;
  return (
    <div className="cw">
      <Head title="Goals" sub={data.summary.narrative || `${data.summary.on_track} of ${data.summary.scored} on track`} />
      <div className="cw-goals">
        {data.goals.map((g) => {
          const tone = goalTone(g.status, light);
          return (
            <div className="cw-goal" key={g.id}>
              <span className="cw-goal-label">{g.label}</span>
              <span className="cw-goal-target">
                {g.comparator === "gte" ? "≥" : "≤"} {formatNum(g.target)}{g.unit ? ` ${g.unit}` : ""}
              </span>
              <span className="cw-d-track">
                <span className="cw-d-fill" style={{ width: `${Math.max(3, g.adherence)}%`, background: tone }} />
              </span>
              <span className="cw-goal-adh" style={{ color: tone }}>
                {g.adherence}%{g.streak > 0 ? ` · ${g.streak}d streak` : ""}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ————— dispatcher ————— */

function num(v: unknown, lo: number, hi: number, dflt: number): number {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? clamp(Math.round(n), lo, hi) : dflt;
}
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function ChatWidget({ spec }: { spec: ChatWidgetSpec }) {
  const p = spec.params ?? {};
  switch (spec.kind) {
    case "chart": {
      const metric = str(p.metric);
      return metric ? <ChartWidget metric={metric} days={num(p.days, 7, 365, 30)} /> : null;
    }
    case "comparison": {
      const a = str(p.metric_a), b = str(p.metric_b);
      return a && b ? <CompareWidget a={a} b={b} days={num(p.days, 7, 365, 30)} /> : null;
    }
    case "stat": {
      const metric = str(p.metric);
      return metric ? <StatWidget metric={metric} /> : null;
    }
    case "intraday": {
      const metric = str(p.metric);
      const rawDay = str(p.day);
      const day = /^\d{4}-\d{2}-\d{2}$/.test(rawDay) ? rawDay : localToday();
      return metric ? <IntradayWidget metric={metric} day={day} /> : null;
    }
    case "readiness":
      return <ReadinessWidget />;
    case "sleep":
      return <SleepWidget nights={num(p.nights, 5, 28, 14)} />;
    case "benchmark": {
      const metric = str(p.metric);
      return metric ? <BenchmarkWidget metric={metric} /> : null;
    }
    case "goals":
      return <GoalsWidget />;
    default:
      return <WErr msg={`This reply used a widget this build doesn't know ("${spec.kind}").`} />;
  }
}
