"use client";

/**
 * First-run setup — the opening page of the ledger.
 *
 * A numbered editorial walkthrough that takes a brand-new user from "empty Google
 * Cloud account" to "first sync done". Steps 01–03 happen in Google's console (we
 * can't observe them; the pasted client JSON in 04 is their proof), 04–06 are
 * verified live against /api/setup/status, which this component polls while open.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, SetupStatus, SyncReport } from "@/lib/api";

const CONSOLE = {
  createProject: "https://console.cloud.google.com/projectcreate",
  enableApi: "https://console.cloud.google.com/apis/library/health.googleapis.com",
  audience: "https://console.cloud.google.com/auth/audience",
  scopes: "https://console.cloud.google.com/auth/scopes",
  clients: "https://console.cloud.google.com/auth/clients",
};

/* Rotating lines for the first-sync wait (~1–3 min for the 90-day backfill). */
const SYNC_PHASES = [
  "negotiating with Google…",
  "pulling 90 days of daily metrics…",
  "heart-rate detail, beat by beat…",
  "sleep sessions, stage by stage…",
  "workouts and active minutes…",
  "deriving readiness and sleep scores…",
  "writing the ledger…",
];

function ExtLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a className="linkchip" href={href} target="_blank" rel="noreferrer">
      {children}
      <svg className="ext" viewBox="0 0 24 24" width="11" height="11" fill="none"
        stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M7 17L17 7M9 7h8v8" />
      </svg>
    </a>
  );
}

function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn btn-mini"
      onClick={() => {
        navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        });
      }}
    >
      {copied ? "copied ✓" : label ?? "copy"}
    </button>
  );
}

function CopyField({ value }: { value: string }) {
  return (
    <div className="copyfield">
      <span className="copyfield-val">{value}</span>
      <CopyButton value={value} />
    </div>
  );
}

function Check() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
      strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4.5 12.5l5 5 10-11" />
    </svg>
  );
}

type StepState = "done" | "active" | "ahead";

const STEP = { project: 0, consent: 1, client: 2, key: 3, connect: 4, sync: 5 } as const;

function Badge({ state, doneLabel }: { state: StepState; doneLabel?: string }) {
  if (state === "done")
    return <span className="step-badge done"><Check /> {doneLabel ?? "done"}</span>;
  if (state === "active") return <span className="step-badge active">you're here</span>;
  return <span className="step-badge">ahead</span>;
}

export function SetupWizard({ initial }: { initial: SetupStatus }) {
  const [status, setStatus] = useState<SetupStatus>(initial);
  const [pasted, setPasted] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [replacing, setReplacing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncPhase, setSyncPhase] = useState(0);
  const [report, setReport] = useState<SyncReport | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    api.setupStatus().then(setStatus).catch(() => { /* backend blip — next tick */ });
  }, []);

  // Live checklist: console work and the OAuth round-trip complete outside this tab,
  // so poll until everything the wizard tracks is green.
  useEffect(() => {
    if (status.authenticated && status.has_data) return;
    const t = setInterval(refresh, 3500);
    return () => clearInterval(t);
  }, [status.authenticated, status.has_data, refresh]);

  const credsOk = !!(status.credentials.present && status.credentials.valid);
  const done = {
    console: credsOk, // holding a client JSON is the proof the console work happened
    key: credsOk,
    connect: status.authenticated,
    sync: status.has_data,
  };
  // First thing still open, for the "you're here" marker.
  const activeIdx = !credsOk ? 0 : !status.authenticated ? STEP.connect : STEP.sync;

  const stateFor = (idx: number, isDone: boolean): StepState =>
    isDone ? "done" : idx === activeIdx ? "active" : "ahead";
  const st = {
    project: stateFor(STEP.project, done.console),
    consent: stateFor(STEP.consent, done.console),
    client: stateFor(STEP.client, done.console),
    key: stateFor(STEP.key, done.key),
    connect: stateFor(STEP.connect, done.connect),
    sync: stateFor(STEP.sync, done.sync),
  };

  async function submitCredentials(text: string) {
    setSaving(true);
    setSaveError(null);
    try {
      const res = await api.saveCredentials(text);
      setWarnings(res.warnings);
      setPasted("");
      setReplacing(false);
      refresh();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function readFile(f: File | undefined | null) {
    if (!f) return;
    submitCredentials(await f.text());
  }

  async function runFirstSync() {
    setSyncing(true);
    setSyncError(null);
    setSyncPhase(0);
    const ticker = setInterval(
      () => setSyncPhase((p) => Math.min(p + 1, SYNC_PHASES.length - 1)),
      9000
    );
    try {
      const res = await api.triggerSync();
      if (res.detail) setSyncError(res.detail);
      else {
        setReport(res);
        const failed = res.results?.filter((r) => r.error) ?? [];
        if (failed.length) setSyncError(`${failed.length} stream(s) had errors — first: ${failed[0].error}`);
      }
      refresh();
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : String(e));
    } finally {
      clearInterval(ticker);
      setSyncing(false);
    }
  }

  const scopeShort = status.scopes.map((s) =>
    s.replace("https://www.googleapis.com/auth/googlehealth.", "").replace(".readonly", "")
  );

  return (
    <section className="setup">
      <header className="setup-head rise" style={{ animationDelay: "40ms" }}>
        <p className="eyebrow">first run · one-time setup</p>
        <h2 className="setup-title">
          Let&rsquo;s get you in fine <em>fettle</em>.
        </h2>
        <p className="setup-sub">
          Everything runs on this machine — your data syncs from Google straight into a
          local file, and no one else&rsquo;s server is involved. That&rsquo;s also why
          Google asks you to hold your own keys: one free Cloud project, about ten
          minutes, once.
        </p>
        <ul className="setup-facts">
          <li>runs entirely on this machine</li>
          <li>your own Google project — your keys</li>
          <li>free · no review · ten minutes</li>
        </ul>
      </header>

      <ol className="ledger rise" style={{ animationDelay: "120ms" }}>
        {/* ——— 01 · project ——— */}
        <li className={`step ${st.project}`}>
          <span className="step-num">01</span>
          <div className="step-main">
            <div className="step-row">
              <h3 className="step-title">A project of your own</h3>
              <Badge state={st.project} />
            </div>
            {!done.console && (
              <div className="step-body">
                <p className="step-prose">
                  Create a free Google Cloud project — any name, no billing — then enable
                  the <strong>Google Health API</strong> inside it.
                </p>
                <div className="step-links">
                  <ExtLink href={CONSOLE.createProject}>Create a project</ExtLink>
                  <ExtLink href={CONSOLE.enableApi}>Enable the Health API</ExtLink>
                </div>
              </div>
            )}
          </div>
        </li>

        {/* ——— 02 · consent screen ——— */}
        <li className={`step ${st.consent}`}>
          <span className="step-num">02</span>
          <div className="step-main">
            <div className="step-row">
              <h3 className="step-title">Take the personal-use lane</h3>
              <Badge state={st.consent} />
            </div>
            {!done.console && (
              <div className="step-body">
                <p className="step-prose">
                  Under <strong>APIs &amp; Services → OAuth consent screen</strong> (Google
                  asks for an app name first — <em>fettle</em> works), choose user type{" "}
                  <strong>External</strong> and leave the app in <strong>Testing</strong>.
                  That&rsquo;s Google&rsquo;s lane for personal apps: no review, no fees.
                  Then add <strong>your own Gmail</strong> as a test user, and under{" "}
                  <strong>Data access</strong>, add the four Google Health scopes.
                </p>
                <div className="step-links">
                  <ExtLink href={CONSOLE.audience}>Audience &amp; test users</ExtLink>
                  <ExtLink href={CONSOLE.scopes}>Data access</ExtLink>
                </div>
                <div className="scopebox">
                  <div className="scopebox-row">
                    <span className="scopebox-label">the four scopes</span>
                    <CopyButton value={status.scopes.join("\n")} label="copy all four" />
                  </div>
                  <ul className="scopelist">
                    {scopeShort.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </div>
              </div>
            )}
          </div>
        </li>

        {/* ——— 03 · oauth client ——— */}
        <li className={`step ${st.client}`}>
          <span className="step-num">03</span>
          <div className="step-main">
            <div className="step-row">
              <h3 className="step-title">Mint the key</h3>
              <Badge state={st.client} />
            </div>
            {!done.console && (
              <div className="step-body">
                <p className="step-prose">
                  Create an <strong>OAuth client ID</strong>, application type{" "}
                  <strong>Web application</strong>, with exactly this authorized redirect
                  URI — then <strong>download the JSON</strong> it offers.
                </p>
                <CopyField value={status.redirect_uri} />
                <div className="step-links">
                  <ExtLink href={CONSOLE.clients}>OAuth clients</ExtLink>
                </div>
              </div>
            )}
          </div>
        </li>

        {/* ——— 04 · hand over the key ——— */}
        <li className={`step ${st.key}`}>
          <span className="step-num">04</span>
          <div className="step-main">
            <div className="step-row">
              <h3 className="step-title">Hand fettle the key</h3>
              <Badge
                state={st.key}
                doneLabel={
                  status.credentials.client_id_hint
                    ? `${status.credentials.client_type} client · ${status.credentials.client_id_hint}`
                    : "done"
                }
              />
            </div>
            {done.key && !replacing ? (
              <div className="step-body">
                {warnings.map((w) => (
                  <div className="callout warn" key={w}>{w}</div>
                ))}
                <button className="btn btn-mini ghost" onClick={() => setReplacing(true)}>
                  replace the key
                </button>
              </div>
            ) : (
              <div className="step-body">
                <p className="step-prose">
                  Paste the downloaded <code>client_secret_….json</code> here — or drop the
                  file straight in. It stays on disk as{" "}
                  <code>backend/credentials.json</code>, gitignored.
                </p>
                <textarea
                  className={`pastezone${dragging ? " drag" : ""}`}
                  value={pasted}
                  placeholder={'{ "web": { "client_id": … } }'}
                  spellCheck={false}
                  onChange={(e) => setPasted(e.target.value)}
                  onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragging(false);
                    readFile(e.dataTransfer.files?.[0]);
                  }}
                />
                <div className="step-links">
                  <button
                    className="btn btn-lime"
                    disabled={saving || !pasted.trim()}
                    onClick={() => submitCredentials(pasted)}
                  >
                    {saving && <span className="spinner" aria-hidden />}
                    {saving ? "Checking" : "Save the key"}
                  </button>
                  <button className="btn" onClick={() => fileInput.current?.click()}>
                    …or pick the file
                  </button>
                  <input
                    ref={fileInput} type="file" accept=".json,application/json" hidden
                    onChange={(e) => readFile(e.target.files?.[0])}
                  />
                  {replacing && (
                    <button className="btn btn-mini ghost" onClick={() => setReplacing(false)}>
                      keep the current key
                    </button>
                  )}
                </div>
                {saveError && <div className="callout bad">{saveError}</div>}
              </div>
            )}
          </div>
        </li>

        {/* ——— 05 · connect ——— */}
        <li className={`step ${st.connect}`}>
          <span className="step-num">05</span>
          <div className="step-main">
            <div className="step-row">
              <h3 className="step-title">Shake hands with Google</h3>
              <Badge state={st.connect} doneLabel="connected" />
            </div>
            {!done.connect && (
              <div className="step-body">
                <p className="step-prose">
                  One consent screen. Google will note the app is unverified —{" "}
                  <em>that&rsquo;s the Testing lane; the app is yours</em> — and list the
                  scopes as checkboxes. <strong>Tick every box:</strong> each unticked
                  scope is a blind spot on the dashboard.
                </p>
                <div className="step-links">
                  <button
                    className="btn btn-lime"
                    disabled={!credsOk}
                    onClick={() => { window.location.href = api.loginUrl(); }}
                  >
                    Connect Google
                  </button>
                  {!credsOk && <span className="step-hint">needs the key from step 04 first</span>}
                </div>
              </div>
            )}
          </div>
        </li>

        {/* ——— 06 · first sync ——— */}
        {/* While the sync runs, rows are already landing — the polled has_data flips
            true within seconds. Gate the "done" face on !syncing so the user can't
            enter a 5%-synced dashboard. */}
        <li className={`step ${syncing ? "active" : st.sync}`}>
          <span className="step-num">06</span>
          <div className="step-main">
            <div className="step-row">
              <h3 className="step-title">First light</h3>
              <Badge state={syncing ? "active" : st.sync} doneLabel="ledger open" />
            </div>
            <div className="step-body">
              {!done.sync && !syncing && (
                <>
                  <p className="step-prose">
                    Pull your first 90 days — dailies in full, heart-rate detail for recent
                    days. Takes a minute or three.
                  </p>
                  <div className="step-links">
                    <button className="btn btn-lime" disabled={!status.authenticated} onClick={runFirstSync}>
                      Run the first sync
                    </button>
                    {!status.authenticated && <span className="step-hint">connect first</span>}
                  </div>
                </>
              )}
              {syncing && (
                <div className="syncstage">
                  <span className="syncring" aria-hidden />
                  <span className="syncline">{SYNC_PHASES[syncPhase]}</span>
                </div>
              )}
              {syncError && !syncing && <div className="callout warn">{syncError}</div>}
              {done.sync && !syncing && (
                <>
                  {report?.total_rows != null && (
                    <p className="step-prose">
                      <strong>{report.total_rows.toLocaleString()}</strong> rows across{" "}
                      {report.results?.filter((r) => !r.error).length ?? "your"} streams.
                      You&rsquo;re in fine fettle.
                    </p>
                  )}
                  <div className="step-links">
                    <button className="btn btn-lime" onClick={() => window.location.replace("/")}>
                      Enter fettle →
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </li>
      </ol>

      <footer className="setup-foot rise" style={{ animationDelay: "200ms" }}>
        <p>
          The one recurring chore: Google&rsquo;s Testing lane expires tokens every{" "}
          <strong>7 days</strong>. fettle counts down in the top bar and warns you the day
          before; reconnecting is the same one-click handshake.
        </p>
        <p>
          Moving machines? Bring <code>backend/credentials.json</code> and{" "}
          <code>backend/token.json</code> with you and skip straight to sync.
        </p>
      </footer>
    </section>
  );
}
