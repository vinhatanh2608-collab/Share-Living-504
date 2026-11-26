from datetime import timedelta
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from .deps import Actor
from .routes_living import approved_member, qr
from ..models.living import DebtSplit, GroupEvent, GroupExpense, GroupMember, GroupProof, LivingGroup
from ..models.mysql_models import now
from ..schemas.living import GROUP_PROOF_MAX_BYTES, GroupExpenseIn, ReceiptIn, ReceiptSubmit, ReceiptUpload, VerifyIn
from ..services.storage_service import PROOF_IMAGE_TYPES, download, finalize, upload_ticket
from ..services.vietqr_service import image_url

router = APIRouter(prefix="/groups/{group_id}", tags=["group-finance"])


def split_expense(data: GroupExpenseIn):
    ids = sorted(set(data.participants))
    if len(ids) != len(data.participants) or data.amount < len(ids):
        raise HTTPException(422, "Choose unique participants and a positive amount per participant")
    if data.custom_shares is not None:
        if set(data.custom_shares) != set(ids) or sum(data.custom_shares.values()) != data.amount:
            raise HTTPException(422, "Custom shares must cover the participants and add up to the expense")
        return data.custom_shares
    each, remainder = divmod(data.amount, len(ids))
    return {user_id: each + (index < remainder) for index, user_id in enumerate(ids)}


async def lock_group(sql, group_id, actor_id):
    if not await sql.get(LivingGroup, group_id, with_for_update=True):
        raise HTTPException(404, "Group unavailable")
    await approved_member(sql, group_id, actor_id, lock=True)


@router.post("/expenses", status_code=201)
async def log_expense(group_id: int, data: GroupExpenseIn, request: Request, actor: Actor):
    shares = split_expense(data)
    fingerprint = sha256(data.model_dump_json().encode()).hexdigest()
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        prior = await sql.scalar(select(GroupExpense).where(GroupExpense.group_id == group_id,
            GroupExpense.creditor_id == actor.id, GroupExpense.request_id == str(data.request_id)).with_for_update())
        if prior:
            if prior.request_hash != fingerprint:
                raise HTTPException(409, "Request ID already used for a different expense")
            return {"id": prior.id}
        required = set(shares) | {actor.id}
        members = await sql.scalars(select(GroupMember).where(GroupMember.group_id == group_id,
            GroupMember.user_id.in_(required), GroupMember.status == "APPROVED").with_for_update())
        eligible = {member.user_id for member in members if member.bank_bin}
        if eligible != required:
            raise HTTPException(422, "The payer and all participants need approved membership and a group VietQR")
        expense = GroupExpense(group_id=group_id, creditor_id=actor.id, request_id=str(data.request_id),
            request_hash=fingerprint, title=data.title, category=data.category, amount=data.amount,
            payer_share=shares.get(actor.id, 0))
        sql.add(expense)
        await sql.flush()
        sql.add_all(DebtSplit(expense_id=expense.id, debtor_id=user_id, amount=amount)
                    for user_id, amount in shares.items() if user_id != actor.id)
        return {"id": expense.id}


@router.get("/expenses")
async def expenses(group_id: int, request: Request, actor: Actor, before: int | None = None):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        query = select(GroupExpense).where(GroupExpense.group_id == group_id)
        if before is not None:
            query = query.where(GroupExpense.id < before)
        rows = await sql.scalars(query.order_by(GroupExpense.id.desc()).limit(100))
        return [{key: getattr(row, key) for key in ("id", "creditor_id", "title", "category", "amount", "created_at")}
                for row in rows]


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


@router.post("/debts/qr")
async def payment_qr(group_id: int, data: ReceiptIn, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        rows, creditor_id = await payable(sql, group_id, actor.id, data.debt_ids)
        creditor = await approved_member(sql, group_id, creditor_id)
        if not qr(creditor):
            raise HTTPException(409, "The creditor has not configured VietQR")
        total = sum(debt.amount for debt, _ in rows)
        reference = f"G{group_id}-{rows[0][0].id}"
        return {"amount": total, "qr_url": image_url(creditor.bank_bin, creditor.bank_account_no,
                    creditor.bank_account_name, total, reference)}


@router.post("/debts/remind")
async def remind(group_id: int, data: ReceiptIn, request: Request, actor: Actor):
    cutoff = now() - timedelta(days=1)
    async with request.app.state.resources.sql.begin() as sql:
        await approved_member(sql, group_id, actor.id)
        rows = (await sql.execute(debt_query(group_id).where(DebtSplit.id.in_(data.debt_ids),
            GroupExpense.creditor_id == actor.id).order_by(DebtSplit.id).with_for_update())).all()
        if len(rows) != len(data.debt_ids):
            raise HTTPException(404, "One or more receivables were not found")
        recipients = set()
        group_name = (await sql.get(LivingGroup, group_id)).name
        approved = set(await sql.scalars(select(GroupMember.user_id).where(
            GroupMember.group_id == group_id, GroupMember.status == "APPROVED",
            GroupMember.user_id.in_({debt.debtor_id for debt, _ in rows}))))
        for debt, _ in rows:
            if (debt.debtor_id in approved and debt.status == "UNPAID"
                    and (debt.reminded_at is None or debt.reminded_at < cutoff)):
                debt.reminded_at = now()
                recipients.add(debt.debtor_id)
    if recipients:
        request.app.state.ws.publish({"type": "group_debt_reminder", "group_id": group_id, "group_name": group_name}, recipients)
    return {"notified": len(recipients)}


@router.post("/proofs/upload")
async def receipt_ticket(group_id: int, data: ReceiptUpload, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        await approved_member(sql, group_id, actor.id)
        await payable(sql, group_id, actor.id, data.debt_ids)
    return await upload_ticket(actor.id, f"group-receipts/{group_id}", data)


@router.post("/proofs", status_code=201)
async def submit_receipt(group_id: int, data: ReceiptSubmit, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        existing = await sql.scalar(select(GroupProof).where(GroupProof.upload_id == str(data.upload_id),
            GroupProof.group_id == group_id, GroupProof.debtor_id == actor.id).with_for_update())
        if existing:
            if sorted(data.debt_ids) != existing.debt_ids:
                raise HTTPException(409, "Upload already submitted with different debts")
            return {"id": existing.id, "status": existing.status}
        rows, creditor_id = await payable(sql, group_id, actor.id, data.debt_ids, lock=True)
        await approved_member(sql, group_id, creditor_id, lock=True)
        key = await finalize(actor.id, f"group-receipts/{group_id}", data.upload_id,
                             PROOF_IMAGE_TYPES, GROUP_PROOF_MAX_BYTES)
        proof = GroupProof(group_id=group_id, debtor_id=actor.id, creditor_id=creditor_id,
                           upload_id=str(data.upload_id), object_key=key, debt_ids=sorted(data.debt_ids))
        sql.add(proof)
        await sql.flush()
        for debt, _ in rows:
            debt.status = "PENDING"
            debt.proof_id = proof.id
        sql.add(GroupEvent(proof_id=proof.id))
        return {"id": proof.id, "status": proof.status}


async def authorized_proof(sql, group_id, proof_id, actor_id, lock=False):
    await approved_member(sql, group_id, actor_id)
    proof = await sql.get(GroupProof, proof_id, with_for_update=lock)
    if not proof or proof.group_id != group_id or actor_id not in (proof.creditor_id, proof.debtor_id):
        raise HTTPException(404, "Receipt not found")
    return proof


@router.get("/proofs/{proof_id}")
async def get_receipt(group_id: int, proof_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        proof = await authorized_proof(sql, group_id, proof_id, actor.id)
        return {"url": await download(proof.object_key, namespace=f"group-receipts/{group_id}", user_id=proof.debtor_id), "status": proof.status, "reject_reason": proof.reject_reason}


@router.patch("/proofs/{proof_id}/verify")
async def verify_receipt(group_id: int, proof_id: int, data: VerifyIn, request: Request, actor: Actor):
    if data.decision == "REJECT" and data.reject_reason is None:
        raise HTTPException(422, "Enter a reason for rejection")
    status = "PAID" if data.decision == "APPROVE" else "REJECTED"
    async with request.app.state.resources.sql.begin() as sql:
        await lock_group(sql, group_id, actor.id)
        proof = await authorized_proof(sql, group_id, proof_id, actor.id, lock=True)
        if proof.creditor_id != actor.id:
            raise HTTPException(403, "Only the creditor can verify this receipt")
        reason = data.reject_reason if status == "REJECTED" else None
        if proof.status == status and proof.reject_reason == reason:
            return {"status": status}
        if proof.status != "PENDING":
            raise HTTPException(409, "Receipt already decided")
        rows = (await sql.execute(debt_query(group_id).where(DebtSplit.proof_id == proof.id)
                                  .order_by(DebtSplit.id).with_for_update())).all()
        if (not rows or [debt.id for debt, _ in rows] != sorted(proof.debt_ids)
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
