def shares(total, participants):
    ids = sorted(participants)
    if not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids) or total < len(ids):
        raise ValueError("Invalid split")
    base, remainder = divmod(total, len(ids))
    return {uid: base + (i < remainder) for i, uid in enumerate(ids)}


def decision(owner, status, approve):
    if not owner:
        raise PermissionError("Owner approval required")
    target = "paid" if approve else "rejected"
    if status not in {"pending", target}:
        raise ValueError("Proof already decided")
    return target
