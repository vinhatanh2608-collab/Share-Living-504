from datetime import timedelta, timezone
from uuid import uuid5
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select, or_
from .deps import Actor, is_owner
from ..models.mysql_models import Debt, Expense, Proof, User, now
from ..schemas.finance import (LEGACY_PROOF_MAX_BYTES, BatchSubmit, BatchUpload, DebtSplit,
                               ExpenseIn, PaymentProof, ProofDecision, ProofUpload)
from ..services.finance_service import create_expense, proof_event, review
from ..services.storage_service import PROOF_IMAGE_TYPES, upload_ticket, finalize, download
from ..services.vietqr_service import image_url

router = APIRouter(prefix="/finance", tags=["finance"])


@router.post("/expenses", status_code=201)
async def expense(data: ExpenseIn, request: Request, actor: Actor):
    return await create_expense(request.app.state.resources, actor.id, data)


async def query_debts(request, actor, after, limit, overdue):
    query = select(Debt, Expense).join(Expense, Debt.expense_id == Expense.id).where(Debt.id > after)
    if not is_owner(actor):
        query = query.where(or_(Debt.debtor_id == actor.id, Expense.creditor_id == actor.id))
    if overdue:
        query = query.where(Debt.status != "paid", Expense.due_at < now())
    async with request.app.state.resources.sql() as sql:
        rows = (await sql.execute(query.order_by(Debt.id).limit(limit))).all()
        return [DebtSplit(id=d.id, expense_id=e.id, debtor_id=d.debtor_id, creditor_id=e.creditor_id,
                          amount=d.amount, status=d.status, title=e.title, due_at=e.due_at.replace(tzinfo=timezone.utc))
                for d, e in rows]


@router.get("/debts", response_model=list[DebtSplit])
async def debts(request: Request, actor: Actor, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return await query_debts(request, actor, after, limit, False)


@router.get("/overdue", response_model=list[DebtSplit])
async def overdue(request: Request, actor: Actor, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    return await query_debts(request, actor, after, limit, True)


@router.post("/debts/{debt_id}/reminder", status_code=202)
async def remind(debt_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        debt = await sql.scalar(select(Debt).where(Debt.id == debt_id).with_for_update())
        expense = await sql.get(Expense, debt.expense_id) if debt else None
        if not expense or (actor.id != expense.creditor_id and not is_owner(actor)):
            raise HTTPException(404, "Debt not found")
        when = now()
        if debt.status != "open" or expense.due_at >= when:
            raise HTTPException(409, "Only open overdue debts can be reminded")
        if debt.reminded_at and debt.reminded_at > when - timedelta(hours=24):
            raise HTTPException(429, "Already reminded within 24 hours")
        debt.reminded_at = when
        debtor = debt.debtor_id
    request.app.state.ws.publish({"type": "overdue_reminder", "debt_id": debt_id}, [debtor])
    return {"debt_id": debt_id, "reminded_at": when.replace(tzinfo=timezone.utc)}


@router.get("/debts/{debt_id}/vietqr")
async def vietqr(debt_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        debt = await sql.get(Debt, debt_id)
        if not debt:
            raise HTTPException(404, "Debt not found")
        expense = await sql.get(Expense, debt.expense_id)
        if actor.id not in (debt.debtor_id, expense.creditor_id) and not is_owner(actor):
            raise HTTPException(404, "Debt not found")
        creditor = await sql.get(User, expense.creditor_id)
        if not creditor.bank_bin or not creditor.account_number or debt.status == "paid":
            raise HTTPException(409, "Payment details unavailable")
        return {"url": image_url(creditor.bank_bin, creditor.account_number,
                                 creditor.account_name or creditor.name, debt.amount, debt.id)}


@router.post("/debts/{debt_id}/proof-upload")
async def ticket(debt_id: int, data: ProofUpload, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        debt = await sql.get(Debt, debt_id)
        if not debt or debt.debtor_id != actor.id:
            raise HTTPException(404, "Debt not found")
        if debt.status != "open":
            raise HTTPException(409, "Debt is not open")
    return await upload_ticket(actor.id, f"proof-{debt_id}", data)


@router.post("/debts/{debt_id}/proofs", status_code=201)
async def submit(debt_id: int, data: PaymentProof, request: Request, actor: Actor):
    resources = request.app.state.resources
    upload_id = str(data.upload_id)
    async with resources.sql() as sql:
        debt = await sql.get(Debt, debt_id)
        if not debt or debt.debtor_id != actor.id:
            raise HTTPException(404, "Debt not found")
        existing = await sql.scalar(select(Proof).where(Proof.upload_id == upload_id, Proof.debt_id == debt_id))
        if existing:
            return {"id": existing.id, "status": existing.status}
        if debt.status != "open":
            raise HTTPException(409, "Debt is not open")
    key = await finalize(actor.id, f"proof-{debt_id}", upload_id, PROOF_IMAGE_TYPES, LEGACY_PROOF_MAX_BYTES)
    async with resources.sql.begin() as sql:
        debt = await sql.scalar(select(Debt).where(Debt.id == debt_id).with_for_update())
        existing = await sql.scalar(select(Proof).where(Proof.upload_id == upload_id, Proof.debt_id == debt_id))
        if existing:
            return {"id": existing.id, "status": existing.status}
        if debt.status != "open":
            raise HTTPException(409, "Debt is not open")
        expense = await sql.get(Expense, debt.expense_id)
        proof = Proof(debt_id=debt_id, upload_id=upload_id, object_key=key)
        sql.add(proof)
        debt.status = "pending"
        await sql.flush()
        proof_event(sql, proof, debt, expense.creditor_id)
        return {"id": proof.id, "status": proof.status}


@router.post("/proofs/{proof_id}/decision")
async def decide(proof_id: int, data: ProofDecision, request: Request, actor: Actor):
    return await review(request.app.state.resources, actor.id, is_owner(actor), proof_id, data.approve)


def batch_scope(ids):
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Duplicate debts")
    return "batch-" + "-".join(map(str, sorted(ids)))


async def batch_debts(sql, uid, ids, lock=False):
    query = select(Debt).where(Debt.id.in_(ids)).order_by(Debt.id)
    debts = list(await sql.scalars(query.with_for_update() if lock else query))
    if len(debts) != len(ids) or any(d.debtor_id != uid for d in debts):
        raise HTTPException(404, "Debt not found")
    expenses = {exp.id: exp for exp in await sql.scalars(
        select(Expense).where(Expense.id.in_({d.expense_id for d in debts})))}
    if len({expenses[d.expense_id].creditor_id for d in debts}) != 1:
        raise HTTPException(422, "Select debts for one creditor")
    return debts, expenses


@router.post("/proof-batches/upload")
async def batch_ticket(data: BatchUpload, request: Request, actor: Actor):
    namespace = batch_scope(data.debt_ids)
    async with request.app.state.resources.sql() as sql:
        debts, _ = await batch_debts(sql, actor.id, data.debt_ids)
        if any(d.status != "open" for d in debts):
            raise HTTPException(409, "Every debt must be open")
    return await upload_ticket(actor.id, namespace, data)


async def check_batch(sql, uid, data, lock=False):
    debts, expenses = await batch_debts(sql, uid, data.debt_ids, lock)
    scope = batch_scope(data.debt_ids)
    ids = {d.id: str(uuid5(data.upload_id, f"{scope}:debt:{d.id}")) for d in debts}
    query = select(Proof).where(Proof.upload_id.in_(ids.values())).order_by(Proof.debt_id)
    existing = list(await sql.scalars(query.with_for_update() if lock else query))
    if existing and len(existing) == len(debts):
        return debts, expenses, ids, [{"id": p.id, "debt_id": p.debt_id, "status": p.status} for p in existing]
    if existing or any(d.status != "open" for d in debts):
        raise HTTPException(409, "Every debt must be open")
    return debts, expenses, ids, None


@router.post("/proof-batches", status_code=201)
async def submit_batch(data: BatchSubmit, request: Request, actor: Actor):
    namespace = batch_scope(data.debt_ids)
    resources = request.app.state.resources
    async with resources.sql() as sql:
        _, _, _, existing = await check_batch(sql, actor.id, data)
        if existing:
            return existing
    key = await finalize(actor.id, namespace, str(data.upload_id), PROOF_IMAGE_TYPES, LEGACY_PROOF_MAX_BYTES)
    async with resources.sql.begin() as sql:
        debts, expenses, ids, existing = await check_batch(sql, actor.id, data, lock=True)
        if existing:
            return existing
        proofs = [Proof(debt_id=d.id, upload_id=ids[d.id], object_key=key) for d in debts]
        for debt in debts:
            debt.status = "pending"
        sql.add_all(proofs)
        await sql.flush()
        for debt, proof in zip(debts, proofs):
            proof_event(sql, proof, debt, expenses[debt.expense_id].creditor_id)
        return [{"id": p.id, "debt_id": p.debt_id, "status": p.status} for p in proofs]


@router.get("/proofs/pending")
async def pending(request: Request, actor: Actor, after: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    if not is_owner(actor):
        raise HTTPException(403, "Owner approval required")
    async with request.app.state.resources.sql() as sql:
        proofs = await sql.scalars(select(Proof).where(Proof.id > after, Proof.status == "pending")
                                   .order_by(Proof.id).limit(limit))
        return [{"id": p.id, "debt_id": p.debt_id,
                 "submitted_at": p.submitted_at.replace(tzinfo=timezone.utc)} for p in proofs]


@router.get("/proofs/{proof_id}")
async def proof_download(proof_id: int, request: Request, actor: Actor):
    async with request.app.state.resources.sql() as sql:
        proof = await sql.get(Proof, proof_id)
        if not proof:
            raise HTTPException(404, "Proof not found")
        debt = await sql.get(Debt, proof.debt_id)
        if actor.id != debt.debtor_id and not is_owner(actor):
            raise HTTPException(404, "Proof not found")
        key = proof.object_key
    return {"id": proof.id, "status": proof.status, "url": await download(key)}
