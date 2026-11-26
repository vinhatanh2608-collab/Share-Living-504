import { BookOpen, Download, FileText, Plus, Search } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, explain, openDownload, upload } from "../lib/api";
import { date } from "../lib/format";
import { useRealtimeStore } from "../store/useRealtimeStore";
import type { Material, Note, Page, Ticket, User } from "../types/api";
import Modal from "../components/Modal";

function Errata({ scope }: { scope: string }) {
  const [note, setNote] = useState<Note>();
  const [text, setText] = useState("");
  const [remote, setRemote] = useState<Note>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const change = useRealtimeStore(s => s.notes);
  const name = `errata-${scope}`;
  const current = useRef({ note, text });
  current.current = { note, text };
  useEffect(() => {
    const cont = new AbortController();
    void api.get<Note>(`/study/notes/${encodeURIComponent(name)}`, { signal: cont.signal }).then(({data}) => {
      if (cont.signal.aborted) return;
      const { note, text } = current.current;
      if (!note || note.text === text) { setNote(data); setText(data.text); setRemote(undefined); }
      else if (note.version !== data.version) setRemote(data);
    }).catch(e => { if (!cont.signal.aborted) setError(explain(e)); });
    return () => cont.abort();
  }, [change, name]);
  async function save() {
    if (!note || busy) return;
    setBusy(true); setError("");
    try {
      const { data } = await api.put<Note>(`/study/notes/${encodeURIComponent(name)}`, { text, expected_version: note.version });
      setNote(data); setText(data.text); setRemote(undefined);
    } catch (e) {
      setError(explain(e));
      try { const {data} = await api.get<Note>(`/study/notes/${encodeURIComponent(name)}`); if (data.version !== note.version) setRemote(data); } catch { /* Keep the original error. */ }
    } finally { setBusy(false); }
  }
  return <section className="panel section"><div className="section-head"><div><h2><BookOpen size={20}/> Shared errata</h2><p>Record corrections and study tips together.</p></div><span className="pill">{note ? `Version ${note.version}` : "Loading"}</span></div>
    <div className="form notes"><label>Note<textarea rows={5} placeholder="Add a correction or tip…" maxLength={20000} disabled={!note || busy} value={text} onChange={e => setText(e.target.value)}/></label>
      {error && <p className="error" role="alert">{error}</p>}
      {remote && <div className="notice">Another roommate updated this note. Your draft is preserved.
        <details><summary>Read latest saved note</summary><pre>{remote.text || "(empty)"}</pre></details>
        <div className="actions"><button className="secondary" onClick={() => { setText(remote.text); setNote(remote); setRemote(undefined); setError(""); }}>Use latest</button>
          <button className="secondary" onClick={() => { setNote(remote); setRemote(undefined); setError(""); }}>Keep editing my draft</button></div></div>}
      <button className="primary self-start" disabled={!note || busy || !!remote || text === note.text} onClick={() => void save()}>{busy ? "Saving…" : "Save note"}</button>
    </div></section>;
}

export default function StudyVaultPage({ user }: { user: User }) {
  const [items, setItems] = useState<Material[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [revision, setRevision] = useState(0);
  const [editor, setEditor] = useState<Material | "new">();
  const [errata, setErrata] = useState("room");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File>();
  const uploaded = useRef<string>();
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [removing, setRemoving] = useState<Material>();
  const epoch = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    const id = ++epoch.current;
    setLoading(true); setError(""); setCursor(null);
    void api.get<Page<Material>>("/study/materials", { params: { q: search }, signal: controller.signal }).then(({data}) => {
      if (epoch.current === id) { setItems(data.items); setCursor(data.next_cursor); }
    }).catch(e => { if (epoch.current === id) setError(explain(e)); }).finally(() => { if (epoch.current === id) setLoading(false); });
    return () => { epoch.current++; controller.abort(); };
  }, [search, revision]);
  async function more() {
    if (!cursor || loading) return;
    setLoading(true);
    const id = epoch.current;
    try {
      const { data } = await api.get<Page<Material>>("/study/materials", { params: { q: search, after: cursor } });
      if (epoch.current === id) { setItems(old => [...old, ...data.items]); setCursor(data.next_cursor); }
    } catch (e) { if (epoch.current === id) setError(explain(e)); }
    finally { if (epoch.current === id) setLoading(false); }
  }
  function edit(item: Material | "new") {
    setEditor(item); setTitle(item === "new" ? "" : item.title); setDescription(item === "new" ? "" : item.description);
    setFile(undefined); uploaded.current = undefined; setFormError("");
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!editor || busy || !title.trim()) return;
    setBusy(true); setFormError("");
    try {
      const fields = { title: title.trim(), description: description.trim() };
      if (editor === "new") {
        if (!file || !["application/pdf", "text/plain"].includes(file.type) || !file.size || file.size > 20_000_000)
          throw new Error("Choose a non-empty PDF or TXT file up to 20 MB.");
        if (!uploaded.current) {
          const { data } = await api.post<Ticket>("/study/uploads", { content_type: file.type, size: file.size });
          await upload(data, file); uploaded.current = data.upload_id;
        }
        await api.post("/study/materials", { ...fields, upload_id: uploaded.current });
      } else await api.put(`/study/materials/${encodeURIComponent(editor.id)}`, fields);
      setEditor(undefined); setRevision(x => x + 1);
    } catch (e) { setFormError(explain(e)); } finally { setBusy(false); }
  }
  async function download(item: Material) {
    try {
      const { data } = await api.get<{url: string}>(`/study/materials/${encodeURIComponent(item.id)}`);
      await openDownload(data.url);
    } catch (e) { setError(explain(e)); }
  }
  async function remove() {
    if (!removing || busy) return;
    setBusy(true);
    try {
      await api.delete(`/study/materials/${encodeURIComponent(removing.id)}`);
      if (errata === removing.id) setErrata("room");
      setRemoving(undefined); setRevision(x => x + 1);
    } catch (e) { setFormError(explain(e)); } finally { setBusy(false); }
  }
  return <div className="page"><header className="page-header"><div><p className="eyebrow">ROOM KNOWLEDGE BASE</p><h1>Study vault</h1><p>Shared PDFs, text files, and corrections in one place.</p></div><button className="primary" onClick={() => edit("new")}><Plus size={17}/> Upload material</button></header>
    <section className="panel section"><div className="section-head"><div><h2>Materials</h2><p>Search titles and descriptions.</p></div></div>
      <form className="search-form" onSubmit={e => { e.preventDefault(); setSearch(query.trim()); }}><Search size={17}/><input aria-label="Search materials" placeholder="Search study materials…" value={query} maxLength={200} onChange={e => setQuery(e.target.value)}/><button className="secondary">Search</button>{search && <button type="button" className="secondary" onClick={() => { setSearch(""); setQuery(""); }}>Clear</button>}</form>
      {error && <p className="error" role="alert">{error}</p>}
      {!items.length && <p className="empty">{loading ? "Loading materials…" : "No matching materials."}</p>}
      {items.map(item => <div className="review-row" key={item.id}><FileText size={19}/><div className="grow break-words"><strong>{item.title}</strong><small>{date(item.created_at)}{item.description && ` · ${item.description}`}</small></div>
        <button className="secondary" onClick={() => void download(item)}><Download size={16}/> Download</button><button className="secondary" onClick={() => setErrata(item.id)}>Errata</button>
        {(user.is_owner || item.owner_id === user.id) && <><button className="secondary" onClick={() => edit(item)}>Edit</button><button className="secondary danger" onClick={() => { setFormError(""); setRemoving(item); }}>Delete</button></>}</div>)}
      {cursor && <button className="secondary mt-3" onClick={() => void more()} disabled={loading}>{loading ? "Loading…" : "Load more"}</button>}
    </section>
    {errata !== "room" && <div className="actions"><span className="pill">Material errata selected</span><button className="secondary" onClick={() => setErrata("room")}>Back to room errata</button></div>}
    <Errata key={errata} scope={errata}/>
    {editor && <Modal title={editor === "new" ? "Upload study material" : "Edit study material"} close={() => setEditor(undefined)} busy={busy}><form className="form" onSubmit={save}>
      <label>Title<input value={title} maxLength={200} required onChange={e => setTitle(e.target.value)}/></label>
      <label>Description<textarea rows={3} value={description} maxLength={4000} onChange={e => setDescription(e.target.value)}/></label>
      {editor === "new" && <label>PDF or TXT<input required type="file" accept=".pdf,.txt,application/pdf,text/plain" disabled={busy} onChange={e => { uploaded.current = undefined; setFile(e.target.files?.[0]); }}/></label>}
      {formError && <p className="error" role="alert">{formError}</p>}
      <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save material"}</button>
    </form></Modal>}
    {removing && <Modal title="Delete material" close={() => setRemoving(undefined)} busy={busy}><div className="form">
      <p>Remove “{removing.title}” from the shared library?</p>{formError && <p className="error" role="alert">{formError}</p>}
      <button className="secondary danger" disabled={busy} onClick={() => void remove()}>Delete material</button>
    </div></Modal>}
  </div>;
}
