import { useEffect, useState } from "react";
import DrawingViewer from "./DrawingViewer.jsx";
import Viewer3D from "./Viewer3D.jsx";

const THEME_KEY = "meridian-theme";

function initialTheme() {
  if (typeof document !== "undefined" && document.documentElement.dataset.theme) {
    return document.documentElement.dataset.theme;
  }
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") {
      return stored;
    }
  } catch {
    /* ignore */
  }
  if (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

const STARTERS = [
  "Tell me about the build plate",
  "What does the Front Door interface with?",
  "Which BOM rows are stainless steel?",
  "Open the recoater plate drawing",
];

const BOM_FIELDS = [
  ["name", "Name", ["name"]],
  ["part_family", "Family", ["part_family"]],
  ["material", "Material", ["material", "material_raw"]],
  ["amount", "Amount", ["amount", "amount_raw"]],
  ["cost_raw", "Cost", ["cost", "cost_raw"]],
  ["supplier", "Supplier", ["supplier", "supplier_raw"]],
  ["interface_with", "Interface with", ["interface_with", "interface_with_raw"]],
];

function citeLabel(cite) {
  if (cite.kind === "drawing") {
    const region = cite.region ? ` · ${cite.region}` : "";
    return `${cite.drawing_id} p.${cite.page || 1}${region}`;
  }
  if (cite.kind === "correction") {
    return `accepted correction · ${cite.field}`;
  }
  const line = cite.file_line || cite.csv_line || cite.source_row;
  const lineBit = line ? ` · Line ${line}` : "";
  return `${cite.name || "BOM"} · ${cite.field}${lineBit}`;
}

function parsedChecks(item) {
  if (!item.checks) {
    return null;
  }
  if (typeof item.checks === "object") {
    return item.checks;
  }
  try {
    return JSON.parse(item.checks);
  } catch {
    return null;
  }
}

function Checks({ item }) {
  const checks = parsedChecks(item);
  if (!checks) {
    return null;
  }
  const drawing = checks.drawing;
  return (
    <div className="checks">
      {checks.part_name && <p>Part: {checks.part_name}</p>}
      <p>Part exists: {checks.part_exists ? "yes" : "no"}</p>
      <p>Field can be corrected: {checks.field_correctable ? "yes" : "no"}</p>
      <p>
        BOM value: {checks.bom_value || "(blank)"}
        {checks.file_line ? ` · file line ${checks.file_line}` : ""}
        {checks.csv_line ? ` · record ${checks.csv_line}` : ""}
      </p>
      {drawing && (
        <p>
          Drawing {drawing.drawing_id} p.{drawing.page} {drawing.region}: {drawing.value} (
          {drawing.confidence})
        </p>
      )}
      <p>Agrees with: {checks.agreement}</p>
      {checks.existing && <p>Existing {checks.existing} correction for this field</p>}
    </div>
  );
}

function ReviewActions({ id, onDecide }) {
  const [note, setNote] = useState("");
  return (
    <div className="row">
      <input
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Optional note"
      />
      <button onClick={() => onDecide(id, "accepted", note)}>Accept</button>
      <button onClick={() => onDecide(id, "rejected", note)}>Reject</button>
    </div>
  );
}

function isBomField(cite, keys) {
  return cite?.kind === "bom_row" && keys.includes(cite.field);
}

export default function App() {
  const [tab, setTab] = useState("chat");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [attachment, setAttachment] = useState(null);
  const [bomDetails, setBomDetails] = useState([]);
  const [activeCite, setActiveCite] = useState(null);
  const [corrections, setCorrections] = useState([]);
  const [correctionEvents, setCorrectionEvents] = useState([]);
  const [drawingOpen, setDrawingOpen] = useState(false);
  const [theme, setTheme] = useState(initialTheme);
  const [apiKeyMissing, setApiKeyMissing] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  useEffect(() => {
    refreshCorrections();
  }, []);

  useEffect(() => {
    if (tab === "review") {
      refreshCorrections();
    }
  }, [tab]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/status")
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (!cancelled && body) {
          setApiKeyMissing(!body.api_key_configured);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshCorrections() {
    const response = await fetch("/api/corrections");
    const body = await response.json();
    setCorrections(body.items || []);
    setCorrectionEvents(body.events || []);
  }

  async function send(text) {
    const question = (text || input).trim();
    if (!question || busy) {
      return;
    }
    setInput("");
    setMessages((current) => [...current, { role: "user", text: question }]);
    setBusy(true);
    try {
      const history = messages.slice(-6).map((item) => ({
        role: item.role === "assistant" ? "assistant" : "user",
        text: (item.text || "").slice(0, 800),
        knowledge_version: item.knowledge_version ?? null,
      }));
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, history }),
      });
      const body = await response.json();
      setMessages((current) => [...current, { role: "assistant", ...body }]);
      const firstDrawing =
        (body.citations || []).find((cite) => cite.kind === "drawing" && cite.region) ||
        (body.citations || []).find((cite) => cite.kind === "drawing");
      const firstBom =
        (body.citations || []).find((cite) => cite.kind === "bom_row" && cite.field === "material_raw") ||
        (body.citations || []).find((cite) => cite.kind === "bom_row");
      if (body.attachments && body.attachments[0]) {
        const next = body.attachments[0];
        setAttachment({
          ...next,
          page: firstDrawing?.page || 1,
          openedAt: Date.now(),
        });
      } else {
        setAttachment(null);
      }
      if (firstDrawing) {
        setActiveCite(firstDrawing);
      } else if (firstBom) {
        setActiveCite(firstBom);
      } else {
        setActiveCite(null);
      }
      const attachedRows = (body.attachments && body.attachments[0]?.bom_rows) || [];
      if (attachedRows.length) {
        setBomDetails(attachedRows);
      } else if (firstBom?.bom_row_id) {
        const evidence = await fetch(`/api/evidence/bom/${firstBom.bom_row_id}`);
        setBomDetails(evidence.ok ? [await evidence.json()] : []);
      } else {
        setBomDetails([]);
      }
    } finally {
      setBusy(false);
      refreshCorrections();
    }
  }

  async function openCite(cite, message) {
    setActiveCite(cite);
    if (cite.kind === "drawing") {
      setDrawingOpen(true);
    }
    let drawingId = cite.drawing_id;
    let reconstruction =
      message.attachments?.find((item) => item.drawing_id === drawingId)?.reconstruction || null;
    if (cite.kind === "bom_row" && cite.bom_row_id) {
      const response = await fetch(`/api/evidence/bom/${cite.bom_row_id}`);
      if (response.ok) {
        const evidence = await response.json();
        setBomDetails((current) => {
          const next = current.filter((row) => row.bom_row_id !== evidence.bom_row_id);
          return [evidence, ...next];
        });
        drawingId = drawingId || evidence.drawing_id;
        reconstruction = reconstruction || evidence.reconstruction;
      }
    }
    if (drawingId && !reconstruction) {
      const response = await fetch(`/api/evidence/drawing/${drawingId}`);
      if (response.ok) {
        reconstruction = (await response.json()).reconstruction;
      }
    }
    if (drawingId) {
      const matched =
        reconstruction && reconstruction.drawing_id && reconstruction.drawing_id !== drawingId
          ? null
          : reconstruction;
      setAttachment({
        drawing_id: drawingId,
        reconstruction: matched,
        page: cite.page || 1,
        openedAt: Date.now(),
      });
    }
  }

  function closeEvidence() {
    setAttachment(null);
    setBomDetails([]);
    setActiveCite(null);
    setDrawingOpen(false);
  }

  async function decide(id, decision, note) {
    await fetch(`/api/corrections/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, reviewer: "local", note: note || null }),
    });
    refreshCorrections();
  }

  function clearChat() {
    setMessages([]);
    setAttachment(null);
    setBomDetails([]);
    setActiveCite(null);
    setDrawingOpen(false);
  }

  const highlight = activeCite?.kind === "drawing" ? activeCite.highlight : null;
  const page = attachment?.page || activeCite?.page || 1;
  const showEvidence = Boolean(attachment || bomDetails.length);
  const reconstruction =
    attachment?.reconstruction &&
    (!attachment.reconstruction.drawing_id ||
      attachment.reconstruction.drawing_id === attachment.drawing_id)
      ? attachment.reconstruction
      : null;

  const pendingCount = corrections.filter((item) => item.status === "pending").length;

  return (
    <div className="shell">
      <aside className="rail">
        <div className="mark">M</div>
        <button className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>
          Chat
        </button>
        <button
          className={tab === "review" ? "on review-tab" : "review-tab"}
          onClick={() => setTab("review")}
          aria-label={pendingCount > 0 ? `Review, ${pendingCount} waiting` : "Review"}
        >
          Review
          {pendingCount > 0 && <span className="pending-dot" />}
        </button>
        <button
          type="button"
          className="theme-toggle"
          onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
        >
          {theme === "dark" ? "Light" : "Dark"}
        </button>
      </aside>

      {tab === "chat" ? (
        <main className={showEvidence ? "main with-evidence" : "main"}>
          <section className="chat">
            <header>
              <h1>Meridian</h1>
              <p>Ask the extracted knowledge. A citation is the source of a fact, not a file download.</p>
              {messages.length > 0 && (
                <button type="button" onClick={clearChat} disabled={busy}>
                  Clear chat
                </button>
              )}
              {apiKeyMissing && (
                <p className="key-flag" role="status">
                  No API key in .env. Add GROQ_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY, then restart the API.
                </p>
              )}
            </header>
            <div className="thread">
            {messages.length === 0 && (
              <div className="starters">
                {STARTERS.map((item) => (
                  <button key={item} onClick={() => send(item)} disabled={busy}>
                    {item}
                  </button>
                ))}
              </div>
            )}
              {messages.map((message, index) => (
                <article key={index} className={message.role}>
                  <p>{message.text}</p>
                  {message.citations?.length > 0 && (
                    <div className="cites">
                      {message.citations.slice(0, 10).map((cite, citeIndex) => (
                        <button
                          key={citeIndex}
                          type="button"
                          className={cite === activeCite ? "cite on" : "cite"}
                          onClick={() => openCite(cite, message)}
                        >
                          {citeLabel(cite)}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              ))}
              {busy && (
                <article className="assistant thinking" aria-live="polite" aria-label="Model is thinking">
                  <div className="thinking-row">
                    <span className="thinking-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                    <p>Thinking</p>
                  </div>
                </article>
              )}
            </div>
            <form
              className="composer"
              onSubmit={(event) => {
                event.preventDefault();
                send();
              }}
            >
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={busy ? "Reading stored knowledge…" : "Ask about a part, material, or interface"}
                disabled={busy}
              />
              <button type="submit" disabled={busy} aria-busy={busy}>
                {busy ? (
                  <span className="send-wait">
                    <span className="send-spinner" aria-hidden="true" />
                    Wait
                  </span>
                ) : (
                  "Send"
                )}
              </button>
            </form>
          </section>
          {showEvidence ? (
            <section className="evidence">
              <div className="evidence-bar">
                <p className="eyebrow">
                  {attachment?.drawing_id || "BOM"}
                  {reconstruction?.label ? ` · ${reconstruction.label}` : ""}
                </p>
                <button type="button" className="close" onClick={closeEvidence}>
                  Close
                </button>
              </div>
              {bomDetails.length > 1 && (
                <p className="empty">
                  This drawing is linked to {bomDetails.length} BOM rows. There is no single row
                  with this drawing title.
                </p>
              )}
              {bomDetails.map((row) => (
                <div key={row.bom_row_id || row.name} className="bom-card">
                  <p className="eyebrow">
                    BOM{row.link_status === "conflict" ? " · conflict" : ""}
                    {row.file_line || row.csv_line || row.source_row
                      ? ` · ${(row.source_file || "openlpbf-bom.csv").replace(/^.*[/\\]/, "")} Line ${row.file_line || row.csv_line || row.source_row}`
                      : ""}
                  </p>
                  {BOM_FIELDS.map(([key, label, aliases]) => (
                    <p
                      key={key}
                      className={
                        activeCite?.bom_row_id === row.bom_row_id && isBomField(activeCite, aliases)
                          ? "hit"
                          : ""
                      }
                    >
                      <span>{label}</span>
                      {row[key] || "—"}
                    </p>
                  ))}
                </div>
              ))}
              {attachment ? (
                <>
                  <DrawingViewer
                    drawingId={attachment.drawing_id}
                    page={page}
                    highlight={highlight}
                    extractedText={
                      activeCite?.kind === "drawing" ? activeCite.extracted_text : ""
                    }
                    expanded={drawingOpen}
                    onOpen={() => setDrawingOpen(true)}
                    onClose={() => setDrawingOpen(false)}
                  />
                  {reconstruction ? (
                    <Viewer3D
                      key={`${attachment.drawing_id}-${attachment.openedAt || page}-${theme}`}
                      drawingId={attachment.drawing_id}
                      label={reconstruction.label}
                      disclaimer={reconstruction.disclaimer}
                      dimensions={reconstruction.dimensions_used}
                      theme={theme}
                    />
                  ) : (
                    <p className="empty">No 3D reconstruction for this drawing.</p>
                  )}
                </>
              ) : null}
            </section>
          ) : null}
        </main>
      ) : (
        <main className="review">
          <h1>Correction review</h1>
          <p>Accepting a proposal changes later answers. Raw BOM and drawing values stay in place.</p>
          {corrections.length === 0 && <p className="empty">No proposals yet. Ask chat to correct a field.</p>}
          {corrections.map((item) => (
            <article key={item.id} className="proposal">
              <h2>
                #{item.id} {parsedChecks(item)?.part_name || `${item.entity_type} ${item.entity_id}`} · {item.field}
              </h2>
              <p>
                {item.old_value || "(blank)"} → {item.new_value}
              </p>
              <p>{item.reason}</p>
              <p>{item.check_notes}</p>
              <p>Status: {item.status}</p>
              <Checks item={item} />
              {item.status === "pending" && (
                <ReviewActions id={item.id} onDecide={decide} />
              )}
            </article>
          ))}
          <section className="event-log">
            <h2>History</h2>
            {correctionEvents.length === 0 && (
              <p className="empty">No correction events yet.</p>
            )}
            {correctionEvents.map((event) => (
              <article key={event.id}>
                <p>
                  #{event.id} {event.event} · correction #{event.correction_id}
                  {event.part_name ? ` · ${event.part_name}` : ""} · {event.field}
                </p>
                <p>
                  {event.old_value || "(blank)"} → {event.new_value}
                  {event.created_at ? ` · ${event.created_at}` : ""}
                </p>
              </article>
            ))}
          </section>
        </main>
      )}
    </div>
  );
}
