"use client";

import { KeyboardEvent as ReactKeyboardEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DataTypeInfo, Point } from "@/lib/api";

/* ————————————————————————————————————————————————————————————————
   ⌘K command palette — fuzzy-search every metric (and jump to any view),
   with live values, sparklines, keyboard nav, and match highlighting.
   Self-contained: no recharts, theme-aware via CSS tokens.
   ———————————————————————————————————————————————————————————————— */

// Small local copies of the app's formatters (kept in sync with page.tsx) so this
// component has zero coupling to the page module.
function latestVal(pts?: Point[]): number | null {
  const v = (pts ?? []).filter((p) => p.value != null).map((p) => p.value as number);
  return v.length ? v[v.length - 1] : null;
}
function formatNum(n: number): string {
  if (Math.abs(n) >= 100) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

// Extra search terms so power-user shorthand resolves ("rhr", "hrv", "spo2", "azm"…).
const ALIASES: Record<string, string> = {
  "daily-resting-heart-rate": "rhr resting pulse bpm",
  "daily-heart-rate-variability": "hrv variability",
  "daily-oxygen-saturation": "spo2 o2 oxygen blood ox saturation",
  "daily-respiratory-rate": "breathing respiration breaths brpm",
  "daily-sleep-temperature-derivations": "skin temperature temp thermal",
  "core-body-temperature": "core temp temperature",
  "active-zone-minutes": "azm zone minutes cardio",
  "active-energy-burned": "active calories energy burn kcal",
  "total-calories": "calories energy kcal burn",
  "readiness": "recovery ready recovery-score",
  "daily-vo2-max": "vo2 cardio fitness aerobic",
  "run-vo2-max": "vo2 running aerobic",
  "sedentary-period": "sitting inactive idle",
  "heart-rate": "hr bpm pulse",
  "body-fat": "bodyfat composition fat percent",
  "blood-glucose": "glucose sugar",
  "sleep": "sleep rest bed",
};

// Curated defaults shown (and lightly boosted) so the empty state is useful.
const SUGGEST = ["readiness", "sleep", "steps", "daily-resting-heart-rate", "daily-heart-rate-variability", "active-zone-minutes"];
const SUGGEST_SET = new Set(SUGGEST);

type Cmd = {
  kind: "metric" | "view";
  id: string;
  label: string;
  group: string;
  unit: string;
  hay: string;   // lowercased extra searchable text (name + group + aliases)
  boost: number; // ranking nudge for suggested/pinned items
};

type Row =
  | { type: "header"; label: string; key: string }
  | { type: "item"; cmd: Cmd; ranges: Range[]; key: string };

type Range = [number, number];

// Subsequence fuzzy match with quality scoring. Returns null when not every query
// character is present, else a score (higher = better) plus highlight ranges.
function fuzzy(q: string, text: string): { score: number; ranges: Range[] } | null {
  if (!q) return { score: 0, ranges: [] };
  const t = text.toLowerCase();
  const hits: number[] = [];
  let qi = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (q[qi] === t[ti]) { hits.push(ti); qi++; }
  }
  if (qi < q.length) return null;

  let score = 0;
  let prev = -2;
  for (const pos of hits) {
    score += 100;                                    // base per matched char
    if (pos === prev + 1) score += 70;               // consecutive run
    if (pos === 0) score += 90;                       // matches the very start
    else if (" -/·".includes(t[pos - 1])) score += 55; // word-boundary start
    score -= pos * 0.4;                              // earlier is better
    prev = pos;
  }
  const idx = t.indexOf(q);                          // contiguous substring bonus
  if (idx >= 0) { score += 130; if (idx === 0) score += 130; }
  score -= t.length * 0.15;                          // prefer tighter labels

  const ranges: Range[] = [];
  for (const pos of hits) {
    const last = ranges[ranges.length - 1];
    if (last && pos === last[1]) last[1] = pos + 1;
    else ranges.push([pos, pos + 1]);
  }
  return { score, ranges };
}

function scoreCmd(q: string, c: Cmd): { score: number; ranges: Range[] } | null {
  if (!q) return { score: c.boost, ranges: [] };
  const onLabel = fuzzy(q, c.label);
  if (onLabel) return { score: onLabel.score + c.boost, ranges: onLabel.ranges };
  const onHay = fuzzy(q, c.hay);                     // matched via key/alias → no highlight
  if (onHay) return { score: onHay.score * 0.55 + c.boost, ranges: [] };
  return null;
}

function CmdGlyph({ group }: { group: string }) {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const paths: Record<string, ReactNode> = {
    Recovery: <><circle cx="12" cy="12" r="9" {...p} /><path d="M12 7v5l3 2" {...p} /></>,
    Activity: <path d="M3 13h3l2 5 4-14 2 9h4" {...p} />,
    Workouts: <><path d="M4 9v6M20 9v6M7 7v10M17 7v10" {...p} /><path d="M7 12h10" {...p} /></>,
    Heart: <path d="M12 20s-7-4.5-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.5-7 10-7 10z" {...p} />,
    Vitals: <path d="M3 12h4l2-5 3 10 2-6 2 3h5" {...p} />,
    Body: <><circle cx="12" cy="7" r="3.2" {...p} /><path d="M5.5 20a6.5 6.5 0 0 1 13 0" {...p} /></>,
    Sleep: <path d="M19.5 14.5A8 8 0 0 1 9.5 4.3 8 8 0 1 0 19.5 14.5z" {...p} />,
    "Jump to": <><path d="M5 12h12" {...p} /><path d="M13 6l6 6-6 6" {...p} /></>,
  };
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden className="cmd-glyph">
      {paths[group] ?? <circle cx="12" cy="12" r="8" {...p} />}
    </svg>
  );
}

function MiniSpark({ pts }: { pts?: Point[] }) {
  const vals = (pts ?? []).filter((p) => p.value != null).map((p) => p.value as number).slice(-24);
  if (vals.length < 2) return null;
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const W = 52, H = 18;
  const points = vals.map((v, i) => `${((i / (vals.length - 1)) * W).toFixed(1)},${(H - ((v - min) / span) * H).toFixed(1)}`).join(" ");
  return (
    <svg className="cmd-spark" width={W} height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden>
      <polyline points={points} fill="none" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Highlight({ text, ranges }: { text: string; ranges: Range[] }) {
  if (!ranges.length) return <>{text}</>;
  const out: ReactNode[] = [];
  let i = 0;
  ranges.forEach(([s, e], k) => {
    if (s > i) out.push(text.slice(i, s));
    out.push(<mark key={k}>{text.slice(s, e)}</mark>);
    i = e;
  });
  if (i < text.length) out.push(text.slice(i));
  return <>{out}</>;
}

export function CommandPalette({
  open, onClose, types, dailyCache, tabs, onOpenMetric, onGoView,
}: {
  open: boolean;
  onClose: () => void;
  types: DataTypeInfo[];
  dailyCache: Record<string, Point[]>;
  tabs: { id: string; label: string }[];
  onOpenMetric: (name: string) => void;
  onGoView: (id: string) => void;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [recent, setRecent] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Build the searchable command set once per data load.
  const cmds = useMemo<Cmd[]>(() => {
    const metrics: Cmd[] = types.map((t) => ({
      kind: "metric",
      id: t.name,
      label: t.label,
      group: t.group || "Other",
      unit: t.unit,
      hay: `${t.name} ${t.group} ${t.unit} ${ALIASES[t.name] ?? ""}`.toLowerCase(),
      boost: SUGGEST_SET.has(t.name) ? 40 : 0,
    }));
    const views: Cmd[] = tabs.map((v) => ({
      kind: "view",
      id: v.id,
      label: `Go to ${v.label}`,
      group: "Jump to",
      unit: "",
      hay: `${v.id} ${v.label} view page tab`.toLowerCase(),
      boost: 6,
    }));
    return [...metrics, ...views];
  }, [types, tabs]);

  const byId = useMemo(() => Object.fromEntries(cmds.map((c) => [c.id, c])), [cmds]);

  // Assemble the visible rows: grouped browse when empty, flat ranked list when typing.
  const rows = useMemo<Row[]>(() => {
    const query = q.trim().toLowerCase();
    const out: Row[] = [];

    if (!query) {
      const seen = new Set<string>();
      const pushSection = (label: string, ids: string[]) => {
        const items = ids.map((id) => byId[id]).filter((c): c is Cmd => !!c && !seen.has(c.id));
        if (!items.length) return;
        out.push({ type: "header", label, key: `h-${label}` });
        for (const c of items) { seen.add(c.id); out.push({ type: "item", cmd: c, ranges: [], key: c.id }); }
      };
      pushSection("Recent", recent);
      pushSection("Suggested", SUGGEST);
      // Everything else, in the app's group order.
      const order = ["Recovery", "Activity", "Workouts", "Heart", "Vitals", "Body", "Sleep", "Other"];
      const rest = cmds.filter((c) => c.kind === "metric" && !seen.has(c.id));
      for (const g of order) {
        pushSection(g, rest.filter((c) => c.group === g).map((c) => c.id));
      }
      pushSection("Jump to", cmds.filter((c) => c.kind === "view").map((c) => c.id));
      return out;
    }

    const scored = cmds
      .map((c) => ({ c, m: scoreCmd(query, c) }))
      .filter((x): x is { c: Cmd; m: { score: number; ranges: Range[] } } => x.m !== null)
      .sort((a, b) => b.m.score - a.m.score || a.c.label.localeCompare(b.c.label))
      .slice(0, 40);
    for (const { c, m } of scored) out.push({ type: "item", cmd: c, ranges: m.ranges, key: c.id });
    return out;
  }, [q, cmds, byId, recent]);

  const items = useMemo(() => rows.filter((r): r is Extract<Row, { type: "item" }> => r.type === "item"), [rows]);

  // Reset selection to the top whenever the result set changes.
  useEffect(() => { setSel(0); }, [q]);
  useEffect(() => { if (sel > items.length - 1) setSel(0); }, [items.length, sel]);

  // On open: focus input, clear query, reload recents.
  useEffect(() => {
    if (!open) return;
    setQ("");
    try { setRecent(JSON.parse(localStorage.getItem("cmdk.recent") || "[]")); } catch { setRecent([]); }
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  const run = useCallback((c: Cmd) => {
    if (c.kind === "metric") {
      const next = [c.id, ...recent.filter((x) => x !== c.id)].slice(0, 6);
      try { localStorage.setItem("cmdk.recent", JSON.stringify(next)); } catch { /* private mode */ }
      setRecent(next);
      onOpenMetric(c.id);
    } else {
      onGoView(c.id);
    }
    onClose();
  }, [recent, onOpenMetric, onGoView, onClose]);

  const onKey = useCallback((e: ReactKeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key === "ArrowDown" || (e.key === "n" && e.ctrlKey)) {
      e.preventDefault(); setSel((s) => (items.length ? (s + 1) % items.length : 0)); return;
    }
    if (e.key === "ArrowUp" || (e.key === "p" && e.ctrlKey)) {
      e.preventDefault(); setSel((s) => (items.length ? (s - 1 + items.length) % items.length : 0)); return;
    }
    if (e.key === "Enter") { e.preventDefault(); const it = items[sel]; if (it) run(it.cmd); return; }
    if (e.key === "Home") { e.preventDefault(); setSel(0); return; }
    if (e.key === "End") { e.preventDefault(); setSel(Math.max(0, items.length - 1)); return; }
  }, [items, sel, run, onClose]);

  // Keep the selected row in view as you arrow through.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-idx="${sel}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [sel, open]);

  if (!open) return null;

  let idx = -1; // running index over selectable items, for arrow-key mapping

  return (
    <div className="cmdk-scrim" onMouseDown={onClose} role="presentation">
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="Search metrics"
        onMouseDown={(e) => e.stopPropagation()} onKeyDown={onKey}>
        <div className="cmdk-input-wrap">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth={1.8}
            strokeLinecap="round" aria-hidden className="cmdk-search-icon">
            <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" />
          </svg>
          <input
            ref={inputRef}
            className="cmdk-input"
            placeholder="Search metrics — steps, HRV, sleep, RHR…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            aria-label="Search"
          />
          <kbd className="cmdk-esc">esc</kbd>
        </div>

        <div className="cmdk-list" ref={listRef} role="listbox" aria-label="Results">
          {items.length === 0 && (
            <div className="cmdk-empty">No metric matches “{q.trim()}”.</div>
          )}
          {rows.map((row) => {
            if (row.type === "header") return <div className="cmdk-group" key={row.key}>{row.label}</div>;
            idx += 1;
            const i = idx;
            const c = row.cmd;
            const val = c.kind === "metric" ? latestVal(dailyCache[c.id]) : null;
            const selected = i === sel;
            return (
              <div
                key={row.key}
                data-idx={i}
                role="option"
                aria-selected={selected}
                className={`cmdk-row${selected ? " sel" : ""}`}
                onMouseMove={() => setSel(i)}
                onClick={() => run(c)}
              >
                <span className={`cmdk-ic g-${c.group.replace(/\s+/g, "-")}`}><CmdGlyph group={c.group} /></span>
                <span className="cmdk-label"><Highlight text={c.label} ranges={row.ranges} /></span>
                {q.trim() && c.kind === "metric" && <span className="cmdk-tag">{c.group}</span>}
                {c.kind === "metric" && <MiniSpark pts={dailyCache[c.id]} />}
                {val != null && (
                  <span className="cmdk-val">{formatNum(val)}{c.unit ? <i>{c.unit}</i> : null}</span>
                )}
                {c.kind === "view" && <span className="cmdk-tag">View</span>}
                <span className="cmdk-enter" aria-hidden>↵</span>
              </div>
            );
          })}
        </div>

        <div className="cmdk-foot">
          <span className="cmdk-hints">
            <kbd>↑</kbd><kbd>↓</kbd> navigate&nbsp;&nbsp;<kbd>↵</kbd> open&nbsp;&nbsp;<kbd>esc</kbd> close
          </span>
          <span className="cmdk-count">{items.length} result{items.length === 1 ? "" : "s"}</span>
        </div>
      </div>
    </div>
  );
}
