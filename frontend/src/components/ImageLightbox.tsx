import { useEffect, useState } from "react";
import { api, explain, getObject, openDownload } from "../lib/api";
import Modal from "./Modal";

export default function ImageLightbox({ id, close }: { id: number; close: () => void }) {
  const [src, setSrc] = useState("");
  const [link, setLink] = useState("");
  const [zoom, setZoom] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let object = "";
    void (async () => {
      try {
        const { data } = await api.get<{url: string}>(`/finance/proofs/${id}`);
        if (!active) return;
        setLink(data.url);
        const blob = await getObject(data.url);
        const head = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
        const mime = head[0] === 255 && head[1] === 216 ? "image/jpeg" :
          head[0] === 137 && head[1] === 80 && head[2] === 78 && head[3] === 71 ? "image/png" : "";
        if (!mime) throw new Error("This receipt is not a previewable image. Download it to review.");
        if (active) { object = URL.createObjectURL(new Blob([blob], { type: mime })); setSrc(object); }
      } catch (e) { if (active) setError(explain(e)); }
    })();
    return () => { active = false; if (object) URL.revokeObjectURL(object); };
  }, [id]);
  return <Modal title={`Receipt #${id}`} close={close}><div className="form">
    {!src && !error && <p className="muted">Loading receipt…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {src && <div className={zoom ? "receipt zoom" : "receipt"}><img src={src} alt={`Receipt for proof ${id}`}/></div>}
    <div className="actions">{src && <button className="secondary" onClick={() => setZoom(!zoom)}>{zoom ? "Fit image" : "Zoom in"}</button>}
      {link && <button className="secondary" onClick={() => void openDownload(link).catch(e => setError(explain(e)))}>Download</button>}</div>
  </div></Modal>;
}
