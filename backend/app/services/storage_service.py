from functools import lru_cache
from io import BytesIO
from uuid import UUID, uuid4
import re
import asyncio
from threading import BoundedSemaphore
from time import monotonic
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from fastapi import HTTPException
from PIL import Image
from ..core.config import settings
from ..core.request_security import RECEIPT_LIMIT


PROOF_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
NAMESPACE = r"(?:study|proof-[1-9][0-9]*|batch-[1-9][0-9]*(?:-[1-9][0-9]*)*|group-receipts/[1-9][0-9]*)"
OBJECT_KEY = re.compile(
    rf"objects/{NAMESPACE}/[1-9][0-9]*/[0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


Image.MAX_IMAGE_PIXELS = 16_000_000
RECEIPT_KEY = re.compile(r"receipts/([1-9][0-9]*)/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(jpg|png|webp)")


def jpeg_ends_at_eoi(data):
    # Segment lengths skip metadata and thumbnails. Entropy-coded scans escape
    # literal FF bytes and can contain restart markers or another scan header.
    pos = 2
    scan = False
    while pos < len(data):
        if scan:
            pos = data.find(b"\xff", pos)
            if pos < 0:
                return False
        if data[pos] != 255:
            return False
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            return False
        marker = data[pos]
        pos += 1
        if marker == 217:
            return pos == len(data)
        if scan and (marker == 0 or 208 <= marker <= 215):
            continue
        size = int.from_bytes(data[pos:pos + 2], "big")
        if size < 2 or marker in (0, 216):
            return False
        pos += size
        scan = marker == 218
    return False


def sanitize_image(data):
    formats = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
    try:
        with Image.open(BytesIO(data), formats=list(formats)) as image:
            fmt = image.format
            if image.width * image.height > Image.MAX_IMAGE_PIXELS or getattr(image, "is_animated", False):
                raise ValueError
            if fmt == "PNG" and data.find(b"\x00\x00\x00\x00IEND\xaeB`\x82") != len(data) - 12:
                raise ValueError
            if fmt == "WEBP" and int.from_bytes(data[4:8], "little") + 8 != len(data):
                raise ValueError
            if fmt == "JPEG" and not jpeg_ends_at_eoi(data):
                raise ValueError
            image.verify()
        with Image.open(BytesIO(data), formats=[fmt]) as image:
            pixels = image.convert("RGB" if fmt == "JPEG" else "RGBA")
            pixels.info.clear()
            output = BytesIO()
            pixels.save(output, format=fmt)
        return (*formats[fmt], output.getvalue())
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "Invalid receipt image") from exc


storage_slots = BoundedSemaphore(4)


async def storage_call(operation):
    if not storage_slots.acquire(blocking=False):
        raise HTTPException(503, "Object storage busy", headers={"Retry-After": "1"})
    task = asyncio.create_task(asyncio.to_thread(operation))

    def finished(done):
        storage_slots.release()
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    try:
        async with asyncio.timeout(25):
            return await asyncio.shield(task)
    except TimeoutError as exc:
        raise HTTPException(503, "Object storage timed out") from exc


@lru_cache
def client(public=False):
    cfg = settings()
    return boto3.client("s3",
        endpoint_url=(cfg.s3_public_endpoint or cfg.s3_endpoint) if public else cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key.get_secret_value(),
        aws_secret_access_key=cfg.s3_secret_key.get_secret_value(),
        region_name=cfg.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 2}))


async def presign(operation, parameters, ttl=300):
    try:
        return await storage_call(
            lambda: client(True).generate_presigned_url(
                operation, Params={"Bucket": settings().s3_bucket, **parameters}, ExpiresIn=ttl))
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "Object storage unavailable") from exc


async def upload_ticket(user_id, namespace, spec):
    if user_id <= 0 or not re.fullmatch(NAMESPACE, namespace):
        raise HTTPException(422, "Invalid upload scope")
    filename = getattr(spec, "filename", None)
    if filename is not None and (not re.fullmatch(r"[^/\\:\x00-\x1f]{1,200}\.(?:jpe?g|png|webp)", filename, re.I)
                                 or ".." in filename):
        raise HTTPException(400, "Invalid receipt filename")
    uid = str(uuid4())
    key = f"staging/{namespace}/{user_id}/{uid}"
    url = await presign("put_object", {"Key": key, "ContentType": spec.content_type,
                                       "ContentLength": spec.size})
    return {"upload_id": uid, "url": url, "expires_in": 300,
            "headers": {"Content-Type": spec.content_type, "Content-Length": str(spec.size)}}


async def finalize(user_id, namespace, upload_id, types, limit):
    bucket = settings().s3_bucket
    if user_id <= 0 or not re.fullmatch(NAMESPACE, namespace):
        raise HTTPException(422, "Invalid upload scope")
    try:
        upload_id = UUID(str(upload_id))
    except ValueError as exc:
        raise HTTPException(422, "Invalid upload ID") from exc
    source = f"staging/{namespace}/{user_id}/{upload_id}"
    # A new immutable key for each submission keeps a replayed PUT from altering accepted bytes.

    def copy():
        deadline = monotonic() + 20
        target = f"objects/{namespace}/{user_id}/{uuid4()}"
        s3 = client(False)
        head = s3.head_object(Bucket=bucket, Key=source)
        receipt = types <= PROOF_IMAGE_TYPES
        byte_limit = min(limit, RECEIPT_LIMIT) if receipt else limit
        if receipt and head["ContentLength"] > RECEIPT_LIMIT:
            raise HTTPException(413, "Receipt exceeds the upload limit")
        if head["ContentType"] not in types or not 0 < head["ContentLength"] <= limit:
            raise HTTPException(400 if receipt else 422, "Invalid upload type or size")
        if receipt:
            result = s3.get_object(Bucket=bucket, Key=source, IfMatch=head["ETag"])
            body = result["Body"]
            try:
                data = bytearray()
                while chunk := body.read(min(65_536, byte_limit - len(data) + 1)):
                    if monotonic() >= deadline:
                        raise HTTPException(503, "Object storage timed out")
                    if len(data) + len(chunk) > byte_limit:
                        raise HTTPException(413, "Receipt exceeds the upload limit")
                    data.extend(chunk)
            finally:
                body.close()
            verified_type, extension, clean = sanitize_image(data)
            if namespace.startswith("group-receipts/"):
                target = f"receipts/{namespace.removeprefix('group-receipts/')}/{uuid4()}.{extension}"
            if len(data) != head["ContentLength"] or verified_type != head["ContentType"]:
                raise HTTPException(400, "Invalid receipt image")
            s3.put_object(Bucket=bucket, Key=target, Body=clean,
                          ContentType=verified_type, ContentDisposition="attachment")
        else:
            s3.copy_object(Bucket=bucket, Key=target, CopySource={"Bucket": bucket, "Key": source},
                           CopySourceIfMatch=head["ETag"], MetadataDirective="REPLACE",
                           ContentType="application/octet-stream", ContentDisposition="attachment")
        return target

    try:
        return await storage_call(copy)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status in {404, 412}:
            raise HTTPException(409, "Upload missing or changed; upload again") from exc
        raise HTTPException(503, "Object storage unavailable") from exc
    except BotoCoreError as exc:
        raise HTTPException(503, "Object storage unavailable") from exc


async def download(key, *, namespace=None, user_id=None):
    match = RECEIPT_KEY.fullmatch(key) if isinstance(key, str) else None
    if match and namespace == f"group-receipts/{match[1]}" and isinstance(user_id, int) and user_id > 0:
        return await presign("get_object", {"Key": key})
    if (not isinstance(key, str) or not OBJECT_KEY.fullmatch(key)
            or (namespace is not None and not key.startswith(f"objects/{namespace}/{user_id}/"))):
        raise HTTPException(404, "Object unavailable")
    return await presign("get_object", {"Key": key})
