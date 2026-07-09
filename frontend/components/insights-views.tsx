"use client";

/* ————————————————————————————————————————————————————————————————
   Insight surfaces built on the existing design language:
     · BenchmarksView — where you stand vs. evidence-based reference norms
     · SleepView       — the narrative behind the nightly score
   Both are presentational: the dashboard fetches and passes the data.
   ———————————————————————————————————————————————————————————————— */

import { CSSProperties } from "react";
import {
  Area, AreaChart, CartesianGrid, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Benchmark, BenchmarksResponse, SleepDetail } from "@/lib/api";

const MoonGlyph = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M19.5 14.5A8 8 0 0 1 9.5 4.3 8 8 0 1 0 19.5 14.5z" />
  </svg>
);
const BarsGlyph = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M4 20V6M9 20V10M14 20v-7M19 20V4" />
  </svg>
);

/* ————— shared bits ————— */

// Tones map to the existing signal tokens, so both themes adapt for free.
const TONE: Record<string, string> = {
  under: "var(--rose)", low: "var(--rose)",
  typical: "var(--amber)", high: "var(--amber)", fair: "var(--amber)",
  good: "var(--lime)", healthy: "var(--lime)",
  optimal: "var(--cyan)",
};
const toneColor = (t: string) => TONE[t] ?? "var(--faint)";
const dim = (t: string, pct = 20) => `color-mix(in oklab, ${toneColor(t)} ${pct}%, transparent)`;

function fmt(n: number | null | undefined, dp = 1): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (Math.abs(n) >= 100 || Number.isInteger(n)) return String(Math.round(n));
  return n.toLocaleString(undefined, { maximumFractionDigits: dp });
}
function shortDay(iso: string): string {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function longDay(iso: string): string {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

// A banded track with a value marker and an optional target tick. The workhorse visual.
function ScaleBar({
  segments, markerPos, markerTone, targetPos, height = 12,
}: {
  segments: { left: number; width: number; tone: string; solid?: boolean }[];
  markerPos: number; markerTone: string; targetPos?: number | null; height?: number;
}) {
  return (
    <div className="scalebar" style={{ height }}>
      <div className="scalebar-track">
        {segments.map((s, i) => (
          <span key={i} className="scalebar-band"
            style={{ left: `${s.left}%`, width: `${s.width}%`, background: s.solid ? dim(s.tone, 34) : dim(s.tone, 15) }} />
        ))}
      </div>
      {targetPos != null && (
        <span className="scalebar-target" style={{ left: `${Math.max(0, Math.min(100, targetPos))}%` }} aria-hidden />
      )}
      <span className="scalebar-marker" style={{ left: `${Math.max(0, Math.min(100, markerPos))}%`, background: toneColor(markerTone) }} />
    </div>
  );
}

/* ————— benchmarks ————— */

export function BenchmarkRow({ b }: { b: Benchmark }) {
  const [lo, hi] = b.scale;
  const targetPos = b.target ? ((b.target.value - lo) / (hi - lo)) * 100 : null;
  const segments = b.bands.map((band) => ({
    left: band.start, width: Math.max(0, band.end - band.start), tone: band.tone,
    solid: band.tone === b.tone,
  }));
  const cmp = b.target ? (b.target.comparator === "gte" ? "≥" : "≤") : "";
  return (
    <div className="bm">
      <div className="bm-top">
        <span className="bm-label">{b.label}</span>
        <span className="bm-tier" style={{ color: toneColor(b.tone) }}>{b.tier}</span>
      </div>
      <div className="bm-value">
        {fmt(b.value)}{b.unit && <span className="u">{b.unit}</span>}
      </div>
      <ScaleBar segments={segments} markerPos={b.position} markerTone={b.tone} targetPos={targetPos} />
      <div className="bm-scale-ends">
        <span>{fmt(lo)}</span>
        <span>{fmt(hi)}{b.unit ? ` ${b.unit}` : ""}</span>
      </div>
      {b.target && (
        <div className="bm-target-note">
          <span className="bm-target-dot" /> Next: <strong>{cmp} {fmt(b.target.value)}{b.unit ? ` ${b.unit}` : ""}</strong>
          <span className="bm-target-tier"> · {b.target.label}</span>
        </div>
      )}
      <p className="bm-basis">{b.basis}</p>
      {b.caveat && <p className="bm-caveat">{b.caveat}</p>}
    </div>
  );
}

export function BenchmarksView({ data }: { data: BenchmarksResponse | null }) {
  if (!data || !data.benchmarks.length) {
    return <p className="muted">Not enough history yet to place you on the reference scales.</p>;
  }
  return (
    <section className="section rise" style={{ animationDelay: "40ms" }}>
      <div className="sec-head">
        <span className="sec-glyph"><BarsGlyph /></span>
        <h2 className="sec-title">Where you stand</h2>
        <span className="sec-count">{data.cohort}</span>
      </div>
      <p className="sec-blurb">
        Your habitual values against evidence-based reference norms — orientation and a next rung
        to reach for, not a verdict. Ordered with your biggest opportunities first.
      </p>
      <hr className="sec-rule" />
      <div className="bm-grid">
        {data.benchmarks.map((b) => <BenchmarkRow key={b.key} b={b} />)}
      </div>
      <p className="bm-foot muted">
        Reference bands are population context for a healthy adult in his mid-20s. Your own baseline
        and trend (elsewhere in the app) are the truer guide — especially for HRV.
      </p>
    </section>
  );
}

/* ————— sleep ————— */

const STAGES = [
  { key: "deep", label: "Deep", color: "#b3a4f5" },
  { key: "rem", label: "REM", color: "var(--cyan)" },
  { key: "light", label: "Light", color: "var(--lime)" },
  { key: "awake", label: "Awake", color: "var(--faint)" },
] as const;

// Per-stage display ranges so the target band and marker read clearly (% of total sleep).
const STAGE_SCALE: Record<string, [number, number]> = {
  deep: [0, 35], rem: [0, 40], light: [30, 80],
};

function SleepStat({ k, v, unit }: { k: string; v: number | null | undefined; unit?: string }) {
  return (
    <div className="stat">
      <div className="stat-k">{k}</div>
      <div className="stat-v">{fmt(v)}{unit && <span className="u">{unit}</span>}</div>
    </div>
  );
}

function ToneCard({ eyebrow, title, detail, tone, big }: {
  eyebrow: string; title: string; detail: string; tone: string; big: string;
}) {
  return (
    <article className="rec" style={{ "--rc": toneColor(tone), animationDelay: "80ms" } as CSSProperties}>
      <div className="rec-cat"><span className="rec-dot" />{eyebrow}</div>
      <h3 className="rec-title">{big}</h3>
      <div className="sleep-card-sub">{title}</div>
      <p className="rec-detail">{detail}</p>
    </article>
  );
}

export function SleepView({ data }: { data: SleepDetail | null }) {
  if (!data) {
    return <p className="muted">Not enough sleep data yet — a few more synced nights and this fills in.</p>;
  }
  const ln = data.last_night;
  const asleep = ["deep", "rem", "light"].reduce((t, k) => t + (ln.stages[k] ?? 0), 0);
  const total = asleep + (ln.stages.awake ?? 0);

  const durChart = data.nights.map((n) => ({ x: n.day, y: n.duration }));

  return (
    <section className="section rise" style={{ animationDelay: "40ms" }}>
      <div className="sec-head">
        <span className="sec-glyph"><MoonGlyph /></span>
        <h2 className="sec-title">Sleep</h2>
        <span className="sec-count">{data.nights.length} nights</span>
      </div>
      <p className="sec-blurb">Last night in full, how your stage mix compares to what a body needs, and where the week is heading.</p>
      <hr className="sec-rule" />

      {/* last night hero */}
      <div className="sleep-hero">
        <div className="sleep-hero-main">
          <p className="eyebrow" style={{ marginBottom: 8 }}>Last night · {longDay(ln.day)}</p>
          <div className="sleep-dur">
            {fmt(ln.duration)}<span className="u">h asleep</span>
          </div>
          <div className="sleep-hero-stats">
            <SleepStat k="Score" v={ln.score} />
            <SleepStat k="Efficiency" v={ln.efficiency} unit="%" />
            <SleepStat k="Time in bed" v={total ? Math.round(total * 10) / 10 : null} unit="h" />
          </div>
        </div>
        <div className="sleep-hero-stage">
          <div className="stagebar">
            {STAGES.map((s) => {
              const hrs = ln.stages[s.key] ?? 0;
              if (!hrs || !total) return null;
              return <span key={s.key} className="stagebar-seg"
                style={{ flexGrow: hrs, background: s.color }} title={`${s.label} ${fmt(hrs)}h`} />;
            })}
          </div>
          <div className="stagebar-legend">
            {STAGES.map((s) => {
              const hrs = ln.stages[s.key];
              const pct = s.key !== "awake" && asleep ? Math.round(((hrs ?? 0) / asleep) * 100) : null;
              return (
                <span key={s.key} className="lg">
                  <i style={{ background: s.color }} />{s.label}
                  <b>{fmt(hrs)}h{pct != null ? ` · ${pct}%` : ""}</b>
                </span>
              );
            })}
          </div>
        </div>
      </div>

      {/* stage mix vs targets */}
      <div className="eyebrow" style={{ margin: "26px 0 12px" }}>Stage mix vs. targets · 14-night average</div>
      <div className="stage-targets">
        {data.stage_targets.map((s) => {
          const [lo, hi] = STAGE_SCALE[s.key] ?? [0, 100];
          const pos = ((s.pct - lo) / (hi - lo)) * 100;
          const bandLeft = ((s.target_lo - lo) / (hi - lo)) * 100;
          const bandRight = ((s.target_hi - lo) / (hi - lo)) * 100;
          return (
            <div className="stage-target" key={s.key}>
              <div className="stage-target-top">
                <span className="stage-target-label">{s.label}</span>
                <span className="stage-target-pct" style={{ color: toneColor(s.tone) }}>{fmt(s.pct)}%</span>
              </div>
              <ScaleBar
                segments={[{ left: bandLeft, width: Math.max(0, bandRight - bandLeft), tone: "good", solid: true }]}
                markerPos={Math.max(0, Math.min(100, pos))} markerTone={s.tone} height={10}
              />
              <p className="stage-target-note">{s.note}</p>
            </div>
          );
        })}
      </div>

      {/* debt · regularity · trend */}
      <div className="sleep-cards">
        <ToneCard eyebrow="Sleep debt" big={`${fmt(Math.abs(data.debt.hours))}h`}
          title={data.debt.hours > 0 ? `behind over ${data.debt.nights} nights` : `banked over ${data.debt.nights} nights`}
          detail={data.debt.message} tone={data.debt.tone} />
        <ToneCard eyebrow="Consistency" big={`±${fmt(data.regularity.std)}h`}
          title={`spread over ${data.regularity.nights} nights`}
          detail={data.regularity.message} tone={data.regularity.tone} />
        <ToneCard eyebrow="Trend" big={data.trend.direction}
          title={`${data.trend.change > 0 ? "+" : ""}${fmt(data.trend.change)}h over ${data.trend.nights} nights`}
          detail={data.trend.direction === "rising"
            ? "Your nightly sleep has been climbing — keep the momentum."
            : data.trend.direction === "easing"
              ? "Your nights have been getting shorter lately — worth protecting your wind-down."
              : "Your nightly sleep has held steady."} tone={data.trend.tone} />
      </div>

      {/* rolling averages */}
      <div className="eyebrow" style={{ margin: "26px 0 12px" }}>Rolling averages</div>
      <div className="statrow">
        <SleepStat k="Duration · 7d" v={data.averages.duration_7} unit="h" />
        <SleepStat k="Duration · 28d" v={data.averages.duration_28} unit="h" />
        <SleepStat k="Efficiency · 7d" v={data.averages.efficiency_7} unit="%" />
        <SleepStat k="Score · 7d" v={data.averages.score_7} />
      </div>

      {/* nightly duration trend */}
      <div className="chart-title" style={{ marginTop: 26 }}>Nightly sleep · last {data.nights.length}</div>
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={durChart} margin={{ left: -6, right: 8, top: 6 }}>
          <defs>
            <linearGradient id="sleepg" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#2ba7be" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#2ba7be" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(130,130,130,0.14)" vertical={false} />
          <XAxis dataKey="x" stroke="#8b929c" fontSize={11} minTickGap={40}
            tickLine={false} axisLine={false} tickFormatter={shortDay} />
          <YAxis stroke="#8b929c" fontSize={11} width={30} domain={[0, "auto"]}
            tickLine={false} axisLine={false} />
          <Tooltip
            contentStyle={{ background: "var(--panel-solid)", border: "1px solid var(--line-strong)", borderRadius: 10, fontSize: 12, color: "var(--text)" }}
            labelStyle={{ color: "var(--muted)" }} labelFormatter={(d) => longDay(String(d))}
            formatter={(v) => [`${fmt(Number(v))} h`, "Sleep"]} />
          <ReferenceLine y={data.need_hours} stroke="rgba(130,130,130,0.4)" strokeDasharray="5 5"
            label={{ value: `need ${data.need_hours}h`, position: "insideTopRight", fill: "#8b929c", fontSize: 10 }} />
          <Area type="monotone" dataKey="y" stroke="#2ba7be" fill="url(#sleepg)"
            strokeWidth={2} connectNulls dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </section>
  );
}

/* ————— overview teasers (invite you into the full views) ————— */

export function SleepTeaser({ data, onOpen }: { data: SleepDetail | null; onOpen: () => void }) {
  if (!data) return null;
  const ln = data.last_night;
  const total = ["deep", "rem", "light", "awake"].reduce((t, k) => t + (ln.stages[k] ?? 0), 0);
  return (
    <section className="section rise" style={{ animationDelay: "100ms" }}>
      <div className="sec-head">
        <span className="sec-glyph"><MoonGlyph /></span>
        <h2 className="sec-title">Sleep</h2>
        <span className="sec-count">last night</span>
        <button className="btn goals-new" onClick={onOpen}>Deep dive →</button>
      </div>
      <hr className="sec-rule" />
      <div className="teaser-sleep">
        <div className="teaser-sleep-lead">
          <div className="teaser-sleep-val">{fmt(ln.duration)}<span className="u">h</span></div>
          <div className="teaser-sleep-meta">
            <span>Score <b>{fmt(ln.score)}</b></span>
            <span>{fmt(ln.efficiency)}% efficiency</span>
          </div>
        </div>
        <div className="teaser-sleep-right">
          <div className="stagebar">
            {STAGES.map((s) => {
              const h = ln.stages[s.key] ?? 0;
              if (!h || !total) return null;
              return <span key={s.key} className="stagebar-seg" style={{ flexGrow: h, background: s.color }} title={`${s.label} ${fmt(h)}h`} />;
            })}
          </div>
          <p className="teaser-sleep-note" style={{ color: toneColor(data.debt.tone) }}>{data.debt.message}</p>
        </div>
      </div>
    </section>
  );
}

export function StandingTeaser({ data, onOpen }: { data: BenchmarksResponse | null; onOpen: () => void }) {
  if (!data || !data.benchmarks.length) return null;
  const top = data.benchmarks[0]; // biggest opportunity (backend sorts opportunity-first)
  const win = [...data.benchmarks].reverse().find((b) => ["good", "optimal", "healthy"].includes(b.tone) && b.key !== top.key);
  const items = [top, win].filter((b): b is Benchmark => !!b);
  return (
    <section className="section rise" style={{ animationDelay: "120ms" }}>
      <div className="sec-head">
        <span className="sec-glyph"><BarsGlyph /></span>
        <h2 className="sec-title">Where you stand</h2>
        <span className="sec-count">{data.benchmarks.length} measures</span>
        <button className="btn goals-new" onClick={onOpen}>See all →</button>
      </div>
      <hr className="sec-rule" />
      <div className="teaser-standing">
        {items.map((b) => {
          const [lo, hi] = b.scale;
          const targetPos = b.target ? ((b.target.value - lo) / (hi - lo)) * 100 : null;
          return (
            <div className="teaser-bm" key={b.key}>
              <div className="teaser-bm-top">
                <span className="teaser-bm-label">{b.label} · <b>{fmt(b.value)}{b.unit ? ` ${b.unit}` : ""}</b></span>
                <span className="teaser-bm-tier" style={{ color: toneColor(b.tone) }}>{b.tier}</span>
              </div>
              <ScaleBar
                segments={b.bands.map((bd) => ({ left: bd.start, width: Math.max(0, bd.end - bd.start), tone: bd.tone, solid: bd.tone === b.tone }))}
                markerPos={b.position} markerTone={b.tone} targetPos={targetPos} height={10} />
            </div>
          );
        })}
      </div>
    </section>
  );
}
