from datetime import timedelta
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import case, delete, func, select

from .deps import Actor
from .routes_living import approved_member, qr
from ..models.living import DebtSplit, ExpenseParticipant, GroupEvent, GroupExpense, GroupMember, GroupProof, GroupReminder, ReminderCooldown, LivingGroup
from ..models.mysql_models import now
from ..schemas.living import GROUP_PROOF_MAX_BYTES, DebtSelection, GroupExpenseIn, GroupExpenseUpdate, ReceiptIn, ReceiptSubmit, ReceiptUpload, ReminderTransfer, VerifyIn
from ..services.expense_participation import resolve_participants
from ..services.debt_simplifier import simplify_group
from ..services.storage_service import PROOF_IMAGE_TYPES, download, finalize, upload_ticket
from ..services.vietqr_service import image_url

from ..models.living import GroupExpenseTemplate, GroupFundTransaction
from ..schemas.living import ExpenseTemplateIn, FundAdjustment, FundDeposit

router = APIRouter(prefix="/groups/{group_id}", tags=["group-finance"])


async def allocations(sql, group_id, actor_id, data):
    members = (await sql.scalars(select(GroupMember).where(GroupMember.group_id == group_id,
        GroupMember.status == "APPROVED").with_for_update())).all()
    rows = resolve_participants(data, actor_id, {member.user_id for member in members})
    required = {row["user_id"] for row in rows if row["status"] != "excluded"}
    if not getattr(data, "funded_by_pool", False) and not required <= {member.user_id for member in members if member.bank_bin}:
        raise HTTPException(422, "Active participants need a group VietQR")
    return rows


def store_participants(sql, expense, rows):
    sql.add_all(ExpenseParticipant(expense_id=expense.id, **row) for row in rows)
    expense.payer_share = next(row["share_amount"] for row in rows if row["user_id"] == expense.creditor_id)
    if all(row["user_id"] == expense.creditor_id or row["paid_amount"] == 0 for row in rows):
        sql.add_all(DebtSplit(expense_id=expense.id, debtor_id=row["user_id"], amount=row["share_amount"])
                    for row in rows if row["user_id"] != expense.creditor_id and row["share_amount"])


async def lock_group(sql, group_id, actor_id):
    if not await sql.get(LivingGroup, group_id, with_for_update=True):
        raise HTTPException(404, "Group unavailable")
    await approved_member(sql, group_id, actor_id, lock=True)


@router.post("/expenses", status_code=201)
async def log_expense(group_id: int, data: GroupExpenseIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        return await save_expense(sql, group_id, actor.id, data)


async def save_expense(sql, group_id, actor_id, data):
    # Preserve replay fingerprints created by older integer-participant clients.
    extended = data.funded_by_pool or data.template_id is not None or data.old_reading is not None
    if not extended and data.participants and isinstance(data.participants[0], int):
        import json
        payload = data.model_dump(mode="json")
        ordered = {k: payload[k] for k in ("request_id", "title", "category", "amount", "participants", "custom_shares")}
        serialized = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    else:
        serialized = data.model_dump_json(exclude=set() if extended else {"funded_by_pool", "template_id", "old_reading", "new_reading", "unit_price"})
    fingerprint = sha256(serialized.encode()).hexdigest()
    prior = await sql.scalar(select(GroupExpense).where(GroupExpense.group_id == group_id,
        GroupExpense.creditor_id == actor_id, GroupExpense.request_id == str(data.request_id)).with_for_update())
    if prior:
        if prior.request_hash != fingerprint:
            raise HTTPException(409, "Request ID already used for a different expense")
        return {"id": prior.id}
    if data.funded_by_pool and await fund_balance(sql, group_id) < data.amount:
        raise HTTPException(409, "S? d? qu? chung kh?ng ??.")
    if data.template_id is not None:
        template = await scoped_template(sql, group_id, data.template_id)
        if data.new_reading is not None:
            if template.last_meter_reading is not None and data.old_reading != template.last_meter_reading:
                raise HTTPException(409, "Ch? s? ?? thay ??i. H?y t?i l?i m?u chi ti?u.")
            template.last_meter_reading = data.new_reading
    rows = await allocations(sql, group_id, actor_id, data)
    expense = GroupExpense(group_id=group_id, creditor_id=actor_id, request_id=str(data.request_id),
        request_hash=fingerprint, title=data.title, category=data.category, amount=data.amount,
        payer_share=0)
    sql.add(expense)
    await sql.flush()
    if data.funded_by_pool:
        sql.add_all(ExpenseParticipant(expense_id=expense.id, **row) for row in rows)
        sql.add(GroupFundTransaction(group_id=group_id, user_id=actor_id,
            tx_type="expense_deduction", amount=-data.amount, expense_id=expense.id))
    else:
        store_participants(sql, expense, rows)
    return {"id": expense.id}


@router.put("/expenses/{expense_id}")
async def update_expense(group_id: int, expense_id: int, data: GroupExpenseUpdate, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        expense = await sql.scalar(select(GroupExpense).where(GroupExpense.id == expense_id,
            GroupExpense.group_id == group_id, GroupExpense.creditor_id == actor.id).with_for_update())
        if not expense:
            raise HTTPException(404, "Expense unavailable")
        if await sql.scalar(select(GroupFundTransaction.id).where(GroupFundTransaction.expense_id == expense.id)):
            raise HTTPException(409, "Kh?ng th? s?a kho?n chi ?? tr?ch t? qu?.")
        if await sql.scalar(select(GroupProof.id).where(GroupProof.group_id == group_id,
                GroupProof.transfer_amount.is_not(None), GroupProof.status == "PENDING").limit(1)):
            raise HTTPException(409, "A group transfer is awaiting review")
        if await sql.scalar(select(DebtSplit.id).where(DebtSplit.expense_id == expense.id,
                DebtSplit.status != "UNPAID").limit(1)):
            raise HTTPException(409, "An expense with a submitted payment cannot be changed")
        rows = await allocations(sql, group_id, actor.id, data)
        await sql.execute(delete(DebtSplit).where(DebtSplit.expense_id == expense.id))
        await sql.execute(delete(ExpenseParticipant).where(ExpenseParticipant.expense_id == expense.id))
        expense.title, expense.category, expense.amount = data.title, data.category, data.amount
        store_participants(sql, expense, rows)
        return {"id": expense.id}


@router.get("/expenses")
async def expenses(group_id: int, request: Request, actor: Actor, before: int | None = None):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        query = select(GroupExpense).where(GroupExpense.group_id == group_id)
        if before is not None:
            query = query.where(GroupExpense.id < before)
        rows = (await sql.scalars(query.order_by(GroupExpense.id.desc()).limit(100))).all()
        if not rows:
            return []
        pool_expense_ids = set(await sql.scalars(select(GroupFundTransaction.expense_id).where(
            GroupFundTransaction.group_id == group_id, GroupFundTransaction.expense_id.in_([row.id for row in rows]))))
        shares = {row.id: ([{"user_id": row.creditor_id, "amount": row.payer_share}]
                           if row.payer_share else []) for row in rows}
        splits = await sql.scalars(select(DebtSplit).where(
            DebtSplit.expense_id.in_(shares.keys())).order_by(DebtSplit.debtor_id))
        for split in splits:
            shares[split.expense_id].append({"user_id": split.debtor_id, "amount": split.amount})
        participants = {row.id: [] for row in rows}
        entries = await sql.scalars(select(ExpenseParticipant).where(
            ExpenseParticipant.expense_id.in_(participants)).order_by(ExpenseParticipant.user_id))
        for entry in entries:
            participants[entry.expense_id].append({"user_id": entry.user_id, "status": entry.status,
                "paid_amount": entry.paid_amount, "share_amount": entry.share_amount,
                "net": entry.paid_amount - entry.share_amount})
        for row in rows:
            if participants[row.id]:
                shares[row.id] = [{"user_id": p["user_id"], "amount": p["share_amount"]}
                                  for p in participants[row.id] if p["status"] != "excluded"]
            else:
                legacy = {p["user_id"]: p["amount"] for p in shares[row.id]}
                legacy.setdefault(row.creditor_id, 0)
                for uid, amount in legacy.items():
                    paid = row.amount if uid == row.creditor_id else 0
                    participants[row.id].append({"user_id": uid, "status": "paid" if paid else "unpaid",
                        "paid_amount": paid, "share_amount": amount, "net": paid - amount})
        return [{**{key: getattr(row, key) for key in
                    ("id", "creditor_id", "title", "category", "amount", "created_at")},
                 "funded_by_pool": row.id in pool_expense_ids,
                 "shares": shares[row.id], "participants": participants[row.id]} for row in rows]


def debt_query(group_id):
    return select(DebtSplit, GroupExpense).join(GroupExpense).where(GroupExpense.group_id == group_id)


@router.get("/debts")
async def debts(group_id: int, request: Request, actor: Actor, before: int | None = None):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        query = debt_query(group_id).where((DebtSplit.debtor_id == actor.id) | (GroupExpense.creditor_id == actor.id))
        if before is not None:
            query = query.where(DebtSplit.id < before)
        rows = await sql.execute(query.order_by(DebtSplit.id.desc()).limit(100))
        cutoff = now() - timedelta(days=3)
        return [{"id": debt.id, "title": expense.title, "amount": debt.amount, "status": debt.status,
                 "debtor_id": debt.debtor_id, "creditor_id": expense.creditor_id, "proof_id": debt.proof_id,
                 "overdue": debt.status == "UNPAID" and expense.created_at < cutoff} for debt, expense in rows]


@router.get("/debts/summary")
async def debt_summary(group_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        shared_funding = await sql.scalar(select(ExpenseParticipant.expense_id).join(GroupExpense).where(
            GroupExpense.group_id == group_id, ExpenseParticipant.paid_amount > 0,
            ExpenseParticipant.user_id != GroupExpense.creditor_id).limit(1))
        cross_payment = await sql.scalar(select(GroupProof.id).where(GroupProof.group_id == group_id,
            GroupProof.transfer_amount.is_not(None), GroupProof.status.in_(("PENDING", "PAID"))).limit(1))
        if shared_funding or cross_payment:
            ledger = await simplify_group(sql, group_id)
            balance = next(item["net_balance"] for item in ledger["member_breakdown"] if item["user_id"] == actor.id)
            return {"owed": max(-balance, 0), "receivable": max(balance, 0)}
        debtor = DebtSplit.debtor_id == actor.id
        creditor = GroupExpense.creditor_id == actor.id
        owed, receivable = (await sql.execute(select(
            func.sum(case((debtor, DebtSplit.amount), else_=0)),
            func.sum(case((creditor, DebtSplit.amount), else_=0)),
        ).select_from(DebtSplit).join(GroupExpense).where(
            GroupExpense.group_id == group_id, DebtSplit.status != "PAID", debtor | creditor
        ))).one()
        return {"owed": int(owed or 0), "receivable": int(receivable or 0)}


async def payable(sql, group_id, actor_id, ids, lock=False):
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Choose unique debts")
    query = debt_query(group_id).where(DebtSplit.id.in_(ids), DebtSplit.debtor_id == actor_id).order_by(DebtSplit.id)
    rows = (await sql.execute(query.with_for_update() if lock else query)).all()
    if len(rows) != len(ids):
        raise HTTPException(404, "One or more debts were not found")
    creditors = {expense.creditor_id for _, expense in rows}
    if len(creditors) != 1 or any(debt.status != "UNPAID" for debt, _ in rows):
        raise HTTPException(409, "Choose unpaid debts owed to the same creditor")
    return rows, next(iter(creditors))


@router.get("/debts/simplified")
async def simplified(group_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        return await simplify_group(sql, group_id)


async def payment_selection(sql, group_id, actor_id, data, lock=False):
    if data.transfer:
        rows = []
        creditor_id = data.transfer.creditor_id
        total = data.transfer.amount
    else:
        rows, creditor_id = await payable(sql, group_id, actor_id, data.debt_ids, lock=lock)
        total = sum(debt.amount for debt, _ in rows)
    # Once a cross-debt transfer exists, raw invoice rows can overstate what remains payable.
    if data.transfer or await sql.scalar(select(GroupProof.id).where(GroupProof.group_id == group_id,
            GroupProof.transfer_amount.is_not(None), GroupProof.status.in_(("PENDING", "PAID"))).limit(1)):
        ledger = await simplify_group(sql, group_id)
        available = next((edge["amount"] for edge in ledger["transfers"]
            if edge["debtor_id"] == actor_id and edge["creditor_id"] == creditor_id), 0)
        if total > available or (data.transfer and total != available):
            raise HTTPException(409, "Balances changed. Refresh the simplified transfers.")
    return rows, creditor_id, total


@router.post("/debts/qr")
async def payment_qr(group_id: int, data: ReceiptIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        rows, creditor_id, total = await payment_selection(sql, group_id, actor.id, data)
        creditor = await approved_member(sql, group_id, creditor_id)
        if not qr(creditor):
            raise HTTPException(409, "The creditor has not configured VietQR")
        reference = f"G{group_id}-{rows[0][0].id}" if rows else f"G{group_id}-{actor.id}-{creditor_id}"
        return {"amount": total, "transfer_description": reference, "bank": qr(creditor), "qr_url": image_url(creditor.bank_bin, creditor.bank_account_no,
                    creditor.bank_account_name, total, reference)}


@router.post("/debts/remind")
async def remind(group_id: int, data: DebtSelection | ReminderTransfer, request: Request, actor: Actor):
    cutoff = now() - timedelta(days=1)
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        group_name = (await sql.get(LivingGroup, group_id)).name
        ledger = await simplify_group(sql, group_id)
        owed = {edge["debtor_id"]: edge["amount"] for edge in ledger["transfers"] if edge["creditor_id"] == actor.id}
        rows = []
        if isinstance(data, ReminderTransfer):
            if owed.get(data.debtor_id) != data.amount:
                raise HTTPException(409, "Balances changed. Refresh the simplified transfers.")
            candidates = {data.debtor_id}
        else:
            rows = (await sql.execute(debt_query(group_id).where(DebtSplit.id.in_(data.debt_ids),
                GroupExpense.creditor_id == actor.id).order_by(DebtSplit.id).with_for_update())).all()
            if len(rows) != len(data.debt_ids):
                raise HTTPException(404, "One or more receivables were not found")
            candidates = {debt.debtor_id for debt, _ in rows if debt.status == "UNPAID" and debt.debtor_id in owed
                          and (debt.reminded_at is None or debt.reminded_at < cutoff)}
        recipients = set()
        for debtor_id in sorted(candidates):
            cooldown = await sql.get(ReminderCooldown, (group_id, actor.id, debtor_id), with_for_update=True)
            if cooldown and cooldown.reminded_at >= cutoff:
                continue
            if not cooldown:
                cooldown = ReminderCooldown(group_id=group_id, creditor_id=actor.id, debtor_id=debtor_id)
                sql.add(cooldown)
            cooldown.reminded_at = now()
            recipients.add(debtor_id)
        for debt, _ in rows:
            if debt.debtor_id in recipients and debt.status == "UNPAID":
                debt.reminded_at = now()
        if recipients:
            sql.add(GroupReminder(group_id=group_id, sender_id=actor.id, sender_name=actor.name,
                                  debtor_ids=sorted(recipients)))
    if recipients:
        request.app.state.ws.publish({"type": "group_debt_reminder", "group_id": group_id, "group_name": group_name}, recipients)
    return {"notified": len(recipients)}


@router.post("/proofs/upload")
async def receipt_ticket(group_id: int, data: ReceiptUpload, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        await payment_selection(sql, group_id, actor.id, data)
    return await upload_ticket(actor.id, f"group-receipts/{group_id}", data)


@router.post("/proofs", status_code=201)
async def submit_receipt(group_id: int, data: ReceiptSubmit, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        existing = await sql.scalar(select(GroupProof).where(GroupProof.upload_id == str(data.upload_id),
            GroupProof.group_id == group_id, GroupProof.debtor_id == actor.id).with_for_update())
        if existing:
            if (sorted(data.debt_ids or []) != existing.debt_ids or (data.note or None) != existing.note
                    or (data.transfer and (existing.creditor_id, existing.transfer_amount)
                        != (data.transfer.creditor_id, data.transfer.amount))):
                raise HTTPException(409, "Upload already submitted with different details")
            return {"id": existing.id, "status": existing.status}
        rows, creditor_id, total = await payment_selection(sql, group_id, actor.id, data, lock=True)
        await approved_member(sql, group_id, creditor_id, lock=True)
        key = await finalize(actor.id, f"group-receipts/{group_id}", data.upload_id,
                             PROOF_IMAGE_TYPES, GROUP_PROOF_MAX_BYTES)
        proof = GroupProof(group_id=group_id, debtor_id=actor.id, creditor_id=creditor_id,
                           transfer_amount=total if data.transfer else None, note=data.note or None,
                           upload_id=str(data.upload_id), object_key=key, debt_ids=sorted(data.debt_ids or []))
        sql.add(proof)
        await sql.flush()
        for debt, _ in rows:
            debt.status = "PENDING"
            debt.proof_id = proof.id
        sql.add(GroupEvent(proof_id=proof.id))
        return {"id": proof.id, "status": proof.status}


async def authorized_proof(sql, group_id, proof_id, actor_id, *, review=False):
    await approved_member(sql, group_id, actor_id)
    query = select(GroupProof).where(GroupProof.id == proof_id, GroupProof.group_id == group_id)
    proof = await sql.scalar(query.with_for_update() if review else query)
    if not proof or (not review and actor_id not in (proof.creditor_id, proof.debtor_id)):
        raise HTTPException(404, "Receipt not found")
    return proof


@router.get("/proofs/{proof_id}")
async def get_receipt(group_id: int, proof_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        proof = await authorized_proof(sql, group_id, proof_id, actor.id)
        return {"url": await download(proof.object_key, namespace=f"group-receipts/{group_id}", user_id=proof.debtor_id), "status": proof.status, "reject_reason": proof.reject_reason, "note": proof.note}


@router.patch("/proofs/{proof_id}/verify")
async def verify_receipt(group_id: int, proof_id: int, data: VerifyIn, request: Request, actor: Actor):
    status = "PAID" if data.decision == "APPROVE" else "REJECTED"
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        proof = await authorized_proof(sql, group_id, proof_id, actor.id, review=True)
        if proof.creditor_id != actor.id:
            raise HTTPException(403, "Only the creditor can verify this receipt")
        if status == "REJECTED" and data.reject_reason is None:
            raise HTTPException(422, "Enter a reason for rejection")
        reason = data.reject_reason if status == "REJECTED" else None
        if proof.status == status and proof.reject_reason == reason:
            return {"status": status}
        if proof.status != "PENDING":
            raise HTTPException(409, "Receipt already decided")
        rows = (await sql.execute(debt_query(group_id).where(DebtSplit.proof_id == proof.id)
                                  .order_by(DebtSplit.id).with_for_update())).all()
        if proof.transfer_amount is None and (not rows or [debt.id for debt, _ in rows] != sorted(proof.debt_ids)
                or any(expense.creditor_id != actor.id or debt.debtor_id != proof.debtor_id
                       or debt.status != "PENDING" for debt, expense in rows)):
            raise HTTPException(409, "Receipt debts no longer match")
        proof.status = status
        proof.reject_reason = reason
        for debt, _ in rows:
            debt.status = "PAID" if status == "PAID" else "UNPAID"
            if status == "REJECTED":
                debt.proof_id = None
        sql.add(GroupEvent(proof_id=proof.id))
    return {"status": status}

async def fund_balance(sql, group_id, cutoff=None):
    query = select(func.sum(GroupFundTransaction.amount)).where(GroupFundTransaction.group_id == group_id)
    if cutoff is not None:
        query = query.where(GroupFundTransaction.created_at < cutoff)
    return int(await sql.scalar(query) or 0)


@router.get("/fund")
async def get_fund(group_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        rows = (await sql.scalars(select(GroupFundTransaction).where(
            GroupFundTransaction.group_id == group_id).order_by(GroupFundTransaction.id.desc()).limit(100))).all()
        return {"balance": await fund_balance(sql, group_id), "entries": rows}


@router.post("/fund/deposit", status_code=201)
async def deposit(group_id: int, data: FundDeposit, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        row = GroupFundTransaction(group_id=group_id, user_id=actor.id,
            tx_type="deposit", amount=data.amount, note=data.note)
        sql.add(row)
        await sql.flush()
        return {"id": row.id}


async def scoped_template(sql, group_id, template_id):
    template = await sql.scalar(select(GroupExpenseTemplate).where(
        GroupExpenseTemplate.group_id == group_id, GroupExpenseTemplate.id == template_id))
    if template is None:
        raise HTTPException(404, "Không tìm thấy mẫu chi tiêu.")
    return template


@router.get("/expense-templates")
async def list_templates(group_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        return (await sql.scalars(select(GroupExpenseTemplate).where(
            GroupExpenseTemplate.group_id == group_id).order_by(GroupExpenseTemplate.title))).all()


@router.post("/expense-templates", status_code=201)
async def create_template(group_id: int, data: ExpenseTemplateIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        for user_id in set(data.participants_preset_json):
            await approved_member(sql, group_id, user_id)
        row = GroupExpenseTemplate(group_id=group_id, **data.model_dump())
        sql.add(row)
        await sql.flush()
        return {"id": row.id}


@router.delete("/expense-templates/{template_id}", status_code=204)
async def delete_template(group_id: int, template_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        group = await sql.get(LivingGroup, group_id)
        if group.owner_id != actor.id:
            raise HTTPException(403, "Chỉ chủ nhóm được xóa mẫu.")
        await sql.delete(await scoped_template(sql, group_id, template_id))


@router.post("/fund/adjustment", status_code=201)
async def adjust_fund(group_id: int, data: FundAdjustment, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        group = await sql.get(LivingGroup, group_id)
        if group.owner_id != actor.id:
            raise HTTPException(403, "Chỉ chủ nhóm được điều chỉnh quỹ.")
        if await fund_balance(sql, group_id) + data.amount < 0:
            raise HTTPException(409, "Điều chỉnh này sẽ làm số dư quỹ bị âm.")
        row = GroupFundTransaction(group_id=group_id, user_id=actor.id,
            tx_type="adjustment", amount=data.amount, note=data.note)
        sql.add(row)
        await sql.flush()
        return {"id": row.id}
