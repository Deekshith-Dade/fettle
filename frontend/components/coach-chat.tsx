"use client";

/**
 * Coach — the conversational face of fitbit+.
 *
 * A ChatGPT-style two-pane chat over the /api/chat bridge: conversation history in the
 * sidebar (opencode keeps the LLM-side context; we render the stored transcript), live
 * tool-call chips as the agent consults the analysis engine, markdown answers, staged
 * attachments, and a model picker over the account's free opencode Zen models.
 */
import {
  useCallback, useEffect, useRef, useState,
  type ChangeEvent, type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  chatApi, streamChat,
  type ChatAttachment, type ChatBlock, type ChatMessage, type ChatModel,
  type ChatToolCall, type Conversation,
} from "../lib/api";
import { bustWidgetCache, ChatWidget } from "./chat-widgets";

// Tool calls that change stored goals — any goals widget rendered after one of these
// must refetch rather than reuse the 60s cache.
const GOAL_MUTATIONS = new Set(["fitbit_create_goal", "fitbit_update_goal", "fitbit_delete_goal"]);

const SUGGESTIONS: { glyph: string; q: string }[] = [
  { glyph: "◎", q: "How's my recovery today, and what's driving it?" },
  { glyph: "☾", q: "How did I sleep this past week — any patterns I should fix?" },
  { glyph: "↯", q: "Is my training load balanced, or am I overdoing it?" },
  { glyph: "✦", q: "What's the single most useful thing I can do today?" },
];

// ---------- small pieces --------------------------------------------------------

function Md({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function toolMeta(t: ChatToolCall): string {
  const inp = t.input ?? {};
  const bits: string[] = [];
  if (typeof inp.metric === "string") bits.push(inp.metric);
  if (typeof inp.day === "string") bits.push(inp.day);
  if (typeof inp.days === "number") bits.push(`${inp.days}d`);
  if (typeof inp.goal_id === "number") bits.push(`#${inp.goal_id}`);
  if (typeof inp.target === "number")
    bits.push(`${inp.comparator === "lte" ? "≤" : "≥"} ${inp.target}`);
  return bits.join(" · ");
}

function ToolChips({ tools }: { tools: ChatToolCall[] }) {
  if (!tools.length) return null;
  return (
    <div className="tool-row">
      {tools.map((t, i) => {
        const meta = toolMeta(t);
        return (
          <span className="tool-chip" key={`${t.name}-${i}`} title={t.name}>
            <span className="tool-dot" aria-hidden />
            {t.label}
            {meta && <span className="tool-meta">{meta}</span>}
          </span>
        );
      })}
    </div>
  );
}

// Ordered prose + inline widgets. Older messages (pre-widget builds) have no blocks —
// fall back to the joined content string.
function Blocks({ blocks, fallback }: { blocks?: ChatBlock[]; fallback: string }) {
  if (!blocks?.length) return <Md text={fallback} />;
  return (
    <>
      {blocks.map((b, i) =>
        b.type === "text" ? <Md text={b.text} key={i} /> : <ChatWidget spec={b.widget} key={i} />
      )}
    </>
  );
}

function MessageRow({ m }: { m: ChatMessage }) {
  if (m.role === "user") {
    return (
      <div className="msg msg-user">
        {m.parts?.attachments?.length ? (
          <div className="msg-atts">
            {m.parts.attachments.map((a, i) => (
              <span className="att-chip" key={i}>⌁ {a.name}</span>
            ))}
          </div>
        ) : null}
        <div className="user-bubble">{m.content}</div>
      </div>
    );
  }
  return (
    <div className="msg msg-coach">
      <span className="coach-dot" aria-hidden>✦</span>
      <div className="msg-body">
        {m.parts?.tools?.length ? <ToolChips tools={m.parts.tools} /> : null}
        <Blocks blocks={m.parts?.blocks} fallback={m.content} />
      </div>
    </div>
  );
}

function groupFor(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const week = new Date(today);
  week.setDate(week.getDate() - 7);
  if (d >= today) return "Today";
  if (d >= yesterday) return "Yesterday";
  if (d >= week) return "This week";
  return "Earlier";
}

// ---------- sidebar --------------------------------------------------------------

function Sidebar({
  convs, activeId, open, onClose, onNew, onSelect, onRename, onDelete,
}: {
  convs: Conversation[];
  activeId: number | null;
  open: boolean;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: number) => void;
  onRename: (id: number, title: string) => void;
  onDelete: (id: number) => void;
}) {
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<number | null>(null);

  // Group by recency, preserving the updated_at DESC order from the API.
  const groups: { label: string; items: Conversation[] }[] = [];
  for (const c of convs) {
    const label = groupFor(c.updated_at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(c);
    else groups.push({ label, items: [c] });
  }

  return (
    <>
      {open && <div className="coach-scrim" onClick={onClose} aria-hidden />}
      <aside className={`coach-side${open ? " open" : ""}`}>
        <div className="side-head">
          <a className="wordmark side-mark" href="/">
            <span className="dot" />
            <h1>fitbit<em>+</em></h1>
          </a>
          <span className="side-tag">coach</span>
        </div>
        <button className="btn newchat" onClick={onNew}>
          <span aria-hidden>＋</span> New chat
        </button>
        <nav className="conv-groups" aria-label="Conversations">
          {groups.map((g) => (
            <div key={g.label}>
              <p className="conv-glabel">{g.label}</p>
              {g.items.map((c) => (
                <div className={`conv-row${c.id === activeId ? " active" : ""}`} key={c.id}>
                  {editing === c.id ? (
                    <input
                      className="conv-edit"
                      value={draft}
                      autoFocus
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && draft.trim()) {
                          onRename(c.id, draft.trim());
                          setEditing(null);
                        }
                        if (e.key === "Escape") setEditing(null);
                      }}
                      onBlur={() => setEditing(null)}
                    />
                  ) : (
                    <>
                      <button className="conv-title" onClick={() => onSelect(c.id)} title={c.title}>
                        {c.title}
                      </button>
                      <span className="conv-actions">
                        <button
                          className="conv-act" title="Rename" aria-label="Rename conversation"
                          onClick={() => { setEditing(c.id); setDraft(c.title); }}
                        >
                          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                            <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
                          </svg>
                        </button>
                        {confirming === c.id ? (
                          <button
                            className="conv-act sure"
                            onClick={() => { onDelete(c.id); setConfirming(null); }}
                            onBlur={() => setConfirming(null)}
                            autoFocus
                          >
                            sure?
                          </button>
                        ) : (
                          <button
                            className="conv-act" title="Delete" aria-label="Delete conversation"
                            onClick={() => setConfirming(c.id)}
                          >
                            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                            </svg>
                          </button>
                        )}
                      </span>
                    </>
                  )}
                </div>
              ))}
            </div>
          ))}
          {!convs.length && <p className="conv-empty">Your conversations will live here.</p>}
        </nav>
        <a className="side-foot" href="/">← Back to the dashboard</a>
      </aside>
    </>
  );
}

// ---------- the page -------------------------------------------------------------

export default function CoachChat() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [models, setModels] = useState<ChatModel[]>([]);
  const [model, setModel] = useState<string>("");
  const [input, setInput] = useState("");
  const [staged, setStaged] = useState<ChatAttachment[]>([]);
  const [sending, setSending] = useState(false);
  const [live, setLive] = useState<{ tools: ChatToolCall[]; blocks: ChatBlock[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sideOpen, setSideOpen] = useState(false);

  const liveRef = useRef({ tools: [] as ChatToolCall[], blocks: [] as ChatBlock[] });
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const lastSentRef = useRef("");

  const refreshConvs = useCallback(() => {
    chatApi.conversations().then(setConvs).catch(() => {});
  }, []);

  useEffect(() => {
    refreshConvs();
    chatApi
      .models()
      .then((ms) => {
        setModels(ms);
        setModel((cur) => cur || (ms.find((m) => m.recommended) ?? ms[0])?.id || "");
      })
      .catch(() => setError("Backend unreachable — is uvicorn running on :8400?"));
  }, [refreshConvs]);

  // Keep the newest content in view unless the user has scrolled up to read.
  useEffect(() => {
    if (stickRef.current) {
      threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
    }
  }, [messages, live, sending]);

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
  }

  function resetLive() {
    liveRef.current = { tools: [], blocks: [] };
    setLive(null);
  }

  function newChat() {
    stop();
    setActiveId(null);
    setMessages([]);
    resetLive();
    setError(null);
    setSideOpen(false);
    inputRef.current?.focus();
  }

  async function selectConv(id: number) {
    if (id === activeId) { setSideOpen(false); return; }
    stop();
    resetLive();
    setError(null);
    setSideOpen(false);
    setActiveId(id);
    setMessages([]);
    try {
      const conv = await chatApi.conversation(id);
      setMessages(conv.messages);
      if (conv.model && models.some((m) => m.id === conv.model)) setModel(conv.model);
    } catch {
      setError("Couldn't load that conversation.");
    }
  }

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || sending) return;
    lastSentRef.current = message;
    const attachments = staged;
    setInput("");
    setStaged([]);
    if (inputRef.current) inputRef.current.style.height = "auto";
    setError(null);
    stickRef.current = true;
    setMessages((m) => [
      ...m,
      {
        id: -Date.now(), role: "user", content: message,
        parts: attachments.length ? { attachments: attachments.map((a) => ({ name: a.name })) } : null,
        created_at: new Date().toISOString(),
      },
    ]);
    liveRef.current = { tools: [], blocks: [] };
    setLive({ tools: [], blocks: [] });
    setSending(true);

    const push = () =>
      setLive({ tools: [...liveRef.current.tools], blocks: [...liveRef.current.blocks] });
    const ac = new AbortController();
    abortRef.current = ac;
    let failed: string | null = null;

    try {
      await streamChat(
        { message, conversation_id: activeId, model: model || undefined, attachments },
        {
          meta: (m) => { setActiveId((cur) => cur ?? m.conversation_id); refreshConvs(); },
          tool: (t) => {
            if (GOAL_MUTATIONS.has(t.name)) bustWidgetCache("goals");
            liveRef.current.tools.push(t);
            push();
          },
          widget: (w) => { liveRef.current.blocks.push({ type: "widget", widget: w }); push(); },
          text: (t) => {
            const chunk = t.text.trim();
            if (!chunk) return;
            liveRef.current.blocks.push({ type: "text", text: chunk });
            push();
          },
          error: (e) => { failed = e.message; },
        },
        ac.signal
      );
    } catch (e) {
      if (!ac.signal.aborted) failed = String(e);
    }

    // Fold whatever streamed into the transcript — even a stopped turn keeps its partial.
    const { tools, blocks } = liveRef.current;
    if (blocks.length || tools.length) {
      const joined = blocks
        .filter((b): b is Extract<ChatBlock, { type: "text" }> => b.type === "text")
        .map((b) => b.text)
        .join("\n\n");
      setMessages((m) => [
        ...m,
        {
          id: Date.now(), role: "assistant",
          content: joined || (blocks.length ? "" : "*(no answer — try again)*"),
          parts: { tools, blocks }, created_at: new Date().toISOString(),
        },
      ]);
    }
    resetLive();
    setSending(false);
    abortRef.current = null;
    if (failed) setError(failed);
    refreshConvs();
  }

  async function onPickFiles(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    for (const f of files) {
      try {
        const att = await chatApi.upload(f);
        setStaged((s) => [...s, att]);
      } catch {
        setError(`Couldn't upload ${f.name}.`);
      }
    }
  }

  function onKeyDown(e: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const activeTitle = convs.find((c) => c.id === activeId)?.title ?? "New chat";
  const showHello = !messages.length && !sending;

  return (
    <div className="coach">
      <Sidebar
        convs={convs} activeId={activeId} open={sideOpen}
        onClose={() => setSideOpen(false)} onNew={newChat} onSelect={selectConv}
        onRename={async (id, title) => { await chatApi.rename(id, title); refreshConvs(); }}
        onDelete={async (id) => {
          await chatApi.remove(id);
          if (id === activeId) newChat();
          refreshConvs();
        }}
      />

      <main className="coach-main">
        <div className="coach-mobilebar">
          <button className="icon-btn" onClick={() => setSideOpen(true)} aria-label="Conversations">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden>
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="mobile-title">{activeTitle}</span>
          <a className="icon-btn" href="/" aria-label="Back to dashboard">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M3 12 12 3l9 9M5 10v10h5v-6h4v6h5V10" />
            </svg>
          </a>
        </div>

        <div
          className="coach-thread" ref={threadRef}
          onScroll={() => {
            const el = threadRef.current;
            if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
          }}
        >
          <div className="thread-inner">
            {showHello ? (
              <div className="coach-hello">
                <span className="hello-mark" aria-hidden>✦</span>
                <h2>Your data, <em>on the record.</em></h2>
                <p>
                  Ask anything about your training, sleep, and recovery. Every answer is
                  pulled live from your synced metrics — numbers first, never vibes.
                </p>
                <div className="suggest-grid">
                  {SUGGESTIONS.map((s) => (
                    <button className="suggest-card" key={s.q} onClick={() => send(s.q)}>
                      <span className="suggest-glyph" aria-hidden>{s.glyph}</span>
                      {s.q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {messages.map((m) => <MessageRow m={m} key={m.id} />)}
                {live && (
                  <div className="msg msg-coach">
                    <span className="coach-dot" aria-hidden>✦</span>
                    <div className="msg-body">
                      <ToolChips tools={live.tools} />
                      {live.blocks.map((b, i) =>
                        b.type === "text" ? <Md text={b.text} key={i} /> : <ChatWidget spec={b.widget} key={i} />
                      )}
                      {sending && (
                        <span className="thinking" aria-label="Thinking">
                          <i /><i /><i />
                        </span>
                      )}
                    </div>
                  </div>
                )}
                {error && (
                  <div className="coach-error" role="alert">
                    <span>{error}</span>
                    <button onClick={() => { setError(null); send(lastSentRef.current); }}>
                      Try again
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div className="composer">
          {staged.length > 0 && (
            <div className="staged-row">
              {staged.map((a) => (
                <span className="att-chip" key={a.id}>
                  ⌁ {a.name}
                  <button
                    onClick={() => setStaged((s) => s.filter((x) => x.id !== a.id))}
                    aria-label={`Remove ${a.name}`}
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="composer-box">
            <textarea
              ref={inputRef}
              className="composer-input"
              placeholder="Ask your coach…"
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 190)}px`;
              }}
              onKeyDown={onKeyDown}
            />
            <div className="composer-row">
              <button
                className="icon-btn" title="Attach a file" aria-label="Attach a file"
                onClick={() => fileRef.current?.click()} disabled={sending}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>
              <input type="file" ref={fileRef} onChange={onPickFiles} multiple hidden />
              <select
                className="model-pick" value={model} title="Model (all free)"
                onChange={(e) => setModel(e.target.value)} disabled={sending}
                aria-label="Model"
              >
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}{m.recommended ? " ✓" : ""}
                  </option>
                ))}
              </select>
              <span className="composer-space" />
              {sending ? (
                <button className="send-btn stop" onClick={stop} title="Stop" aria-label="Stop">
                  <span className="stop-square" aria-hidden />
                </button>
              ) : (
                <button
                  className="send-btn" onClick={() => send()} title="Send" aria-label="Send"
                  disabled={!input.trim()}
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M12 19V5M5 12l7-7 7 7" />
                  </svg>
                </button>
              )}
            </div>
          </div>
          <p className="coach-foot">
            Heuristic coaching over your own data — not medical advice.
          </p>
        </div>
      </main>
    </div>
  );
}
