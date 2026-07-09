# Personal Health Dashboard — Evidence-Based Insight Spec

> Compiled overnight as the evidence base for fitbit+'s insight layer. Every threshold,
> formula, and reference value the app uses traces back to a citation here. Read it when you
> want to know *why* a number is where it is — and where the honest uncertainty lives.
>
> **Two caveats that run through everything:**
> 1. Vendor score weightings (WHOOP/Oura/etc.) are almost never published — input lists and
>    scales are official, exact weights are third-party estimates.
> 2. Daily scores normalize to *your own rolling baseline*, not population norms. The peer
>    benchmarks in §5 are context for the "where you stand" view only — never for daily scoring.

**Scope:** buildable formulas, numeric thresholds, and reference values for four insight systems
(Readiness, HRV/RHR, Sleep, Trends & Correlations) plus peer benchmarks, tuned for a 25-year-old,
72 kg male on Fitbit/Google Health data.

---

## Section 0 — Cross-cutting primitives

**0.1 Rolling personal baseline (per metric).** Trailing window of daily values. Two windows: a
long **baseline** (60 days, min 14 to activate) for "your normal," and a short **acute** value
(last night, or a 3–7-day average for noisy metrics).

```
μ_i  = mean(last 60 daily values)      # your normal
σ_i  = sample SD(last 60 daily values) # your day-to-day spread
```

**Robust variant (recommended — resists sick days / travel):** median + MAD:
```
center_i = median(window)
scale_i  = 1.4826 × median(|x − center_i|)   # MAD → SD-equivalent
```
Floor `scale_i` at the population SD when n < 14 or scale ≈ 0.

**0.2 Directional z-score** (positive = better):
```
z_i = sign_i × (acute_i − μ_i) / σ_i , clipped to [−3, +3]
sign = +1 higher-better (HRV, sleep, steps) · −1 lower-better (RHR, RR, strain, temp)
```

**0.3 Smallest Worthwhile Change (SWC).** Below this, a change is noise. **0.5×SD for HRV/RHR**
([Plews 2013](https://pubmed.ncbi.nlm.nih.gov/23852425/)); **0.2–0.3×SD for generic deltas**
([Science for Sport](https://www.scienceforsport.com/smallest-worthwhile-change/)). `|z| < 0.5` → gray it out.

**0.4 Missing-input renormalization.** Drop a missing metric and renormalize remaining weights to
sum to 1 — this makes graceful degradation automatic. `Score = Σ(w_i·s_i)/Σ(w_i)` over available i.

---

## Section 1 — Readiness / Recovery

**How the commercial scores work** (all use personal baselines + z-scores; HRV-centric):

| System | Inputs | Baseline | Bands |
|---|---|---|---|
| WHOOP Recovery | HRV (rMSSD, SWS-weighted), RHR, RR, sleep, temp, SpO₂ | ~30 d | Green 67+/Yellow 34–66/Red 0–33 |
| Oura Readiness | RHR, HRV Balance, temp, recovery index, sleep, activity | 14 d vs ~90 d | 85+ Optimal/70–84 Good/<70 |
| Polar Nightly Recharge | HR > HRV > breathing (only vendor to publish order) | 28 d | −10…+10 |
| Apple Vitals | overnight HR/RR/temp/SpO₂/sleep — per-metric outlier, alert at ≥2 out | ~7 nights | no composite |

**z → sub-score map:** `s_i = clamp(60 + 13·z_i, 5, 100)`. Intercept 60 = an average day reads
solidly recovered; slope 13 aligns with WHOOP's bands (`s≥67`≈z≥+0.5, `s<34`≈z≤−2).

**Full formula (HRV available):** weights HRV 0.40 / RHR 0.25 / Sleep 0.20 / prior-day strain 0.10
/ RR 0.05. `Readiness = Σ(w·s)/Σ(w)`.

**Graceful degradation (no HRV):** RHR 0.55 / Sleep 0.35 / Strain 0.10, + a "reduced confidence" badge.

**Overrides:**
- **Illness/strain flag (Apple-style):** if ≥2 of {RHR, RR, temp elevated; HRV suppressed} are beyond
  +2 SD in the bad direction → cap Readiness at 40, "prioritize rest." Wearable RHR/RR rose up to
  ~10 days before COVID symptoms in 81% of cases ([Mishra 2020](https://www.nature.com/articles/s41551-020-00640-6)).
- **Parasympathetic-saturation guard:** if HRV low (z≤−1) *while* RHR also low (z≥+1), the low HRV is
  likely vagal saturation, not fatigue — don't penalize it ([Altini](https://marcoaltini.substack.com/p/parasympathetic-saturation)).

**Bands:** ≥85 Primed · 67–84 Ready · 50–66 Moderate · 34–49 Low · <34 Compromised. Always show the
7-day trend and which sub-score dragged it down.

> fitbit+'s existing `readiness.py` already does personal-baseline, HRV-weighted scoring. The
> illness flag, saturation guard, and respiratory-rate input above are the highest-value *future*
> upgrades.

---

## Section 2 — HRV & Resting HR

**Metrics:** rMSSD (ms) = short-term parasympathetic/vagal marker (what every wearable uses); SDNN =
total variability.

**Normal ranges, men 25–34** ([Voss 2015, n=1,906](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0118308), 5-min supine ECG):
| Metric | Mean ± SD | Normal band |
|---|---|---|
| rMSSD | 39.7 ± 19.9 ms | ~20–60 ms |
| SDNN | 50.0 ± 20.9 ms | ~29–71 ms |

**Two traps:** (1) the SD is huge — personal baseline >> population number. (2) **Overnight wearable
rMSSD reads higher than seated-lab values** — never compare across protocols.

**Smoothing daily HRV** (within-person CV ≈ 30–37% — a single day is unreliable):
1. `lnRMSSD = ln(rMSSD)` (raw is right-skewed).
2. Baseline = 7-day rolling mean of lnRMSSD ([Plews/Buchheit](https://pubmed.ncbi.nlm.nih.gov/23852425/)).
3. Normal band = 30–60 d mean ± SD; flag only when a day exits it (or beyond 0.5×SD).
4. Track 7-day CV as a second-order stability signal — a *collapsing* CV is an early overreaching marker.

**Resting HR (25-yo male):** general 60–100; a non-athlete in **55–70 is normal**, **<60 = good
fitness**, **>80 at rest = flag**. Fitbit measures RHR from resting (incl. daytime) periods — noisier
and higher than overnight-only devices. Morning RHR **+5–10 bpm** over baseline = under-recovery/illness.

**Combined signature:** falling HRV + rising RHR together = strain/illness (HRV leads, RHR confirms).

---

## Section 3 — Sleep

**Stage proportions of total sleep** (healthy young adult, [StatPearls](https://www.ncbi.nlm.nih.gov/books/NBK526132/)):
Light (N1+N2) **50–60%** · Deep (N3) **13–23%** (~60–110 min) · REM **20–25%** (~90–120 min). Deep is
front-loaded, REM back-loaded. Deep-% is the least-standardized metric — weight *trend vs. baseline*
over hitting an absolute.

**Duration / efficiency / latency** (25-yo): duration **7–9 h**, floor ≥7 ([NSF 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/));
efficiency **≥85% good, 90–95% optimal**; latency **10–20 min ideal**, flag <5 (deprived) or >30 (onset problem).

**Sleep Regularity Index** ([Phillips 2017](https://www.nature.com/articles/s41598-017-03171-4)): % probability of
same sleep/wake state 24 h apart. **UK Biobank: most- vs least-regular quintile had 20–48% lower
mortality — regularity beat duration** ([Windred 2024](https://academic.oup.com/sleep/article/47/1/zsad253/7280269)).
Cheap proxies without per-minute hypnograms: SD of midsleep time, or social jetlag (target <1 h).
*(fitbit+ uses nightly-duration spread as its available proxy.)*

**Sleep debt:** `Σ max(0, need − actual)` over a **rolling 14 days** ([Van Dongen 2003](https://www.med.upenn.edu/uep/assets/user-content/documents/Van_Dongen_Dinges_Sleep_26_3_2003.pdf)).
**Recovery is asymmetric — one long night doesn't clear it.**

**Buildable Sleep Score (0–100):** Duration vs need 0.35 · Efficiency 0.20 · Restorative (deep+REM) 0.20
· Latency 0.10 · Regularity 0.15.

---

## Section 4 — Trends & Correlations (honest stats for n-of-1)

- **Smoothing:** SMA-7 for baseline lines; EWMA (7-day acute / 28-day chronic) when reactivity matters.
- **Is a delta real?** Gate on SWC (0.2–0.3×SD; 0.5×SD for HRV/RHR) *and* measurement noise.
- **Correlations — default to Spearman** (wearable data is skewed) ([Bishara & Hittner 2012](https://bpb-us-w2.wpmucdn.com/blogs.cofc.edu/dist/7/881/files/2021/06/Bishara-Hittner-2012.pdf)).
  Correlations stabilize around **n≈250**; **hide r until ≥21–30 pairs, label <60 "preliminary."**
  Always show a CI (Fisher z), correct for autocorrelation (`n_eff = n·(1−r₁)/(1+r₁)`) and multiple
  comparisons (Benjamini–Hochberg). **Empirical effect sizes: |r| 0.1/0.2/0.3 = small/medium/large** —
  in personal data r≈0.3 is already strong ([Gignac 2016](https://www.sciencedirect.com/science/article/abs/pii/S0191886916308194)).
- **Lagged effects:** shift a series ±3 days, recompute — but require a plausible mechanism, don't grab the max.

**Established relationships — surface these first:**
| Relationship | Effect | Lag | Source |
|---|---|---|---|
| Alcohol → HRV↓/RHR↑ | HRV −10.8 ms, RHR +8% (Oura) | same night | [Oura](https://ouraring.com/blog/how-does-alcohol-impact-oura-members/) |
| Late/intense exercise (<4 h pre-bed) → HRV↓ | disruption in last ~4 h; ≥4 h before = none | same night | [Leota 2025](https://www.nature.com/articles/s41467-025-58271-x) |
| Caffeine <6 h pre-bed → sleep↓ | 400 mg 6 h before cut TST >1 h | same night | [Drake 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3805807/) |
| High strain → next-morning HRV↓ | 24–72 h recovery | +1 day | [PMC11541970](https://pmc.ncbi.nlm.nih.gov/articles/PMC11541970/) |
| Regular exercise → RHR↓ | −3.3 bpm (men −7.1) over weeks | chronic | [Reimers 2018](https://pmc.ncbi.nlm.nih.gov/articles/PMC6306777/) |
| Exercise → deep sleep↑ | small beneficial | same night | [Kredlow 2015](https://pubmed.ncbi.nlm.nih.gov/25596964/) |
| **Single night's sleep → next-day HRV** | **WEAK: <1% variance (r≈−0.05)** | +1 | [Terra](https://tryterra.co/research/think-a-good-hrv-score-follows-a-good-night-sleep-think-again) |

That last row is the honesty exemplar: good sleep supports recovery on average, but *last night's*
sleep doesn't reliably predict *today's* HRV. Always say "associated with," never "causes."

---

## Section 5 — Peer benchmarks (25-yo male): typical / good / optimal

| Metric | Typical | Good | Optimal | Source |
|---|---|---|---|---|
| Resting HR (bpm) | ~64 (wearable mean) | <65 | <60 | [Quer 2020, n=92k](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0227709) |
| HRV rMSSD (5-min, ms) | 30–50 | 50–60 | >60 | [Voss 2015](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0118308) |
| Daily steps | 5,000–7,000 | 7,000–8,000 | 8,000–10,000 | [Paluch 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9289978/) |
| VO₂max (ml/kg/min) | 42–48 | 52–56 | >60 | [ACSM percentiles](https://www.topendsports.com/testing/norms/vo2max.htm) |
| Sleep duration | 7 h | 7.5–8 h | 8–9 h | [NSF 2015](https://pmc.ncbi.nlm.nih.gov/articles/PMC4434546/) |
| Sleep efficiency | 85–90% | 90%+ | 90–95% | [Ohayon 2017](https://pubmed.ncbi.nlm.nih.gov/28346153/) |
| Active min/week | 150 mod | ~225 mod | 300 mod + 2 strength days | [WHO 2020](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7719906/) |
| BMI | 18.5–24.9 | 20–23 | ~22 + low body-fat | [WHO/CDC](https://www.cdc.gov/mmwr/volumes/65/wr/mm6506a1.htm) |

**Guards against false "you vs norm" flags:**
- Use **RHR = 64** as the wearable norm line (not the clinical 70–73). Fitbit reads a bit high → a
  touch of leniency is correct.
- **HRV: compare like-for-like units only.** The overnight device value reads higher than the 5-min
  lab norm — the personal baseline is the honest comparator.
- **Steps optimal is 8,000–10,000** for this age (not folklore 10k); the steepest benefit is escaping
  the low end.
- **VO₂max peaks now** (age 25 = best benchmarking window).
- **BMI ignores muscle mass** — show that caveat so a lean-muscular reading isn't mislabeled.

---

*Full source list and worked examples live in the original research thread; this is the distilled,
buildable version. `benchmarks.py` and `sleep_analysis.py` implement §5 and §3; `readiness.py`
implements a version of §1 already.*
