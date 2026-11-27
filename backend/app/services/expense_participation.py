from fastapi import HTTPException

from ..schemas.living import GroupExpenseUpdate, ParticipantIn


def resolve_participants(data: GroupExpenseUpdate, actor_id: int, approved: set[int]):
    """Return one allocation per member, preserving integer-dong totals."""
    funded_by_pool = getattr(data, "funded_by_pool", False)
    submitted = data.participants
    if submitted and isinstance(submitted[0], ParticipantIn):
        if any(value is not None for value in (data.payer_id, data.split_user_ids, data.custom_shares)):
            raise HTTPException(422, "Choose one participation format")
        entries = submitted
    else:
        if submitted is None:
            if data.payer_id is None or data.split_user_ids is None:
                raise HTTPException(422, "Provide participants or payer_id and split_user_ids")
            payer = data.payer_id
            ids = data.split_user_ids
            if len(ids) != len(set(ids)):
                raise HTTPException(422, "Duplicate split members")
            ids = sorted(set(ids) | {payer})
        else:
            if data.payer_id is not None or data.split_user_ids is not None:
                raise HTTPException(422, "Choose one participation format")
            payer, ids = actor_id, submitted
        if len(set(ids)) != len(ids) or len(ids) > data.amount:
            raise HTTPException(422, "Choose unique members with a positive legacy share")
        if data.custom_shares is not None and set(data.custom_shares) != set(ids):
            raise HTTPException(422, "Custom shares must cover selected members")
        entries = [ParticipantIn(user_id=uid, status="paid" if uid == payer and not funded_by_pool else "unpaid",
                                 paid_amount=data.amount if uid == payer and not funded_by_pool else 0,
                                 share_amount=(data.custom_shares or {}).get(uid)) for uid in ids]
        if payer not in ids and not funded_by_pool:
            entries.append(ParticipantIn(user_id=payer, status="paid", paid_amount=data.amount, share_amount=0))

    by_id = {entry.user_id: entry for entry in entries}
    if len(by_id) != len(entries) or not by_id.keys() <= approved:
        raise HTTPException(422, "Each participant must be a distinct approved group member")
    expected = 0 if funded_by_pool else data.amount
    if sum(entry.paid_amount for entry in entries) != expected:
        raise HTTPException(422, "Contributions must add up to the total")
    remaining = data.amount - sum(entry.share_amount or 0 for entry in entries)
    missing = sorted(entry.user_id for entry in entries if entry.status != "excluded" and entry.share_amount is None)
    if remaining < 0 or (not missing and remaining):
        raise HTTPException(422, "Shares must add up to the total")
    each, remainder = divmod(remaining, len(missing)) if missing else (0, 0)
    shares = {uid: each + int(index < remainder) for index, uid in enumerate(missing)}
    result = []
    for uid in sorted(approved):
        entry = by_id.get(uid)
        result.append({"user_id": uid, "status": entry.status if entry else "excluded",
                       "paid_amount": entry.paid_amount if entry else 0,
                       "share_amount": shares.get(uid, (entry.share_amount or 0) if entry else 0)})
    return result
