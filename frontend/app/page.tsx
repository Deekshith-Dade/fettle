"use client";

import { CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, BenchmarksResponse, Briefing, CoachResponse, DataTypeInfo, Goal, GoalsResponse, Insight, Point, Recommendation, SleepDetail } from "@/lib/api";
import { BenchmarksView, SleepView, SleepTeaser, StandingTeaser } from "@/components/insights-views";
import { CommandPalette } from "@/components/command-palette";

/* ————— configuration ————— */

const GROUP_ORDER = ["Recovery", "Activity", "Workouts", "Heart", "Vitals", "Body", "Sleep", "Other"];
const GROUP_BLURB: Record<string, string> = {
  Recovery: "The composite verdict — how much you have in the tank.",
  Activity: "Movement, energy, and time on your feet.",
  Workouts: "Logged sessions and what they cost.",
  Heart: "Rhythm, variability, and recovery signals.",
  Vitals: "Breath, blood, and the quiet numbers.",
  Body: "Composition and measures.",
  Sleep: "Nights — staged, scored, and read closely.",
  Other: "Everything else the API surfaces.",
};

// Curated set surfaced at the top (shown if they have data), in this order.
const HIGHLIGHTS = [
  "sleep-score", "sleep-duration", "daily-resting-heart-rate",
  "daily-heart-rate-variability", "steps", "cardio-load",
  "daily-oxygen-saturation", "daily-respiratory-rate", "exercise-minutes",
];
// Metrics where a downward trend is the good direction.
const GOOD_WHEN_DOWN = new Set(["daily-resting-heart-rate", "heart-rate", "sleep-awake", "sedentary-period"]);
// Day-cumulative metrics: today's value is still accruing, so comparing it against
// full-day baselines would show a bogus ▼ every morning — suppress the delta instead.
const CUMULATIVE = new Set([
  "steps", "distance", "floors", "active-zone-minutes", "total-calories",
  "active-energy-burned", "active-minutes", "sedentary-period", "swim-lengths-data",
  "time-in-heart-rate-zone", "calories-in-heart-rate-zone", "cardio-load",
  "exercise-minutes", "exercise-count", "exercise-distance", "exercise-calories",
]);
// Metrics with no "good" pole — their extreme is a neutral peak, not an achievement.
const NEUTRAL_METRIC = new Set([
  "weight", "cardio-load", "daily-sleep-temperature-derivations", "height", "body-fat",
  "altitude", "core-body-temperature", "blood-glucose",
  "time-in-heart-rate-zone", "calories-in-heart-rate-zone",
]);

// Local calendar date as YYYY-MM-DD (toISOString would flip to UTC's date in the evening).
const localToday = () => new Date().toLocaleDateString("en-CA");

// Readiness sub-score → the metric whose chart explains it, so the hero breakdown is
// a set of shortcuts into the data behind the verdict.
const DRIVER_METRIC: Record<string, string> = {
  hrv: "daily-heart-rate-variability",
  rhr: "daily-resting-heart-rate",
  sleep: "sleep-duration",
  load: "cardio-load",
  temp: "daily-sleep-temperature-derivations",
};

/* ————— glyphs (no emoji: a drawn, single-stroke instrument set) ————— */

const GLYPH_PATHS: Record<string, React.ReactNode> = {
  Recovery: (
    <>
      <path d="M4.5 15.5a7.5 7.5 0 1 1 15 0" />
      <path d="M12 15.5l3.4-4.4" />
      <circle cx="12" cy="15.5" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  Activity: (
    <path d="M12 3.5c1.3 2.8-.4 4.3-1.5 5.8-1 1.4-1.7 2.7-1.7 4.2a4.7 4.7 0 0 0 9.4 0c0-2-.9-3.6-1.9-5.1-.4 1.1-1 1.8-1.9 2.3.2-2.6-.8-5.4-2.4-7.2z" />
  ),
  Workouts: <path d="M7.5 8v8M4.5 9.5v5M16.5 8v8M19.5 9.5v5M7.5 12h9" />,
  Heart: (
    <path d="M12 19.5S4.8 15 3.4 10.9C2.3 7.7 4.6 5 7.4 5c1.9 0 3.5 1 4.6 2.6C13.1 6 14.7 5 16.6 5c2.8 0 5.1 2.7 4 5.9C19.2 15 12 19.5 12 19.5z" />
  ),
  Vitals: <path d="M3 12.5h3.5l2-5.5 3.5 10 2.5-6.5 1 2h5.5" />,
  Body: (
    <>
      <circle cx="12" cy="6.5" r="2.8" />
      <path d="M5.5 20.5c.4-4.2 3-6.5 6.5-6.5s6.1 2.3 6.5 6.5" />
    </>
  ),
  Sleep: <path d="M19.5 14.5A8 8 0 0 1 9.5 4.3 8 8 0 1 0 19.5 14.5z" />,
  Other: <path d="M12 4.5v15M5.5 8.25l13 7.5M18.5 8.25l-13 7.5" />,
  Goals: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3.6" />
      <circle cx="12" cy="12" r="0.7" fill="currentColor" stroke="none" />
    </>
  ),
  Focus: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <path d="M15.5 8.5l-2.2 4.8-4.8 2.2 2.2-4.8z" fill="currentColor" stroke="none" />
    </>
  ),
  Arrow: <path d="M5 12h13M13 7l5 5-5 5" />,
  Edit: <path d="M4 20l1.1-4L15.5 5.6a1.9 1.9 0 0 1 2.7 0l.2.2a1.9 1.9 0 0 1 0 2.7L8 19.1 4 20zM14 7.5l2.5 2.5" />,
};

function Glyph({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {GLYPH_PATHS[name] ?? GLYPH_PATHS.Other}
    </svg>
  );
}

// Insight-kind glyphs — a second, smaller instrument set for the observations feed.
const INSIGHT_PATHS: Record<Insight["kind"], React.ReactNode> = {
  trend: <path d="M4 15.5l4.5-4.5 3 3L20 6.5M20 6.5h-4.5M20 6.5V11" />,
  anomaly: <><path d="M12 3.5l9 15.5H3z" /><path d="M12 10v4.5M12 17.2v.2" /></>,
  record: <><path d="M8 4.5h8v3a4 4 0 0 1-8 0z" /><path d="M8 5.5H5.5V7A2.5 2.5 0 0 0 8 9.5M16 5.5h2.5V7A2.5 2.5 0 0 1 16 9.5M9.5 12.5h5M10 19.5h4M12 12.5v7" /></>,
  streak: <path d="M12 3.5c1.3 2.8-.4 4.3-1.5 5.8-1 1.4-1.7 2.7-1.7 4.2a4.7 4.7 0 0 0 9.4 0c0-2-.9-3.6-1.9-5.1-.4 1.1-1 1.8-1.9 2.3.2-2.6-.8-5.4-2.4-7.2z" />,
  load: <><path d="M4.5 15.5a7.5 7.5 0 1 1 15 0" /><path d="M12 15.5l4-3" /></>,
  sleep_debt: <path d="M19.5 14.5A8 8 0 0 1 9.5 4.3 8 8 0 1 0 19.5 14.5z" />,
  correlation: <><circle cx="7" cy="17" r="1.4" fill="currentColor" stroke="none" /><circle cx="12" cy="12.5" r="1.4" fill="currentColor" stroke="none" /><circle cx="17" cy="7" r="1.4" fill="currentColor" stroke="none" /><path d="M5 19.5L19 5" strokeDasharray="2 2.5" /></>,
  // LLM-synthesized briefing card — a four-point star, the coach's mark.
  llm: <path d="M12 3.5l2 6.5 6.5 2-6.5 2-2 6.5-2-6.5L3.5 12l6.5-2z" />,
};

function InsightCard({ ins, onOpen, delay }: { ins: Insight; onOpen: (m: string) => void; delay: number }) {
  const clickable = !!ins.metric;
  return (
    <button
      className={`insight s-${ins.sentiment} rise`}
      style={{ animationDelay: `${delay}ms` }}
      onClick={() => ins.metric && onOpen(ins.metric)}
      disabled={!clickable}
    >
      <span className="ins-glyph">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}
          strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          {INSIGHT_PATHS[ins.kind]}
        </svg>
      </span>
      <div className="ins-body">
        <div className="ins-title">{ins.title}</div>
        <div className="ins-detail">{ins.detail}</div>
        {ins.gauge && <AcwrGauge g={ins.gauge} />}
      </div>
      {clickable && <span className="ins-chev">›</span>}
    </button>
  );
}

// Compact ACWR gauge: a track with the balanced band lit and a marker at the ratio.
function AcwrGauge({ g }: { g: { value: number; zones: number[]; max: number } }) {
  const pct = (x: number) => `${clamp((x / g.max) * 100, 0, 100)}%`;
  const [lo, hi] = g.zones;
  return (
    <div className="acwr" title={`ACWR ${g.value} · balanced ${lo}–${hi}`}>
      <div className="acwr-track">
        <span className="acwr-band" style={{ left: pct(lo), right: `calc(100% - ${pct(hi)})` }} />
        <span className="acwr-mark" style={{ left: pct(g.value) }} />
      </div>
      <div className="acwr-scale"><span>0</span><span>balanced</span><span>{g.max}</span></div>
    </div>
  );
}

/* ————— formatting & series helpers ————— */

function clamp(x: number, a: number, b: number) { return Math.max(a, Math.min(b, x)); }

function seriesVals(pts?: Point[]): number[] {
  return (pts ?? []).map((p) => p.value).filter((v): v is number => v != null);
}
function latestVal(pts?: Point[]): number | null {
  const v = seriesVals(pts);
  return v.length ? v[v.length - 1] : null;
}
function lastDay(pts?: Point[]): string | null {
  const withVal = (pts ?? []).filter((p) => p.value != null);
  return withVal.length ? withVal[withVal.length - 1].day ?? null : null;
}
function baseline(pts?: Point[], n = 28): number | null {
  const v = seriesVals(pts);
  if (v.length < 2) return v[0] ?? null;
  const hist = v.slice(0, -1).slice(-n);
  return hist.length ? hist.reduce((a, b) => a + b, 0) / hist.length : v[0];
}
function avgLast(pts: Point[] | undefined, n: number): number | null {
  const v = seriesVals(pts).slice(-n);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}
// Roll a daily series into 7-day means, binned back from the most recent day so the
// latest week is always whole. Returns oldest→newest {x: week-start ISO, y: mean}.
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

function scoreColor(score: number): string {
  // These tones are used as text and fills on light panels. The neon dark-mode palette
  // washes out on the "paper" light theme, so use deepened variants there. Reading the DOM
  // is safe — every score-colored element renders only after the client data load (post-
  // hydration), never during SSR, and re-renders when the theme toggles.
  const light = typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light";
  if (score >= 75) return light ? "#5f8c0f" : "#cdf24e";
  if (score >= 55) return light ? "#a9710c" : "#f4c257";
  return light ? "#cf4d63" : "#f47a8f";
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
function fmtClock(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function relTime(iso: string): string {
  const t = new Date(/Z|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + "Z").getTime();
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

// Freshness of a daily series: how recent is its last real value?
function freshness(pts?: Point[]): { label: string; stale: boolean } | null {
  const d = lastDay(pts);
  if (!d) return null;
  const days = Math.round((new Date(localToday()).getTime() - new Date(d).getTime()) / 86400000);
  if (days <= 0) return { label: "today", stale: false };
  if (days === 1) return { label: "yesterday", stale: false };
  return { label: fmtDay(d), stale: days > 3 };
}

/* ————— visual primitives ————— */

function useCountUp(target: number, ms = 900): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setV(target);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      setV(Math.round(target * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

function Ring({ score }: { score: number }) {
  const shown = useCountUp(score);
  const r = 104;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - clamp(score, 0, 100) / 100);
  const color = scoreColor(score);
  return (
    <div className="ring">
      <svg width="240" height="240" viewBox="0 0 240 240">
        {/* tick ring — the instrument bezel */}
        <circle cx="120" cy="120" r="115" fill="none" stroke="rgba(130,130,130,0.22)"
          strokeWidth="4" strokeDasharray="1.5 10.55" />
        <circle cx="120" cy="120" r={r} fill="none" stroke="rgba(130,130,130,0.15)" strokeWidth="10" />
        <circle
          cx="120" cy="120" r={r} fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 1.1s cubic-bezier(0.2,0.8,0.2,1)", filter: `drop-shadow(0 0 9px ${color}59)` }}
        />
      </svg>
      <div className="ring-center">
        <span className="ring-score" style={{ color }}>{shown}</span>
        <span className="ring-label">Readiness</span>
      </div>
    </div>
  );
}

// Interactive sparkline: hover reads out day · value without leaving the tile.
function Spark({ points, unit }: { points: Point[]; unit: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const vals = points.map((p) => p.value);
  const nums = vals.filter((v): v is number => v != null);
  if (nums.length < 2) return <div className="spark-empty" />;
  const min = Math.min(...nums), max = Math.max(...nums), range = max - min || 1;
  const w = 120, h = 40, padX = 2, padY = 5;
  const step = (w - padX * 2) / (points.length - 1);
  const xPct = (i: number) => ((padX + i * step) / w) * 100;
  const yPct = (v: number) => ((padY + (1 - (v - min) / range) * (h - padY * 2)) / h) * 100;
  const line = points
    .map((p, i) => (p.value == null ? null : `${(padX + i * step).toFixed(1)},${((yPct(p.value) / 100) * h).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");
  const hv = hover != null ? vals[hover] : null;
  return (
    <>
      <svg
        className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
        onPointerMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHover(clamp(Math.round(((e.clientX - r.left) / r.width) * (points.length - 1)), 0, points.length - 1));
        }}
        onPointerLeave={() => setHover(null)}
      >
        <polyline className="line" pathLength={1} points={line} fill="none" stroke="currentColor"
          strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </svg>
      {hover != null && hv != null && (
        <>
          <span className="spark-dot" style={{ left: `${xPct(hover)}%`, top: `${yPct(hv)}%` }} />
          <span className="spark-tip">{fmtDay(points[hover].day)} · {formatNum(hv)}{unit ? ` ${unit}` : ""}</span>
        </>
      )}
    </>
  );
}

/* ————— sleep stage composition ————— */

const STAGES = [
  { key: "sleep-deep", label: "Deep", color: "#b3a4f5" },
  { key: "sleep-light", label: "Light", color: "#cdf24e" },
  { key: "sleep-rem", label: "REM", color: "#7fe3ef" },
  { key: "sleep-awake", label: "Awake", color: "#5a616d" },
] as const;

function SleepStages({ cache, nights = 14 }: { cache: Record<string, Point[]>; nights?: number }) {
  const [hover, setHover] = useState<string | null>(null);

  const byNight = useMemo(() => {
    const days = new Map<string, Record<string, number>>();
    for (const s of STAGES) {
      for (const p of cache[s.key] ?? []) {
        if (p.day == null || p.value == null) continue;
        (days.get(p.day) ?? days.set(p.day, {}).get(p.day)!)[s.key] = p.value;
      }
    }
    return [...days.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-nights)
      .map(([day, v]) => ({ day, v, total: STAGES.reduce((t, s) => t + (v[s.key] ?? 0), 0) }));
  }, [cache, nights]);

  if (byNight.length < 3) return null;
  const max = Math.max(...byNight.map((n) => n.total)) || 1;
  const hovered = hover ? byNight.find((n) => n.day === hover) : null;

  return (
    <div className="stages">
      <div className="stages-head">
        <div className="stages-legend">
          {STAGES.map((s) => (
            <span key={s.key} className="lg"><i style={{ background: s.color }} />{s.label}</span>
          ))}
        </div>
        <div className="stages-read">
          {hovered ? (
            <>
              <strong>{fmtDayLong(hovered.day)}</strong> · {hovered.total.toFixed(1)}h total ·{" "}
              {STAGES.map((s) => `${s.label} ${(hovered.v[s.key] ?? 0).toFixed(1)}`).join(" · ")}
            </>
          ) : (
            <span className="muted">Hover a night for its stage breakdown</span>
          )}
        </div>
      </div>
      <div className="stages-plot">
        {byNight.map((n) => (
          <div
            key={n.day}
            className={`stg-col${hover === n.day ? " on" : ""}`}
            onPointerEnter={() => setHover(n.day)}
            onPointerLeave={() => setHover(null)}
            title={`${fmtDayLong(n.day)} · ${n.total.toFixed(1)}h`}
          >
            <div className="stg-stack" style={{ height: `${(n.total / max) * 100}%` }}>
              {STAGES.map((s) =>
                n.v[s.key] ? (
                  <span key={s.key} className="stg-seg"
                    style={{ flexGrow: n.v[s.key], background: s.color }} />
                ) : null
              )}
            </div>
            <span className="stg-x">{new Date(n.day + "T12:00:00").getDate()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ————— today's focus (coaching) ————— */

const TONE_COLOR: Record<Recommendation["tone"], string> = {
  push: "#cdf24e", rest: "#f47a8f", improve: "#f4c257", steady: "#7fe3ef",
};

function FocusModule({ recs, onOpen, canOpen }: { recs: Recommendation[]; onOpen: (m: string) => void; canOpen: (m: string) => boolean }) {
  if (!recs.length) return null;
  return (
    <section className="section rise" style={{ animationDelay: "60ms" }}>
      <div className="sec-head">
        <span className="sec-glyph"><Glyph name="Focus" /></span>
        <h2 className="sec-title">Today's focus</h2>
      </div>
      <p className="sec-blurb">What to do with today — read from your readiness, sleep, training load, and goals.</p>
      <hr className="sec-rule" />
      <div className="focus-grid">
        {recs.map((r, i) => {
          const clickable = !!(r.metric && canOpen(r.metric));
          return (
            <article key={r.id} className={`rec t-${r.tone}${clickable ? " is-link" : ""}`}
              style={{ animationDelay: `${90 + i * 70}ms`, "--rc": TONE_COLOR[r.tone] } as CSSProperties}
              {...(clickable ? {
                role: "button", tabIndex: 0,
                onClick: () => onOpen(r.metric!),
                onKeyDown: (e: React.KeyboardEvent) => {
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(r.metric!); }
                },
              } : {})}>
              <div className="rec-cat"><span className="rec-dot" />{r.category}</div>
              <h3 className="rec-title">{r.title}</h3>
              <p className="rec-detail">{r.detail}</p>
              {clickable && <span className="rec-open">See the data<Glyph name="Arrow" /></span>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

/* ————— readiness strip (28-day) with hover tooltip ————— */

function ReadinessStrip({ data }: { data: { day: string; value: number | null }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const n = data.length;
  const h = hover != null ? data[hover] : null;
  // clamp the tooltip's horizontal centre so edge cells don't clip against the hero
  const pct = hover != null ? Math.min(94, Math.max(6, ((hover + 0.5) / n) * 100)) : 50;
  return (
    <>
      <div className="strip" aria-label="Readiness, last 28 days" onMouseLeave={() => setHover(null)}>
        {data.map((s, i) => (
          <span
            key={s.day}
            className="strip-cell"
            data-v={s.value ?? undefined}
            onMouseEnter={() => setHover(i)}
            style={s.value != null ? { background: scoreColor(s.value), opacity: 0.32 + 0.6 * (s.value / 100) } : undefined}
          />
        ))}
        {h && (
          <div className="strip-tip" style={{ left: `${pct}%` }} role="tooltip">
            <span className="strip-tip-d">{fmtDayLong(h.day)}</span>
            {h.value != null ? (
              <span className="strip-tip-v" style={{ color: scoreColor(h.value) }}>
                {Math.round(h.value)}<i>readiness</i>
              </span>
            ) : (
              <span className="strip-tip-v none">No reading</span>
            )}
          </div>
        )}
      </div>
      <div className="strip-legend">
        <span>{fmtDay(data[0].day)}</span><span>28-day readiness</span><span>{fmtDay(data.at(-1)!.day)}</span>
      </div>
    </>
  );
}

/* ————— goals ————— */

const GOAL_STATUS: Record<Goal["status"], { color: string; word: string }> = {
  "met": { color: "#cdf24e", word: "On target" },
  "on-track": { color: "#f4c257", word: "On track" },
  "off-track": { color: "#f47a8f", word: "Behind" },
  "no-data": { color: "#646b78", word: "No data" },
};

// The hues above are the dark-theme neons; on paper they wash out (the lime "met" worst of
// all — near-invisible as badge text on near-white). Resolve a deepened equivalent from the
// live theme, matching the light-token palette. Mirrors scoreColor().
function goalColor(status: Goal["status"]): string {
  const light = typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light";
  if (!light) return GOAL_STATUS[status].color;
  return status === "met" ? "#5f8c0f"
    : status === "on-track" ? "#a9710c"
    : status === "off-track" ? "#cf4d63"
    : "#838a94";
}

function GoalRing({ pct, color }: { pct: number; color: string }) {
  const shown = useCountUp(pct, 800);
  const r = 42, c = 2 * Math.PI * r;
  const off = c * (1 - clamp(pct, 0, 100) / 100);
  return (
    <div className="goal-ring">
      <svg width="108" height="108" viewBox="0 0 108 108">
        <circle cx="54" cy="54" r={r} fill="none" stroke="rgba(130,130,130,0.18)" strokeWidth="8" />
        <circle cx="54" cy="54" r={r} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={off} transform="rotate(-90 54 54)"
          style={{ transition: "stroke-dashoffset 0.9s var(--ease)", filter: `drop-shadow(0 0 6px ${color}55)` }} />
      </svg>
      <div className="goal-ring-c"><span style={{ color }}>{shown}<i>%</i></span></div>
    </div>
  );
}

function GoalCard({ g, onOpen, onEdit, onRemove }: {
  g: Goal; onOpen: (m: string) => void;
  onEdit: (id: number, patch: { target?: number; comparator?: "gte" | "lte" }) => Promise<void>;
  onRemove: (id: number) => void;
}) {
  const meta = GOAL_STATUS[g.status];
  const cmpWord = g.comparator === "gte" ? "at least" : "at most";
  const metDays = Math.round((g.adherence / 100) * g.days);
  const trendArrow = g.trend === "improving" ? "▲" : g.trend === "slipping" ? "▼" : "→";

  const [editing, setEditing] = useState(false);
  const [cmp, setCmp] = useState<"gte" | "lte">(g.comparator);
  const [tVal, setTVal] = useState(String(g.target));
  const [busy, setBusy] = useState(false);

  function beginEdit() { setCmp(g.comparator); setTVal(String(g.target)); setEditing(true); }
  async function save() {
    const t = parseFloat(tVal);
    if (Number.isNaN(t)) return;
    setBusy(true);
    try {
      const patch: { target?: number; comparator?: "gte" | "lte" } = {};
      if (t !== g.target) patch.target = t;
      if (cmp !== g.comparator) patch.comparator = cmp;
      if (patch.target != null || patch.comparator) await onEdit(g.id, patch);
      setEditing(false);
    } finally { setBusy(false); }
  }

  if (editing) {
    return (
      <div className={`goal s-${g.status} is-editing`} style={{ "--gc": goalColor(g.status) } as CSSProperties}>
        <div className="goal-edit">
          <div className="goal-head">
            <span className="goal-label">{g.label}</span>
            <span className="gf-k">Edit target</span>
          </div>
          <div className="gf-cmp">
            <button type="button" className={cmp === "gte" ? "on" : ""} onClick={() => setCmp("gte")}>at least</button>
            <button type="button" className={cmp === "lte" ? "on" : ""} onClick={() => setCmp("lte")}>at most</button>
          </div>
          <div className="gf-inputwrap">
            <input type="number" value={tVal} autoFocus className="gf-target"
              onChange={(e) => setTVal(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }} />
            <span className="gf-unit">{g.unit}</span>
          </div>
          <div className="gf-actions">
            <button className="btn btn-lime" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
            <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`goal s-${g.status}`} style={{ "--gc": goalColor(g.status) } as CSSProperties}>
      <button className="goal-body" onClick={() => onOpen(g.data_type)}>
        <div className="goal-head">
          <span className="goal-label">{g.label}</span>
          <span className="goal-badge">{meta.word}</span>
        </div>
        <div className="goal-nums">
          <span className="goal-latest">{g.latest != null ? formatNum(g.latest) : "—"}</span>
          <span className="goal-target">{cmpWord} {formatNum(g.target)}{g.unit ? ` ${g.unit}` : ""}</span>
        </div>
        <div className="goal-bar"><span className="goal-fill" style={{ width: `${Math.max(3, g.adherence)}%` }} /></div>
        <div className="goal-foot">
          <span>{g.adherence}% · {metDays}/{g.days} days</span>
          <span className={g.trend_good ? "good" : ""}>
            {g.streak > 0 ? `${g.streak}-day streak` : `${trendArrow} ${g.trend}`}
          </span>
        </div>
      </button>
      <div className="goal-actions">
        <button className="goal-act" onClick={beginEdit} aria-label={`Edit ${g.label} goal`}><Glyph name="Edit" /></button>
        <button className="goal-act goal-del" onClick={() => onRemove(g.id)} aria-label={`Delete ${g.label} goal`}>✕</button>
      </div>
    </div>
  );
}

function AddGoalForm({
  types, dailyCache, existing, onAdd, onClose,
}: {
  types: DataTypeInfo[]; dailyCache: Record<string, Point[]>; existing: Set<string>;
  onAdd: (m: string, c: "gte" | "lte", t: number) => Promise<void>; onClose: () => void;
}) {
  const options = useMemo(
    () => types.filter((t) => (dailyCache[t.name] ?? []).some((p) => p.value != null) && !existing.has(t.name)),
    [types, dailyCache, existing]
  );
  const [metric, setMetric] = useState(options[0]?.name ?? "");
  const [comparator, setComparator] = useState<"gte" | "lte">("gte");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);

  // Smart default: preselect a lower/upper comparator by the metric's good direction and
  // prefill the target with a rounded recent average, so the form lands ready to submit.
  useEffect(() => {
    if (!metric) return;
    const vals = seriesVals(dailyCache[metric]).slice(-28);
    const avg = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
    setComparator(GOOD_WHEN_DOWN.has(metric) ? "lte" : "gte");
    const step = avg >= 1000 ? 500 : avg >= 100 ? 10 : avg >= 10 ? 1 : 0.5;
    setTarget(String(Math.round(avg / step) * step));
  }, [metric, dailyCache]);

  const info = types.find((t) => t.name === metric);
  async function submit() {
    const t = parseFloat(target);
    if (!metric || Number.isNaN(t)) return;
    setBusy(true);
    try { await onAdd(metric, comparator, t); onClose(); } finally { setBusy(false); }
  }

  if (!options.length) return <p className="muted goal-form-empty">Every metric with data already has a goal. Delete one to add another.</p>;
  return (
    <div className="goal-form rise">
      <label className="gf-field">
        <span className="gf-k">Metric</span>
        <select value={metric} onChange={(e) => setMetric(e.target.value)} className="gf-select">
          {options.map((o) => <option key={o.name} value={o.name}>{o.label}</option>)}
        </select>
      </label>
      <div className="gf-field">
        <span className="gf-k">Target</span>
        <div className="gf-cmp">
          <button type="button" className={comparator === "gte" ? "on" : ""} onClick={() => setComparator("gte")}>at least</button>
          <button type="button" className={comparator === "lte" ? "on" : ""} onClick={() => setComparator("lte")}>at most</button>
        </div>
      </div>
      <label className="gf-field gf-num">
        <span className="gf-k">Value</span>
        <div className="gf-inputwrap">
          <input type="number" value={target} onChange={(e) => setTarget(e.target.value)} className="gf-target"
            onKeyDown={(e) => e.key === "Enter" && submit()} />
          <span className="gf-unit">{info?.unit}</span>
        </div>
      </label>
      <div className="gf-actions">
        <button className="btn btn-lime" onClick={submit} disabled={busy || !metric}>{busy ? "Adding…" : "Add goal"}</button>
        <button className="btn" onClick={onClose}>Cancel</button>
      </div>
    </div>
  );
}

function GoalsAggregate({ summary, scored }: { summary: GoalsResponse["summary"]; scored: Goal[] }) {
  const color = scoreColor(summary.overall);
  return (
    <div className="goals-agg">
      <GoalRing pct={summary.overall} color={color} />
      <div className="agg-copy">
        <div className="agg-k">Overall consistency</div>
        <div className="agg-narr">{summary.narrative}</div>
        <div className="agg-dots" aria-hidden>
          {scored.map((g) => (
            <span key={g.id} className="agg-dot" title={`${g.label}: ${GOAL_STATUS[g.status].word}`}
              style={{ background: goalColor(g.status) }} />
          ))}
        </div>
      </div>
    </div>
  );
}

function GoalsSection({
  goals, summary, types, dailyCache, onOpen, onAdd, onEdit, onRemove, compact, onManage,
}: {
  goals: Goal[]; summary: GoalsResponse["summary"] | null; types: DataTypeInfo[];
  dailyCache: Record<string, Point[]>; onOpen: (m: string) => void;
  onAdd: (m: string, c: "gte" | "lte", t: number) => Promise<void>;
  onEdit: (id: number, patch: { target?: number; comparator?: "gte" | "lte" }) => Promise<void>;
  onRemove: (id: number) => void;
  compact?: boolean; onManage?: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const existing = useMemo(() => new Set(goals.map((g) => g.data_type)), [goals]);
  const scored = goals.filter((g) => g.latest != null);

  // Compact teaser for the Overview landing: the rollup + a link into the full tab.
  if (compact) {
    return (
      <section className="section rise" style={{ animationDelay: "120ms" }}>
        <div className="sec-head">
          <span className="sec-glyph"><Glyph name="Goals" /></span>
          <h2 className="sec-title">Goals</h2>
          {summary && summary.scored > 0 && <span className="sec-count">{summary.on_track}/{summary.scored} on track</span>}
          <button className="btn goals-new" onClick={onManage}>Manage →</button>
        </div>
        <hr className="sec-rule" />
        {summary && scored.length > 0
          ? <GoalsAggregate summary={summary} scored={scored} />
          : <p className="muted">No goals yet — open Goals to add one.</p>}
      </section>
    );
  }

  return (
    <section className="section rise" style={{ animationDelay: "40ms" }}>
      <div className="sec-head">
        <span className="sec-glyph"><Glyph name="Goals" /></span>
        <h2 className="sec-title">Goals</h2>
        {summary && summary.scored > 0 && <span className="sec-count">{summary.on_track}/{summary.scored} on track</span>}
        <button className="btn goals-new" onClick={() => setAdding((a) => !a)}>{adding ? "Close" : "+ New goal"}</button>
      </div>
      <p className="sec-blurb">Targets you set — and whether you're getting there.</p>
      <hr className="sec-rule" />

      {adding && (
        <AddGoalForm types={types} dailyCache={dailyCache} existing={existing}
          onAdd={onAdd} onClose={() => setAdding(false)} />
      )}

      {summary && scored.length > 0 && <GoalsAggregate summary={summary} scored={scored} />}

      {goals.length > 0 ? (
        <div className="goals-grid">
          {goals.map((g) => <GoalCard key={g.id} g={g} onOpen={onOpen} onEdit={onEdit} onRemove={onRemove} />)}
        </div>
      ) : (
        !adding && <p className="muted">No goals yet — add one to start tracking a target.</p>
      )}
    </section>
  );
}

/* ————— metric tile ————— */

function MetricTile({
  t, series, onOpen, delay,
}: {
  t: DataTypeInfo; series: Point[]; onOpen: (name: string) => void; delay?: number;
}) {
  const v = latestVal(series);
  const fresh = freshness(series);
  if (v == null) {
    return (
      <div className="tile dashed rise" style={{ animationDelay: `${delay ?? 0}ms` }}>
        <div className="tile-top"><span className="tile-label">{t.label}</span></div>
        <div className="tile-await">awaiting data</div>
      </div>
    );
  }
  const base = baseline(series);
  const partialToday = CUMULATIVE.has(t.name) && lastDay(series) === localToday();
  const delta = base != null && !partialToday ? v - base : null;
  const good = delta == null ? false : GOOD_WHEN_DOWN.has(t.name) ? delta < 0 : delta > 0;
  return (
    <button className="tile rise" style={{ animationDelay: `${delay ?? 0}ms` }} onClick={() => onOpen(t.name)}>
      <div className="tile-top">
        <span className="tile-label">{t.label}</span>
        {fresh && <span className={`tile-fresh${fresh.stale ? " stale" : ""}`}>{fresh.label}</span>}
      </div>
      <div className="tile-val">
        {formatNum(v)}
        {t.unit && <span className="u">{t.unit}</span>}
      </div>
      {delta != null && Math.abs(delta) > 0.01 ? (
        <div className={`tile-delta ${good ? "up" : "down"}`} title={`28-day average: ${formatNum(base!)}${t.unit ? ` ${t.unit}` : ""}`}>
          {delta > 0 ? "▲" : "▼"} {formatNum(Math.abs(delta))} <span className="vs">vs 28d</span>
        </div>
      ) : (
        <div className="tile-delta">{partialToday ? "in progress" : " "}</div>
      )}
      <div className="tile-spark"><Spark points={series.slice(-21)} unit={t.unit} /></div>
    </button>
  );
}

/* ————— metric inspector drawer ————— */

const RANGES = [
  { key: "14", label: "14D", days: 14 },
  { key: "30", label: "30D", days: 30 },
  { key: "90", label: "90D", days: 90 },
  { key: "all", label: "ALL", days: Infinity },
] as const;

const tooltipStyle = {
  background: "#131519",
  border: "1px solid rgba(255,255,255,0.14)",
  borderRadius: 10,
  fontSize: 12,
} as const;

// recharts renders colors as SVG presentation attributes, which don't resolve CSS var(),
// so chart structure/series colors are picked from the live theme here. A dark tooltip
// (above) reads fine over either background, so it stays constant. Matches the token palette.
function chartInk() {
  const light = typeof document !== "undefined" && document.documentElement.getAttribute("data-theme") === "light";
  return light
    ? { grid: "rgba(18,22,28,0.07)", refFaint: "rgba(18,22,28,0.22)", axis: "#838a94", lime: "#5f8c0f", cyan: "#1f8ea4" }
    : { grid: "rgba(255,255,255,0.05)", refFaint: "rgba(255,255,255,0.22)", axis: "#646b78", lime: "#cdf24e", cyan: "#7fe3ef" };
}

function Drawer({
  info, series, intraday, intradayLoading, goal, onClose,
}: {
  info: DataTypeInfo; series: Point[]; intraday: Point[]; intradayLoading: boolean;
  goal?: Goal; onClose: () => void;
}) {
  const [range, setRange] = useState<(typeof RANGES)[number]["key"]>("30");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  const windowed = useMemo(() => {
    const days = RANGES.find((r) => r.key === range)!.days;
    if (!isFinite(days)) return series;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    const iso = cutoff.toLocaleDateString("en-CA");
    return series.filter((p) => (p.day ?? "") >= iso);
  }, [series, range]);

  const v = latestVal(series);
  const fresh = freshness(series);
  const base28 = baseline(series);
  const avg7 = avgLast(series, 7);
  const winVals = seriesVals(windowed);
  const hi = winVals.length ? Math.max(...winVals) : null;
  const lo = winVals.length ? Math.min(...winVals) : null;
  // Long ranges get noisy at daily granularity — roll them into weekly averages so the
  // shape of the trend, not the day-to-day jitter, is what reads.
  const weekly = windowed.length > 45;
  const data = useMemo(
    () => (weekly ? bucketWeekly(windowed) : windowed.map((p) => ({ x: p.day, y: p.value }))),
    [windowed, weekly]
  );

  // All-time record over the full series (not just the window): the good extreme for
  // directional metrics, a neutral peak otherwise.
  const lowerBetter = GOOD_WHEN_DOWN.has(info.name);
  const neutral = NEUTRAL_METRIC.has(info.name);
  const record = useMemo(() => {
    const pts = series.filter((p) => p.value != null && p.day) as { day: string; value: number; unit: string }[];
    if (pts.length < 5) return null;
    const pick = pts.reduce((b, p) => ((lowerBetter && !neutral ? p.value < b.value : p.value > b.value) ? p : b));
    return pick;
  }, [series, lowerBetter, neutral]);
  const recordLabel = neutral ? "Peak" : lowerBetter ? "Lowest ever" : "Personal best";
  const ci = chartInk();

  return (
    <>
      <div className="veil" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`${info.label} detail`}>
        <div className="dw-head">
          <div>
            <span className="dw-kicker"><Glyph name={info.group || "Other"} />{info.group}</span>
            <h2 className="dw-title">{info.label}</h2>
          </div>
          <button className="dw-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {v != null && (
          <div className="dw-val">
            {formatNum(v)}
            {info.unit && <span className="u">{info.unit}</span>}
          </div>
        )}
        {fresh && <div className={`dw-fresh${fresh.stale ? " stale" : ""}`}>last reading · {fresh.label}</div>}

        <div className="ranges" role="tablist" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r.key} role="tab" aria-selected={range === r.key}
              className={`range${range === r.key ? " active" : ""}`} onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>

        <div className="statrow">
          <div className="stat"><div className="stat-k">Latest</div>
            <div className="stat-v">{v != null ? formatNum(v) : "—"}<span className="u">{info.unit}</span></div></div>
          <div className="stat"><div className="stat-k">7d avg</div>
            <div className="stat-v">{avg7 != null ? formatNum(avg7) : "—"}<span className="u">{info.unit}</span></div></div>
          <div className="stat"><div className="stat-k">28d avg</div>
            <div className="stat-v">{base28 != null ? formatNum(base28) : "—"}<span className="u">{info.unit}</span></div></div>
          <div className="stat"><div className="stat-k">Range</div>
            <div className="stat-v">{lo != null && hi != null ? `${formatNum(lo)}–${formatNum(hi)}` : "—"}</div></div>
        </div>

        {record && (
          <div className="dw-record">
            <span className="dw-record-k">{recordLabel}</span>
            <span className="dw-record-v">{formatNum(record.value)}{info.unit ? ` ${info.unit}` : ""}</span>
            <span className="dw-record-d">{fmtDayLong(record.day)}</span>
          </div>
        )}

        {goal && (
          <div className="dw-goal" style={{ "--gc": goalColor(goal.status) } as CSSProperties}>
            <div className="dw-goal-top">
              <span className="dw-goal-k">Your goal</span>
              <span className="dw-goal-badge">{GOAL_STATUS[goal.status].word}</span>
            </div>
            <div className="dw-goal-target">
              {goal.comparator === "gte" ? "at least" : "at most"} <strong>{formatNum(goal.target)}{info.unit ? ` ${info.unit}` : ""}</strong>
            </div>
            <div className="dw-goal-bar"><span style={{ width: `${Math.max(3, goal.adherence)}%` }} /></div>
            <div className="dw-goal-foot">
              <span>{goal.adherence}% · met {Math.round((goal.adherence / 100) * goal.days)} of {goal.days} days</span>
              <span className={goal.trend_good ? "good" : ""}>
                {goal.streak > 0 ? `${goal.streak}-day streak` : goal.trend}
              </span>
            </div>
          </div>
        )}

        {data.length === 0 ? (
          <p className="muted">No data in this window.</p>
        ) : (
          <>
            <div className="chart-title">{weekly ? "Weekly average" : "Daily"} · {info.unit || "value"}</div>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={data} margin={{ left: -6, right: 8, top: 6 }}>
                <defs>
                  <linearGradient id="ga" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ci.lime} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={ci.lime} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={ci.grid} vertical={false} />
                <XAxis dataKey="x" stroke={ci.axis} fontSize={11} minTickGap={46}
                  tickLine={false} axisLine={false} tickFormatter={(d) => fmtDay(d)} />
                <YAxis stroke={ci.axis} fontSize={11} width={44} domain={["auto", "auto"]}
                  tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#9aa1ab" }}
                  labelFormatter={(d) => fmtDayLong(String(d))}
                  formatter={(val) => [`${formatNum(Number(val))}${info.unit ? ` ${info.unit}` : ""}`, info.label]} />
                {base28 != null && (
                  <ReferenceLine y={base28} stroke={ci.refFaint} strokeDasharray="5 5"
                    label={{ value: "28d avg", position: "insideTopRight", fill: ci.axis, fontSize: 10 }} />
                )}
                {goal && (
                  <ReferenceLine y={goal.target} stroke={goalColor(goal.status)} strokeDasharray="6 3" strokeWidth={1.5}
                    label={{ value: `goal ${goal.comparator === "gte" ? "≥" : "≤"} ${formatNum(goal.target)}`,
                      position: "insideBottomRight", fill: goalColor(goal.status), fontSize: 10 }} />
                )}
                <Area type="monotone" dataKey="y" stroke={ci.lime} fill="url(#ga)"
                  strokeWidth={2} connectNulls dot={false} activeDot={{ r: 3.5, strokeWidth: 0 }}
                  isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </>
        )}

        {info.intraday && (
          <>
            <div className="chart-title">Intraday · last 2 days</div>
            {intradayLoading ? (
              <p className="muted">Loading…</p>
            ) : intraday.length === 0 ? (
              <p className="muted">No intraday points in range.</p>
            ) : (
              <ResponsiveContainer width="100%" height={170}>
                <LineChart data={intraday.map((p) => ({ x: p.ts, y: p.value }))} margin={{ left: -6, right: 8 }}>
                  <CartesianGrid stroke={ci.grid} vertical={false} />
                  <XAxis dataKey="x" stroke={ci.axis} fontSize={10} minTickGap={64}
                    tickLine={false} axisLine={false} tickFormatter={(t) => fmtClock(String(t))} />
                  <YAxis stroke={ci.axis} fontSize={11} width={44} domain={["auto", "auto"]}
                    tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "#9aa1ab" }}
                    labelFormatter={(t) => new Date(String(t)).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                    formatter={(val) => [`${formatNum(Number(val))}${info.unit ? ` ${info.unit}` : ""}`, info.label]} />
                  <Line type="monotone" dataKey="y" stroke={ci.cyan} dot={false} strokeWidth={1.5}
                    isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </>
        )}
      </aside>
    </>
  );
}

/* ————— the dashboard ————— */

export default function Dashboard() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [tokenDays, setTokenDays] = useState<number | null>(null);
  const [types, setTypes] = useState<DataTypeInfo[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [syncFlash, setSyncFlash] = useState<string | null>(null);
  const [lastSynced, setLastSynced] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [view, setView] = useState("overview");
  const navRef = useRef<HTMLElement>(null);
  const [navStuck, setNavStuck] = useState(false);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");
  const [dailyCache, setDailyCache] = useState<Record<string, Point[]>>({});
  const [intradayCache, setIntradayCache] = useState<Record<string, Point[]>>({});
  const [readiness, setReadiness] = useState<{
    date: string; score: number; tone: string; narrative: string;
    components: { key: string; label: string; score: number; value: number; unit: string; delta: number | null; good: boolean; }[];
  } | null>(null);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [coachRecs, setCoachRecs] = useState<Recommendation[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [goalsSummary, setGoalsSummary] = useState<GoalsResponse["summary"] | null>(null);
  const [benchmarks, setBenchmarks] = useState<BenchmarksResponse | null>(null);
  const [sleepDetail, setSleepDetail] = useState<SleepDetail | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function loadGoals() {
    try {
      const g = await api.goals();
      setGoals(g.goals);
      setGoalsSummary(g.summary);
    } catch { /* goals are optional; keep the rest of the dashboard alive */ }
  }

  async function refreshSyncMeta() {
    try {
      const rows = await api.syncStatus();
      const latest = rows.map((r) => r.last_sync_at).filter(Boolean).sort().at(-1);
      setLastSynced(latest ?? null);
    } catch { /* cosmetic only */ }
  }

  useEffect(() => {
    (async () => {
      try {
        const h = await api.health();
        setAuthed(h.authenticated);
        setTokenDays(h.token_days_left);
        const [dts, bulk, r, ins, rec, bm, sd] = await Promise.all([
          api.dataTypes(),
          api.dailyBulk(),
          api.readiness().catch(() => null),
          api.insights().then((x) => x.insights).catch(() => []),
          api.coach().then((x) => x.recommendations).catch(() => []),
          api.benchmarks().catch(() => null),
          api.sleepDetail().catch(() => null),
        ]);
        setTypes(dts);
        setDailyCache(bulk.series);
        setReadiness(r);
        setInsights(ins);
        setCoachRecs(rec);
        setBenchmarks(bm);
        setSleepDetail(sd);
        loadGoals();
        refreshSyncMeta();
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);

  // Deep-link the open metric via ?m= so a metric view is shareable/bookmarkable
  // (and restores on reload). Reads once on mount, then mirrors state → URL.
  useEffect(() => {
    const m = new URLSearchParams(window.location.search).get("m");
    if (m) setOpen(m);
  }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (open) url.searchParams.set("m", open);
    else url.searchParams.delete("m");
    window.history.replaceState(null, "", url);
  }, [open]);

  // Deep-link the active tab via ?v= so views are bookmarkable and browser back works.
  useEffect(() => {
    const v = new URLSearchParams(window.location.search).get("v");
    if (v) setView(v);
  }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (view && view !== "overview") url.searchParams.set("v", view);
    else url.searchParams.delete("v");
    window.history.replaceState(null, "", url);
  }, [view]);

  // Lazy intraday fetch when the inspector opens on an intraday-capable metric.
  useEffect(() => {
    if (!open) return;
    const info = types.find((t) => t.name === open);
    if (!info?.intraday || intradayCache[open]) return;
    let alive = true;
    const start = new Date();
    start.setDate(start.getDate() - 2);
    api
      .intraday(open, start.toISOString().slice(0, 10), undefined, 1500)
      .then((i) => alive && setIntradayCache((p) => ({ ...p, [open]: i.points })))
      .catch(() => alive && setIntradayCache((p) => ({ ...p, [open]: [] })));
    return () => { alive = false; };
  }, [open, types, intradayCache]);

  const infoByName = useMemo(() => Object.fromEntries(types.map((t) => [t.name, t])), [types]);
  const groups = useMemo(() => {
    const m = new Map<string, DataTypeInfo[]>();
    for (const t of types) {
      const g = t.group || "Other";
      (m.get(g) ?? m.set(g, []).get(g)!).push(t);
    }
    return GROUP_ORDER.filter((g) => m.has(g)).map((g) => [g, m.get(g)!] as const);
  }, [types]);

  const highlights = useMemo(
    () => HIGHLIGHTS.map((n) => infoByName[n]).filter((t) => t && latestVal(dailyCache[t.name]) != null),
    [infoByName, dailyCache]
  );

  const readinessStrip = useMemo(() => {
    const byDay = new Map((dailyCache["readiness"] ?? []).map((p) => [p.day, p.value] as const));
    const out: { day: string; value: number | null }[] = [];
    const d = new Date();
    d.setDate(d.getDate() - 27);
    for (let i = 0; i < 28; i++) {
      const iso = d.toLocaleDateString("en-CA");
      out.push({ day: iso, value: byDay.get(iso) ?? null });
      d.setDate(d.getDate() + 1);
    }
    return out;
  }, [dailyCache]);

  async function runSync() {
    setSyncing(true);
    setError(null);
    setSyncFlash(null);
    try {
      const res = await api.triggerSync();
      if (res.detail) {
        // HTTP-level failure (expired token, bad request) — nothing was synced.
        setError(res.detail);
        if (/token|auth/i.test(res.detail)) setAuthed(false);
      } else if (!res.ok && res.results) {
        const failed = res.results.filter((r) => r.error);
        if (failed.length) {
          setError(
            `${failed.length} type${failed.length > 1 ? "s" : ""} failed — ` +
            `${failed[0].data_type}: ${failed[0].error?.slice(0, 140)}`
          );
        }
      } else if (res.ok) {
        setSyncFlash(`synced · ${(res.total_rows ?? 0).toLocaleString()} rows`);
        if (flashTimer.current) clearTimeout(flashTimer.current);
        flashTimer.current = setTimeout(() => setSyncFlash(null), 5000);
      }
      const [bulk, r, ins, rec, bm, sd] = await Promise.all([
        api.dailyBulk(),
        api.readiness().catch(() => null),
        api.insights().then((x) => x.insights).catch(() => []),
        api.coach().then((x) => x.recommendations).catch(() => []),
        api.benchmarks().catch(() => null),
        api.sleepDetail().catch(() => null),
      ]);
      setDailyCache(bulk.series);
      setIntradayCache({});
      setReadiness(r);
      setInsights(ins);
      setCoachRecs(rec);
      setBenchmarks(bm);
      setSleepDetail(sd);
      loadGoals();
      refreshSyncMeta();
    } catch (e) {
      setError(String(e));
    } finally {
      setSyncing(false);
    }
  }

  // LLM daily briefing — read instantly from the store; regenerated post-sync/on demand.
  const [briefingData, setBriefingData] = useState<Briefing | null>(null);
  const [briefingBusy, setBriefingBusy] = useState(false);
  useEffect(() => {
    api.briefing().then((r) => setBriefingData(r.briefing)).catch(() => {});
  }, []);
  async function refreshBriefing() {
    setBriefingBusy(true);
    try {
      const r = await api.refreshBriefing();
      setBriefingData(r.briefing);
    } catch {
      /* keep the stored briefing; the stale timestamp tells the story */
    } finally {
      setBriefingBusy(false);
    }
  }

  const openInfo = open ? infoByName[open] : null;
  const tabs = [
    { id: "overview", label: "Overview" },
    ...(sleepDetail ? [{ id: "sleep", label: "Sleep" }] : []),
    ...(benchmarks ? [{ id: "standing", label: "Standing" }] : []),
    { id: "goals", label: "Goals" },
    ...(insights.length ? [{ id: "insights", label: "Insights" }] : []),
    { id: "metrics", label: "Metrics" },
  ];

  async function addGoal(data_type: string, comparator: "gte" | "lte", target: number) {
    await api.createGoal({ data_type, comparator, target });
    await loadGoals();
  }
  async function removeGoal(id: number) {
    setGoals((gs) => gs.filter((g) => g.id !== id)); // optimistic
    await api.deleteGoal(id);
    await loadGoals();
  }
  async function editGoal(id: number, patch: { target?: number; comparator?: "gte" | "lte" }) {
    setGoals((gs) => gs.map((g) => (g.id === id ? { ...g, ...patch } : g))); // optimistic
    await api.patchGoal(id, patch);
    await loadGoals();
  }
  function go(v: string) {
    setView(v);
    requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  }

  // Theme: 'system' follows the OS; 'light'/'dark' pin it. The <head> script sets the initial
  // data-theme before paint; here we mirror the saved choice and react to OS changes.
  useEffect(() => {
    const saved = localStorage.getItem("theme");
    setTheme(saved === "light" || saved === "dark" ? saved : "system");
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onSys = () => {
      if ((localStorage.getItem("theme") ?? "system") === "system")
        document.documentElement.setAttribute("data-theme", mq.matches ? "dark" : "light");
    };
    mq.addEventListener("change", onSys);
    return () => mq.removeEventListener("change", onSys);
  }, []);

  function applyTheme(next: "system" | "light" | "dark") {
    const sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", next === "system" ? (sysDark ? "dark" : "light") : next);
    if (next === "system") localStorage.removeItem("theme");
    else localStorage.setItem("theme", next);
    setTheme(next);
  }
  const cycleTheme = () =>
    applyTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system");

  // The nav is transparent until it pins to the top, then a subtle backdrop fades in
  // so tabs stay legible over scrolling content without a band that "sticks out" at rest.
  useEffect(() => {
    const onScroll = () => {
      const el = navRef.current;
      if (el) setNavStuck(el.getBoundingClientRect().top <= 0.5);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // ⌘K / Ctrl-K toggles the command palette anywhere; "/" opens it too, unless you're
  // typing in a field (goal editor, etc.). Escape is handled inside the palette.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !paletteOpen) {
        const el = document.activeElement as HTMLElement | null;
        const typing = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
        if (!typing) { e.preventDefault(); setPaletteOpen(true); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen]);

  const loading = authed === null && !error;

  return (
    <div className="shell">
      <div className="topbar">
        <div className="wordmark">
          <span className="dot" />
          <h1>fitbit<em>+</em></h1>
        </div>
        <div className="controls">
          <button className="searchpill" onClick={() => setPaletteOpen(true)}
            aria-label="Search metrics" title="Search metrics (⌘K)">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" aria-hidden>
              <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
            </svg>
            <span className="searchpill-label">Search</span>
            <kbd className="searchpill-kbd">⌘K</kbd>
          </button>
          <button className="btn theme-toggle" onClick={cycleTheme}
            title={`Theme: ${theme}${theme === "system" ? " · follows your device" : ""}`}
            aria-label={`Theme: ${theme}. Click to change.`}>
            {theme === "system" ? (
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <rect x="3" y="4" width="18" height="12" rx="1.5" /><path d="M8 20h8M12 16v4" />
              </svg>
            ) : theme === "light" ? (
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M19.5 14.5A8 8 0 0 1 9.5 4.3 8 8 0 1 0 19.5 14.5z" />
              </svg>
            )}
          </button>
          {syncFlash && <span className="flash">{syncFlash}</span>}
          {lastSynced && !syncFlash && <span className="syncmeta">synced {relTime(lastSynced)}</span>}
          {authed === null ? (
            <span className="pill">checking…</span>
          ) : authed ? (
            <span className="pill ok">connected</span>
          ) : (
            <a className="btn btn-lime" href={api.loginUrl()}>Connect Google Health</a>
          )}
          {authed && tokenDays != null && (
            <span className={`pill${tokenDays <= 2 ? " warn" : ""}`} title="Testing-mode tokens last 7 days from consent">
              {tokenDays <= 0 ? "re-auth needed" : `re-auth in ${Math.floor(tokenDays)}d`}
            </span>
          )}
          <button className="btn btn-lime" onClick={runSync} disabled={syncing || !authed}>
            {syncing && <span className="spinner" aria-hidden />}
            {syncing ? "Syncing" : "Sync"}
          </button>
        </div>
      </div>

      {error && (
        <div className="toast" role="alert">
          <span>{error}</span>
          <button onClick={() => setError(null)} aria-label="Dismiss">✕</button>
        </div>
      )}

      {loading && (
        <div style={{ marginTop: 34 }}>
          <div className="skel skel-hero" />
          <div className="skel-row">
            {Array.from({ length: 5 }).map((_, i) => <div className="skel skel-tile" key={i} />)}
          </div>
        </div>
      )}

      {!loading && (
        <>
          {/* ———— tab bar ———— */}
          <nav ref={navRef} className={`secnav${navStuck ? " stuck" : ""}`} aria-label="Views">
            {tabs.map((t) => (
              <button key={t.id} className={`chip${view === t.id ? " active" : ""}`} onClick={() => go(t.id)}>
                {t.label}
              </button>
            ))}
            <a className="chip chip-coach" href="/coach" title="Ask your data anything">
              Coach<span className="coach-star" aria-hidden>✦</span>
            </a>
          </nav>

          {/* ———— OVERVIEW · readiness hero ———— */}
          {view === "overview" && readiness && (
            <section
              className="hero rise"
              style={{ animationDelay: "40ms", "--hero-glow": `${scoreColor(readiness.score)}12` } as CSSProperties}
            >
              <div className="ring-wrap">
                <Ring score={readiness.score} />
              </div>
              <div className="hero-copy">
                <p className="eyebrow" style={{ marginBottom: 10 }}>{heroDateLabel(readiness.date)}</p>
                <h2 className="tone">
                  You're <span className="accent" style={{ color: scoreColor(readiness.score) }}>{readiness.tone}</span>
                </h2>
                <p className="narrative">{readiness.narrative}</p>
                <div className="drivers">
                  {readiness.components.map((c) => {
                    const metric = DRIVER_METRIC[c.key];
                    const canOpen = metric && infoByName[metric];
                    return (
                      <button
                        className="driver" key={c.key} disabled={!canOpen}
                        onClick={() => canOpen && setOpen(metric)}
                        title={canOpen ? `Open ${c.label} · ${c.score}/100` : `${c.score}/100`}
                      >
                        <span className="d-label">{c.label}</span>
                        <span className="d-track">
                          <span className="d-fill" style={{ width: `${c.score}%`, background: scoreColor(c.score) }} />
                        </span>
                        <span className="d-val">
                          {formatNum(c.value)} <span className="u">{c.unit}</span>
                          {c.delta != null && (
                            <span className={`tr ${c.good ? "good" : "off"}`} title={`${c.delta >= 0 ? "+" : ""}${formatNum(c.delta)} vs baseline`}>
                              {c.delta >= 0 ? "▲" : "▼"}
                            </span>
                          )}
                        </span>
                      </button>
                    );
                  })}
                </div>
                {readinessStrip.some((s) => s.value != null) && (
                  <ReadinessStrip data={readinessStrip} />
                )}
              </div>
            </section>
          )}

          {/* ———— OVERVIEW · today's focus + at a glance + goals & insights teasers ———— */}
          {view === "overview" && (
            <>
              <FocusModule recs={coachRecs} onOpen={setOpen} canOpen={(m) => !!infoByName[m]} />

              {highlights.length > 0 && (
                <section className="section rise" style={{ animationDelay: "80ms" }}>
                  <p className="eyebrow">At a glance</p>
                  <div className="tiles">
                    {highlights.map((t, i) => (
                      <MetricTile key={t.name} t={t} series={dailyCache[t.name] ?? []} onOpen={setOpen} delay={100 + i * 30} />
                    ))}
                  </div>
                </section>
              )}

              {sleepDetail && <SleepTeaser data={sleepDetail} onOpen={() => go("sleep")} />}
              {benchmarks && <StandingTeaser data={benchmarks} onOpen={() => go("standing")} />}

              <GoalsSection
                goals={goals} summary={goalsSummary} types={types} dailyCache={dailyCache}
                onOpen={setOpen} onAdd={addGoal} onEdit={editGoal} onRemove={removeGoal}
                compact onManage={() => go("goals")}
              />

              {insights.length > 0 && (
                <section className="section rise" style={{ animationDelay: "160ms" }}>
                  <div className="sec-head">
                    <span className="sec-glyph"><Glyph name="Recovery" /></span>
                    <h2 className="sec-title">What we noticed</h2>
                    <span className="sec-count">{insights.length} signals</span>
                    <button className="btn goals-new" onClick={() => go("insights")}>See all →</button>
                  </div>
                  <hr className="sec-rule" />
                  <div className="insights-grid">
                    {insights.slice(0, 4).map((ins, i) => (
                      <InsightCard key={ins.id} ins={ins} onOpen={setOpen} delay={80 + i * 45} />
                    ))}
                  </div>
                </section>
              )}
            </>
          )}

          {/* ———— GOALS ———— */}
          {view === "goals" && (
            <GoalsSection
              goals={goals} summary={goalsSummary} types={types} dailyCache={dailyCache}
              onOpen={setOpen} onAdd={addGoal} onEdit={editGoal} onRemove={removeGoal}
            />
          )}

          {/* ———— INSIGHTS ———— */}
          {view === "insights" && (
            <>
              {briefingData && (
                <section className="briefing rise" style={{ animationDelay: "20ms" }}>
                  <div className="briefing-head">
                    <p className="eyebrow briefing-eyebrow">
                      <span className="briefing-star" aria-hidden>✦</span> Today&apos;s read
                    </p>
                    <button className="btn briefing-refresh" onClick={refreshBriefing} disabled={briefingBusy}>
                      {briefingBusy && <span className="spinner" aria-hidden />}
                      {briefingBusy ? "Reading your data…" : "Refresh"}
                    </button>
                  </div>
                  <h2 className="briefing-headline">{briefingData.headline}</h2>
                  <p className="briefing-narrative">{briefingData.narrative}</p>
                  <div className="insights-grid">
                    {briefingData.insights.map((ins, i) => (
                      <InsightCard key={ins.id} ins={ins} onOpen={setOpen} delay={60 + i * 45} />
                    ))}
                  </div>
                  <p className="briefing-meta">
                    Synthesized from the computed signals below · {briefingData.model?.split("/").pop() ?? "llm"} ·
                    updated {new Date(briefingData.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </p>
                </section>
              )}

              {insights.length > 0 && (
                <section className="section rise" style={{ animationDelay: "40ms" }}>
                  <div className="sec-head">
                    <span className="sec-glyph"><Glyph name="Recovery" /></span>
                    <h2 className="sec-title">What we noticed</h2>
                    <span className="sec-count">{insights.length} signals</span>
                  </div>
                  <p className="sec-blurb">Patterns pulled from your history — ranked by what matters most today.</p>
                  <hr className="sec-rule" />
                  <div className="insights-grid">
                    {insights.map((ins, i) => (
                      <InsightCard key={ins.id} ins={ins} onOpen={setOpen} delay={40 + i * 40} />
                    ))}
                  </div>
                </section>
              )}
            </>
          )}

          {/* ———— SLEEP deep-dive ———— */}
          {view === "sleep" && <SleepView data={sleepDetail} />}

          {/* ———— STANDING · peer benchmarks ———— */}
          {view === "standing" && <BenchmarksView data={benchmarks} />}

          {/* ———— METRICS · all focus areas ———— */}
          {view === "metrics" && groups.map(([g, gTypes], gi) => {
            const live = gTypes.filter((t) => latestVal(dailyCache[t.name]) != null).length;
            return (
              <section className="section rise" key={g} style={{ animationDelay: `${gi * 40}ms` }}>
                <div className="sec-head">
                  <span className="sec-glyph"><Glyph name={g} /></span>
                  <h2 className="sec-title">{g}</h2>
                  <span className="sec-count">{live}/{gTypes.length} live</span>
                </div>
                <p className="sec-blurb">{GROUP_BLURB[g]}</p>
                <hr className="sec-rule" />
                {g === "Sleep" && <SleepStages cache={dailyCache} />}
                <div className="tiles">
                  {gTypes.map((t, i) => (
                    <MetricTile key={t.name} t={t} series={dailyCache[t.name] ?? []} onOpen={setOpen} delay={i * 22} />
                  ))}
                </div>
              </section>
            );
          })}

          <footer className="foot">
            <span>fitbit+ · your data, kept close</span>
            <span>{types.length} metrics registered{lastSynced ? ` · synced ${relTime(lastSynced)}` : ""}</span>
          </footer>
        </>
      )}

      {/* ———— inspector ———— */}
      {openInfo && (
        <Drawer
          info={openInfo}
          series={dailyCache[openInfo.name] ?? []}
          intraday={intradayCache[openInfo.name] ?? []}
          intradayLoading={!!openInfo.intraday && !(openInfo.name in intradayCache)}
          goal={goals.find((g) => g.data_type === openInfo.name)}
          onClose={() => setOpen(null)}
        />
      )}

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        types={types}
        dailyCache={dailyCache}
        tabs={[...tabs, { id: "coach", label: "Coach — ask your data" }]}
        onOpenMetric={(name) => setOpen(name)}
        onGoView={(id) => (id === "coach" ? window.location.assign("/coach") : go(id))}
      />
    </div>
  );
}

function heroDateLabel(iso: string): string {
  const pretty = new Date(iso + "T12:00:00").toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric",
  });
  return `${iso === localToday() ? "Today" : "Latest"} · ${pretty}`;
}
