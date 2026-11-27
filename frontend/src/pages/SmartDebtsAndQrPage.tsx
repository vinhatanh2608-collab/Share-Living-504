import { useEffect, useState } from "react";
import { ArrowDownLeft, ArrowUpRight, Copy, ImagePlus, QrCode } from "lucide-react";
import Modal from "../components/Modal";
import { api, explain, getObject, upload } from "../lib/api";
import { receiptMime } from "../lib/imageCompress";
import { vietQrUrl } from "../lib/safeUrl";
import { type Bank, type Debt, type Group, type Member, useAction, useResource, vnd } from "../lib/living";
import type { Ticket } from "../types/api";
// VietQR public BIN directory: https://api.vietqr.io/v2/banks
import bankNames from "../lib/bankNames.json";

const debtLabels = { UNPAID: "Mới phát sinh", PENDING: "Chờ chủ nợ duyệt", PAID: "Đã thanh toán" };
const proofLabels: Record<string, string> = { PENDING: "Chờ xác minh", PAID: "Đã xác nhận", REJECTED: "Đã từ chối" };
const maxInputSize = 5_000_000;

function Directory({ members }: { members: Member[] }) {
  const listed = members.filter(member => member.qr);
  return <div className="living-grid">{!listed.length && <p>Chưa có thành viên cập nhật VietQR.</p>}
    {listed.map(member => {
      const qr = member.qr!;
      const url = `https://img.vietqr.io/image/${qr.bank_bin}-${qr.bank_account_no}-compact2.png?` +
        new URLSearchParams({ accountName: qr.bank_account_name });
      return <div className="living-card living-qr" key={member.id}><strong>{member.name}</strong>
        <img src={vietQrUrl(url)} alt={`VietQR của ${member.name}`} loading="lazy" referrerPolicy="no-referrer" />
        <span>{qr.bank_account_name} · {qr.bank_account_no}</span></div>;
    })}</div>;
}

export function ProofReview({ groupId, proofId, creditor, close, reload }: {
  groupId: number; proofId: number; creditor: boolean; close: () => void; reload: () => void;
}) {
  const path = `/groups/${groupId}/proofs/${proofId}`;
  const receipt = useResource<{ url: string; status: string; reject_reason: string | null; note?: string | null }>(path);
  const [image, setImage] = useState("");
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  const action = useAction(() => { reload(); close(); });
  const url = receipt.data?.url;
  useEffect(() => {
    if (!url) return;
    let objectUrl = "";
    let active = true;
    void getObject(url).then(async blob => {
      const mime = await receiptMime(blob);
      if (!mime) throw new Error("Định dạng ảnh không được hỗ trợ.");
      if (active) {
        objectUrl = URL.createObjectURL(new Blob([blob], { type: mime }));
        setImage(objectUrl);
      }
    }).catch(error => { if (active) setError(explain(error)); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  function decide(decision: "APPROVE" | "REJECT") {
    void action.run(() => api.patch(`${path}/verify`, { decision, reject_reason: decision === "REJECT" ? reason.trim() : null }));
  }
  return <Modal title={`Biên lai #${proofId}`} close={close} busy={action.busy}>
    <div className="form">
      {receipt.data && <p>{proofLabels[receipt.data.status] ?? receipt.data.status}</p>}
      {receipt.data?.note && <p>Ghi chú người gửi: {receipt.data.note}</p>}
      {receipt.data?.reject_reason && <p>Lý do từ chối: {receipt.data.reject_reason}</p>}
      {!image && !receipt.error && !error && <p role="status">Đang tải biên lai…</p>}
      {(receipt.error || error || action.error) && <p className="error" role="alert">{receipt.error || error || action.error}</p>}
      {image && <img className="living-receipt" src={image} alt="Biên lai chuyển tiền" />}
      {creditor && receipt.data?.status === "PENDING" && <>
        <button className="primary" disabled={action.busy || !image} onClick={() => decide("APPROVE")}>Xác nhận đã nhận tiền</button>
        <label>Lý do từ chối<textarea value={reason} maxLength={300} onChange={event => setReason(event.target.value)} /></label>
        <button className="secondary" disabled={action.busy || !reason.trim()} onClick={() => decide("REJECT")}>Từ chối biên lai</button>
      </>}
    </div>
  </Modal>;
}

type Quote = { amount: number; qr_url: string | null; bank: Bank | null; transfer_description: string };
type Transfer = { debtor_id: number; creditor_id: number; amount: number; explanation_summary: string; chain_steps: string[] };
type Balance = { user_id: number; total_contributed: number; total_share: number; total_settled: number; net_balance: number };
type Simplification = { transfers: Transfer[]; member_breakdown: Balance[]; original_count: number; simplified_count: number;
  pending: { proof_id: number; debtor_id: number; creditor_id: number; amount: number }[] };
type PaymentSelection = { target: { debt_ids: number[] } | { transfer: { creditor_id: number; amount: number } };
  name: string; amount: number; initialTab: "qr" | "proof" };

function Payment({ groupId, selection, close, reload }: {
  groupId: number; selection: PaymentSelection; close: () => void; reload: () => void;
}) {
  const path = `/groups/${groupId}`;
  const { target, name, amount } = selection;
  const [tab, setTab] = useState<"qr" | "proof">(selection.initialTab);
  const [quote, setQuote] = useState<Quote>();
  const [quoteError, setQuoteError] = useState("");
  const [qrFailed, setQrFailed] = useState(false);
  const [uploaded, setUploaded] = useState<string>();
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState("");
  const [validation, setValidation] = useState("");
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState("");
  const action = useAction(() => { reload(); close(); });
  useEffect(() => {
    const controller = new AbortController();
    void api.post<Quote>(`${path}/debts/qr`, target, { signal: controller.signal })
      .then(response => { if (!controller.signal.aborted) setQuote(response.data); })
      .catch(error => { if (!controller.signal.aborted) setQuoteError(explain(error)); });
    return () => controller.abort();
  }, [path, target]);
  useEffect(() => {
    setPreview("");
    if (!file) return;
    const objectUrl = URL.createObjectURL(file);
    setPreview(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  function selectFile(candidate?: File) {
    if (action.busy) return;
    setUploaded(undefined);
    setFile(undefined);
    setValidation("");
    if (!candidate) return;
    if (!candidate.size || candidate.size > maxInputSize || !["image/jpeg", "image/png", "image/webp"].includes(candidate.type)) {
      setValidation("Chọn ảnh JPG, PNG hoặc WebP có dung lượng tối đa 5 MB.");
      return;
    }
    setFile(candidate);
  }
  async function copy(value: string, label: string) {
    try { await navigator.clipboard.writeText(value); setCopied(`Đã sao chép ${label}.`); }
    catch { setCopied("Hãy chọn nội dung và sao chép thủ công."); }
  }
  function submit() {
    if (!file) return;
    void action.run(async () => {
      let uploadId = uploaded;
      if (!uploadId) {
        const mime = await receiptMime(file);
        if (!mime || file.type !== mime) throw new Error("Ảnh biên lai không hợp lệ.");
        const { data } = await api.post<Ticket>(`${path}/proofs/upload`, { ...target, size: file.size, content_type: mime });
        await upload(data, file);
        uploadId = data.upload_id;
        setUploaded(uploadId);
      }
      await api.post(`${path}/proofs`, { ...target, upload_id: uploadId, note: note.trim() || null });
    });
  }
  const url = quote?.qr_url && vietQrUrl(quote.qr_url);
  return <Modal title="Thanh toán khoản nợ" close={close} busy={action.busy}>
    <div className="form">
      <div className="living-row"><span>Người nhận · {name}</span><strong>{vnd(quote?.amount ?? amount)}</strong></div>
      <div className="living-buttons"><button className="secondary" disabled={action.busy} aria-pressed={tab === "qr"} onClick={() => setTab("qr")}><QrCode size={16}/> Quét mã QR</button>
        <button className="secondary" disabled={action.busy} aria-pressed={tab === "proof"} onClick={() => setTab("proof")}><ImagePlus size={16}/> Đã chuyển tiền</button></div>
      {tab === "qr" ? <>
        {!quote && !quoteError && <p role="status">Đang tải thông tin chuyển khoản…</p>}
        {quoteError && <p role="alert">{quoteError}</p>}
        {quote && (!url || qrFailed) && <p role="status">Chưa thể hiển thị mã QR. Hãy kiểm tra thông tin chuyển khoản với {name}.</p>}
        {url && !qrFailed && <div className="living-qr"><img src={url} alt={`VietQR thanh toán cho ${name}`} onError={() => setQrFailed(true)} referrerPolicy="no-referrer" /></div>}
        {quote?.bank && <div className="living-card">{[
          ["Ngân hàng", (bankNames as Record<string, string>)[quote.bank.bank_bin] ?? `BIN ${quote.bank.bank_bin}`],
          ["Số tài khoản", quote.bank.bank_account_no], ["Chủ tài khoản", quote.bank.bank_account_name],
        ].map(([label, value]) => <div className="living-row" key={label}><span>{label} · {value}</span>
          <button className="icon" aria-label={`Sao chép ${label}`} onClick={() => void copy(value, label.toLowerCase())}><Copy size={16}/></button></div>)}</div>}
        {quote?.transfer_description && <div className="living-row"><span>Nội dung · {quote.transfer_description}</span><button className="icon" aria-label="Sao chép nội dung" onClick={() => void copy(quote.transfer_description, "nội dung")}><Copy size={16}/></button></div>}
        {copied && <p role="status">{copied}</p>}
        <button className="primary" onClick={() => setTab("proof")}>Đã chuyển khoản — Nộp ảnh xác minh</button>
      </> : <form className="form" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label className="living-card" onDragOver={event => event.preventDefault()} onDrop={event => {
          event.preventDefault(); selectFile(event.dataTransfer.files[0]);
        }}><ImagePlus aria-hidden="true"/> Kéo thả hoặc chọn ảnh biên lai
          <small>JPG, PNG hoặc WebP · Tối đa 5 MB</small>
          <input type="file" accept="image/jpeg,image/png,image/webp" disabled={action.busy} onChange={event => selectFile(event.target.files?.[0])}/></label>
        {preview && <img className="living-receipt" src={preview} alt="Xem trước biên lai"/>}
        <label>Ghi chú (không bắt buộc)<textarea maxLength={300} disabled={action.busy} value={note} onChange={event => setNote(event.target.value)}/></label>
        {(validation || action.error) && <p className="error" role="alert">{validation || action.error}</p>}
        <button className="primary" disabled={action.busy || !file}>{action.busy ? "Đang gửi…" : "Gửi biên lai xác minh"}</button>
      </form>}
    </div></Modal>;
}

export default function SmartDebtsAndQrPage({ group, userId, revision, reload, members }: {
  group: Group; userId: number; revision: number; reload: () => void; members?: Member[];
}) {
  const path = `/groups/${group.id}`;
  const debts = useResource<Debt[]>(`${path}/debts`, revision);
  const simplified = useResource<Simplification>(`${path}/debts/simplified`, revision);
  const directory = useResource<Member[]>(members ? null : `${path}/members`, revision);
  const people = members ?? directory.data ?? [];
  const [selected, setSelected] = useState<number[]>([]);
  const [payment, setPayment] = useState<PaymentSelection>();
  const [proof, setProof] = useState<{ id: number; creditor: boolean }>();
  const [notice, setNotice] = useState("");
  const action = useAction(reload);
  const rows = debts.data ?? [];
  const owed = rows.filter(row => row.debtor_id === userId);
  const receivable = rows.filter(row => row.creditor_id === userId);
  const chosen = owed.filter(row => row.status === "UNPAID" && selected.includes(row.id));
  const names = new Map(people.map(member => [member.id, member.name]));
  const nameOf = (id: number) => names.get(id) ?? `Thành viên #${id}`;
  function pay(items: Debt[], initialTab: PaymentSelection["initialTab"]) {
    setPayment({ target: { debt_ids: items.map(item => item.id) }, name: nameOf(items[0].creditor_id),
      amount: items.reduce((sum, item) => sum + item.amount, 0), initialTab });
  }
  function payTransfer(edge: Transfer, initialTab: PaymentSelection["initialTab"]) {
    setPayment({ target: { transfer: { creditor_id: edge.creditor_id, amount: edge.amount } },
      name: nameOf(edge.creditor_id), amount: edge.amount, initialTab });
  }
  const balance = simplified.data?.member_breakdown.find(member => member.user_id === userId);
  const net = balance?.net_balance;
  return <section className="living-stack"><h2>Nhắc nợ thông minh</h2>
    {[debts.error, simplified.error, directory.error, action.error].filter(Boolean).map((error, index) => <p className="error" role="alert" key={index}>{error}</p>)}
    {notice && <p role="status">{notice}</p>}
    <div className="group-balances">
      <div className="living-card"><span>Tôi cần trả</span><h3>{net === undefined ? "…" : vnd(Math.max(0, -net))}</h3></div>
      <div className="living-card"><span>Tôi cần thu</span><h3>{net === undefined ? "…" : vnd(Math.max(0, net))}</h3></div>
      <div className="living-card"><span>Số dư ròng</span><h3>{net === undefined ? "…" : vnd(net)}</h3></div>
    </div>
    {simplified.loading && !simplified.data && <p role="status">Đang tính công nợ…</p>}
    {simplified.data && <>
      <div className="living-row"><strong className="pill">Đã tự động cấn trừ nợ chéo</strong>
        <span>Rút gọn từ {simplified.data.original_count} khoản nợ xuống còn {simplified.data.simplified_count} lần chuyển khoản</span></div>
      <div className="living-grid group-debt-columns">{[true, false].map(outgoing => {
        const items = simplified.data!.transfers.filter(edge => (outgoing ? edge.debtor_id : edge.creditor_id) === userId);
        const pending = simplified.data!.pending.filter(edge => (outgoing ? edge.debtor_id : edge.creditor_id) === userId);
        return <section className="living-stack" key={String(outgoing)}><h3>{outgoing ? "Khoản bạn cần trả" : "Khoản cần thu hồi"}</h3>
          {!items.length && !pending.length && <p>{outgoing ? "Bạn không cần chuyển khoản." : "Bạn không còn khoản phải thu."}</p>}
          {items.map(edge => <article className="living-card" key={`${edge.debtor_id}-${edge.creditor_id}`}>
            <div className="living-row"><strong>{nameOf(outgoing ? edge.creditor_id : edge.debtor_id)}</strong><strong>{vnd(edge.amount)}</strong></div>
            <p>{edge.explanation_summary}</p><details><summary>Xem chi tiết cách tính</summary>
              <ul>{edge.chain_steps.map((step, index) => <li key={index}>{step}</li>)}</ul>
              {balance && <dl>{[["Đã ứng trước", balance.total_contributed], ["Phần phải chia", balance.total_share],
                ["Đã thanh toán", balance.total_settled], ["Còn lại", balance.net_balance]].map(([label, value]) =>
                  <div className="living-row" key={label}><dt>{label}</dt><dd>{vnd(Number(value))}</dd></div>)}</dl>}
            </details><div className="living-buttons">{outgoing ? <>
              <button className="primary" onClick={() => payTransfer(edge, "qr")}>Thanh toán</button>
              <button className="secondary" onClick={() => payTransfer(edge, "proof")}>Nộp ảnh xác minh</button></> :
              <button className="secondary" disabled={action.busy} onClick={() => void action.run(async () => {
                const { data } = await api.post<{ notified: number }>(`${path}/debts/remind`, { debtor_id: edge.debtor_id, amount: edge.amount });
                setNotice(data.notified ? "Đã gửi nhắc nợ." : "Bạn có thể gửi lại lời nhắc sau 24 giờ.");
              })}>Gửi nhắc nợ</button>}</div></article>)}
          {pending.map(edge => <article className="living-card" key={edge.proof_id}>
            <div className="living-row"><strong>{nameOf(outgoing ? edge.creditor_id : edge.debtor_id)}</strong><strong>{vnd(edge.amount)}</strong></div>
            <span className="pill wait">Chờ chủ nợ duyệt</span>
            <button className="secondary" onClick={() => setProof({ id: edge.proof_id, creditor: !outgoing })}>
              {outgoing ? "Xem biên lai" : "Xem & duyệt biên lai"}</button></article>)}
        </section>;
      })}</div>
    </>}
    <details className="living-card"><summary>Chi tiết khoản nợ theo hóa đơn</summary>
    <div className="living-grid group-debt-columns">{[{ title: "Hóa đơn phải trả", outgoing: true, items: owed }, { title: "Hóa đơn phải thu", outgoing: false, items: receivable }].map(({ title, outgoing, items }) =>
      <section className="living-stack" key={title}><h3>{title}</h3>
        {debts.loading && !debts.data && <p role="status">Đang tải khoản nợ…</p>}
        {debts.data && !items.length && <p>{outgoing ? "Bạn chưa có khoản phải trả." : "Bạn chưa có khoản phải thu."}</p>}
        {items.map(row => <article className="living-card" key={row.id}>
          <div className="living-row"><strong>{outgoing ? <ArrowUpRight size={18}/> : <ArrowDownLeft size={18}/>} {nameOf(outgoing ? row.creditor_id : row.debtor_id)}</strong><strong>{vnd(row.amount)}</strong></div>
          <div className="living-row"><span>{row.title}</span><span className={row.overdue ? "pill late" : row.status === "PENDING" ? "pill wait" : "pill"}>{row.overdue ? "Đã quá hạn" : debtLabels[row.status]}</span></div>
          <div className="living-buttons">
            {outgoing && row.status === "UNPAID" && <>
              <button className="primary" onClick={() => pay([row], "qr")}>Thanh toán</button>
              <button className="secondary" onClick={() => pay([row], "proof")}>Nộp ảnh xác minh</button>
              <label className="living-check"><input type="checkbox" checked={selected.includes(row.id)}
                disabled={(chosen.length >= 25 && !selected.includes(row.id)) || Boolean(chosen[0] && chosen[0].creditor_id !== row.creditor_id)}
                onChange={event => setSelected(ids => event.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id))}/> Chọn trả gộp</label></>}
            {!outgoing && row.status === "UNPAID" && <button className="secondary" disabled={action.busy} onClick={() => void action.run(async () => {
              const { data } = await api.post<{ notified: number }>(`${path}/debts/remind`, { debt_ids: [row.id] });
              setNotice(data.notified ? "Đã gửi nhắc nợ." : "Chưa gửi thêm lời nhắc. Bạn có thể thử lại sau 24 giờ.");
            })}>Gửi nhắc nợ</button>}
            {row.proof_id !== null && <button className="secondary" onClick={() => setProof({ id: row.proof_id!, creditor: !outgoing })}>
              {!outgoing && row.status === "PENDING" ? "Xem & duyệt biên lai" : "Xem biên lai"}</button>}
          </div></article>)}
        {outgoing && chosen.length > 0 && <button className="primary" onClick={() => pay(chosen, "qr")}>Thanh toán gộp {chosen.length} khoản · {vnd(chosen.reduce((sum, row) => sum + row.amount, 0))}</button>}
      </section>)}</div></details>
    <details className="living-card"><summary>Danh bạ VietQR của nhóm</summary><Directory members={people} /></details>
    {payment && <Payment groupId={group.id} selection={payment} close={() => setPayment(undefined)} reload={() => { setSelected([]); reload(); }}/>}
    {proof && <ProofReview groupId={group.id} proofId={proof.id} creditor={proof.creditor} close={() => setProof(undefined)} reload={reload}/>}
  </section>;
}
