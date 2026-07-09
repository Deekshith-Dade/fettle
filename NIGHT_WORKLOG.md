# 🌙 Night Build Worklog — Health Insights & UI Redesign

**Session start:** 2026-07-09 00:13 MDT · **Target end:** ~03:00 · **Branch:** `feature/health-insights`

Working autonomously while you sleep. This file is my running narrative + durable plan
(so nothing is lost if context rolls over). Read it top-to-bottom in the morning for the story.

---

## The mission (your words, distilled)
Go deep on the science, get inspired by Whoop / Oura / Bevel / Apple Health, and use *my
actual data* to help me understand myself and grow — starting from "perfect information."
And make the UI something I **flow** through and feel attached to, without being overwhelmed.

## Your three taste calls
- **Vibe:** Calm, deep-on-tap — minimal surface, depth revealed on demand (Bevel × Whoop).
- **Theme:** Auto (system) + manual toggle.
- **Build all four insight systems**, leading with **Readiness** as the hero: Readiness,
  Sleep deep-dive, Trends & correlations, Peer benchmarks.

## Key discovery (from README)
The backend **already** computes derived metrics — so I enrich, not reinvent:
- `readiness.py` — 0–100 recovery index (HRV, resting HR, sleep, training load, skin-temp vs 28-day baseline)
- Sleep Score — duration(50) + deep/REM(25) + efficiency(25)
- Cardio Load — TRIMP-style (1·fat-burn + 2·cardio + 3·peak zone minutes)
- Sleep stages/duration/efficiency + workout metrics

## Plan for the night
**Backend (enrich):** verify existing derived metrics against the real DB, then add an
`/insights/*` layer: readiness summary + components + baseline context, sleep deep-dive
(stages, regularity, sleep debt, ideal bedtime), trends (rolling means, WoW deltas),
correlations (steps→resting HR, activity→deep sleep, etc.), benchmarks vs mid-20s-male norms.

**Frontend (the big lift):** a theme system (CSS variables, light/dark + system + toggle),
a redesigned **Home** with a single Readiness hero and calm summary cards, then drill-down
detail for Sleep / Trends / Benchmarks — progressive disclosure so depth appears "on tap."
Charts + subtle motion for flow. Keep everything currently working intact.

**Method:** run the app locally, open it in a real browser, screenshot, critique, iterate —
so the UI is genuinely good, not just plausible.

---

## Progress log
- **01:25** — ✅ **Slices A + B shipped** (benchmarks + sleep), verified end-to-end.
  Backend: `benchmarks.py` (`/api/benchmarks`) + `sleep_analysis.py` (`/api/sleep/detail`),
  each grounded in the research spec (Tudor-Locke, WHO, NSF, Voss HRV, Van Dongen 14-day debt).
  Frontend: `components/insights-views.tsx` — `BenchmarksView` (reusable banded `ScaleBar` with
  marker + target tick) and `SleepView` (last-night hero, stage-mix-vs-targets, debt/consistency/
  trend cards, nightly chart). Wired into nav as **Sleep** and **Standing** tabs. `tsc --noEmit`
  clean, Next compiles clean, endpoints return real data. Real insights surfaced: RHR "average"
  (→ target ≤66), steps "low active" (→7.5k), sleep REM a touch low / light high, ~24h 14-night
  sleep debt but trend rising. The full cited research spec is saved to `docs/health-metrics-spec.md`.
  Next: **(C) light/dark theme + toggle**, then flow polish. Committing this as a checkpoint.
- **00:13** — Branched `feature/health-insights`. Launched 3 background agents: frontend map,
  backend+data map (real DB counts/ranges), and an evidence-based health-metrics research spec
  (Whoop/Oura formulas, HRV/sleep science, mid-20s-male benchmark reference values w/ sources).
  Read README. Waiting on maps + research before writing code, so I build on facts not guesses.
- **00:26** — Maps back. App is more mature than expected (existing readiness/insights/coach/goals;
  polished dark "instrument panel" UI, single `page.tsx` + `globals.css`, recharts). Data rich:
  42–91 days across activity/heart/HRV/sleep/vitals. Reframed the night as **elevation, not rebuild**.
- **00:29** — Servers up (backend `[::]:8400`, Next `3400`). **Constraint found: the Chrome
  extension isn't connected**, so no live screenshot QA tonight. Mitigation: verify via the Next
  compile log + `curl` on every endpoint/page, reuse the existing CSS language for all new surfaces,
  and keep **dark as default** so the current app is never at risk. Morning to-do for you: just
  eyeball light mode and the new views. Building order (each a shippable, committed slice):
  **(A) Peer Benchmarks → (B) Sleep deep-dive → (C) Light/dark theme + toggle → (D) Trends explorer + flow.**
