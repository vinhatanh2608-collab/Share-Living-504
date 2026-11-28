"""Group balances in integer VND. Pending receipts reserve a payment exactly once."""
from collections import defaultdict
from heapq import heapify, heappop, heappush

from sqlalchemy import select

from ..models.archive import DebtHistory, GroupHistory, ParticipantHistory
from ..models.living import GroupMember, GroupProof, GroupFundTransaction
from ..models.mysql_models import User


def match_balances(balances):
    debtors = [(net, uid) for uid, net in balances.items() if net < 0]
    creditors = [(-net, uid) for uid, net in balances.items() if net > 0]
    heapify(debtors)
    heapify(creditors)
    edges = []
    while debtors and creditors:
        owed, debtor = heappop(debtors)
        credit, creditor = heappop(creditors)
        amount = min(-owed, -credit)
        edges.append((debtor, creditor, amount))
        if owed + amount < 0:
            heappush(debtors, (owed + amount, debtor))
        if credit + amount < 0:
            heappush(creditors, (credit + amount, creditor))
    return edges


def dong(amount):
    return f"{amount:,}".replace(",", ".") + "đ"


async def simplify_group(sql, group_id, *, period_bounds=None):
    members = (await sql.execute(select(User.id, User.name).join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id, GroupMember.status == "APPROVED").order_by(User.id))).all()
    totals = {uid: dict(user_id=uid, name=name, total_contributed=0, total_share=0, total_settled=0)
              for uid, name in members}
    from_pool = select(GroupFundTransaction.id).where(GroupFundTransaction.expense_id == GroupHistory.id).exists()
    expense_query = select(GroupHistory).where(GroupHistory.group_id == group_id, ~from_pool)
    if period_bounds:
        start, end = period_bounds
        expense_query = expense_query.where(GroupHistory.created_at >= start, GroupHistory.created_at < end)
    matching = expense_query.with_only_columns(GroupHistory.id)
    shares = defaultdict(dict)
    for row in await sql.scalars(select(ParticipantHistory).where(ParticipantHistory.expense_id.in_(matching))):
        shares[row.expense_id][row.user_id] = (row.paid_amount, row.share_amount)
    by_expense = defaultdict(list)
    for split in await sql.scalars(select(DebtHistory).where(DebtHistory.expense_id.in_(matching))):
        by_expense[split.expense_id].append(split)
    reserved = defaultdict(int)
    pending = {}
    original_count = 0
    expenses = await sql.scalars(expense_query)
    for expense in expenses:
        rows = by_expense[expense.id]
        distribution = shares[expense.id]
        if not distribution:
            distribution[expense.creditor_id] = (expense.amount, expense.payer_share)
            distribution.update({row.debtor_id: (0, row.amount) for row in rows})
        balances = {}
        for uid, (paid, share) in distribution.items():
            if uid in totals:
                totals[uid]["total_contributed"] += paid
                totals[uid]["total_share"] += share
                balances[uid] = paid - share
        for row in rows:
            if row.status in ("PAID", "PENDING"):
                for uid, delta in ((row.debtor_id, row.amount), (expense.creditor_id, -row.amount)):
                    if uid in totals:
                        balances[uid] = balances.get(uid, 0) + delta
                        if row.status == "PAID":
                            totals[uid]["total_settled"] += delta
                        else:
                            reserved[uid] += delta
                if row.status == "PENDING":
                    card = pending.setdefault(row.proof_id, dict(proof_id=row.proof_id, debtor_id=row.debtor_id,
                                              creditor_id=expense.creditor_id, amount=0))
                    card["amount"] += row.amount
        original_count += len(match_balances(balances))
    proofs = [] if period_bounds else await sql.scalars(select(GroupProof).where(
        GroupProof.group_id == group_id, GroupProof.transfer_amount.is_not(None),
        GroupProof.status.in_(("PAID", "PENDING"))))
    for proof in proofs:
        for uid, delta in ((proof.debtor_id, proof.transfer_amount), (proof.creditor_id, -proof.transfer_amount)):
            if uid in totals:
                if proof.status == "PAID":
                    totals[uid]["total_settled"] += delta
                else:
                    reserved[uid] += delta
        if proof.status == "PENDING":
            pending[proof.id] = dict(proof_id=proof.id, debtor_id=proof.debtor_id, creditor_id=proof.creditor_id,
                                     amount=proof.transfer_amount)
    available = {}
    for uid, item in totals.items():
        net = item["total_contributed"] - item["total_share"] + item["total_settled"]
        item["net_balance"] = net
        available[uid] = net + reserved[uid]
        position = "còn thiếu" if net < 0 else "còn được nhận"
        item["explanation_summary"] = f'{item["name"]} {position} {dong(abs(net))}.' if net else f'{item["name"]} đã cân bằng.'
        item["chain_steps"] = [f'{item["name"]} đã góp {dong(item["total_contributed"])}, phần phải chia {dong(item["total_share"])}.',
                               f'Thanh toán đã xác nhận (đã trả trừ đã nhận): {dong(item["total_settled"])}.',
                               item["explanation_summary"]]
        if reserved[uid]:
            item["chain_steps"].append(f'Sau khi giữ chỗ các khoản chờ xác minh: {dong(available[uid])}.')
    transfers = []
    for debtor, creditor, amount in match_balances(available):
        a, b = totals[debtor], totals[creditor]
        transfers.append(dict(debtor_id=debtor, creditor_id=creditor, amount=amount,
            explanation_summary=f'Sau khi cấn trừ các khoản trong nhóm, {a["name"]} chuyển thẳng {dong(amount)} cho {b["name"]}.',
            chain_steps=a["chain_steps"] + b["chain_steps"]))
    return dict(transfers=transfers, original_count=original_count, simplified_count=len(transfers),
                member_breakdown=list(totals.values()), pending=list(pending.values()))
