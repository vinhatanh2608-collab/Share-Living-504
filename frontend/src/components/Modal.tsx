import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

export default function Modal({ title, children, close, busy = false }:
  { title: string; children: ReactNode; close: () => void; busy?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);
  return <dialog ref={ref} className="modal" aria-label={title} onCancel={e => { e.preventDefault(); if (!busy) close(); }}>
    <div className="modal-head"><h2>{title}</h2><button className="icon" aria-label="Close" onClick={close} disabled={busy}><X size={20}/></button></div>
    {children}
  </dialog>;
}
