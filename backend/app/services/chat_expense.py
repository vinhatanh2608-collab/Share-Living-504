import re
import unicodedata
from decimal import Decimal

from fastapi import HTTPException

AMOUNT = re.compile(r"(?<![\w.])([0-9]+(?:[.,][0-9]+)*)\s*(triệu|tr|k|nghìn|ngàn|đồng|vnd|đ)(?!\w)", re.I)


def normalize(text):
    text = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def parse_expense(text, members, sender_id, proposed_title=None):
    body = re.sub(r"^\s*/chi(?:\s+|$)", "", text, flags=re.I).strip()
    match = AMOUNT.search(body)
    if not match:
        raise HTTPException(422, "Hãy ghi số tiền, ví dụ 240k hoặc 150.000đ.")
    number, unit = match.groups()
    unit = normalize(unit)
    if unit in {"tr", "trieu", "k", "nghin", "ngan"}:
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
            number = re.sub(r"[.,]", "", number)
        elif number.count(".") + number.count(",") > 1:
            raise HTTPException(422, "Số tiền chưa rõ.")
        number = Decimal(number.replace(",", ".")) * (10**6 if unit in {"tr", "trieu"} else 1000)
    else:
        if not re.fullmatch(r"\d+|\d{1,3}(?:[.,]\d{3})+", number):
            raise HTTPException(422, "Số tiền chưa rõ.")
        number = Decimal(re.sub(r"[.,]", "", number))
    if not 0 < number <= 10**12 or number != int(number):
        raise HTTPException(422, "Số tiền phải là số đồng nguyên hợp lệ.")
    amount = int(number)
    title = (proposed_title or (body[:match.start()] + body[match.end():]).split(",")[0]).strip(" ,:-")
    paid, excluded = set(), set()
    aliases = {}
    for uid, name, username in members:
        for alias in {normalize(name), normalize(username), normalize(name).split()[-1] if name.strip() else ""} - {""}:
            aliases.setdefault(alias, set()).add(uid)
    names = "|".join(re.escape(alias) for alias in sorted(aliases, key=lambda alias: (-len(alias), alias)))
    if names:
        pattern = rf"(?<!\w)({names})\s+(khong tham gia|khong an|vang|tra|gop)\b"
        for alias, marker in re.findall(pattern, normalize(body)):
            ids = aliases[alias]
            if len(ids) != 1:
                raise HTTPException(422, "Tên thành viên bị trùng. Hãy dùng tên đăng nhập.")
            (excluded if marker.startswith("khong") or marker == "vang" else paid).update(ids)
    if paid & excluded:
        raise HTTPException(422, "Một thành viên có cả trạng thái góp tiền và không tham gia.")
    if not paid and sender_id not in excluded:
        paid.add(sender_id)
    if not paid or len(paid) > amount:
        raise HTTPException(422, "Hãy ghi rõ ai đã trả tiền.")
    shares = dict(zip(sorted(paid), (amount // len(paid) + (i < amount % len(paid)) for i in range(len(paid)))))
    return {"title": title[:200] or "Chi tiêu nhóm", "amount": amount, "total_amount": amount,
            "category": "OTHER", "participants": [
        {"user_id": uid, "name": name, "status": "excluded" if uid in excluded else "paid" if uid in paid else "unpaid",
         "paid_amount": shares.get(uid, 0)} for uid, name, _ in members]}
