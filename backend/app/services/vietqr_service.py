from urllib.parse import urlencode


def image_url(bank, account, name, amount, debt_id):
    query = urlencode({"amount": amount, "addInfo": f"DORM {debt_id}", "accountName": name})
    return f"https://img.vietqr.io/image/{bank}-{account}-compact2.png?{query}"
