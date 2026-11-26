from functools import lru_cache
from io import BytesIO
from uuid import UUID, uuid4
import re
import asyncio
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from fastapi import HTTPException
from PIL import Image
from ..core.config import settings


PROOF_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
NAMESPACE = r"(?:study|proof-[1-9][0-9]*|batch-[1-9][0-9]*(?:-[1-9][0-9]*)*|group-receipts/[1-9][0-9]*)"
OBJECT_KEY = re.compile(
    rf"objects/{NAMESPACE}/[1-9][0-9]*/[0-9a-f]{{8}}-[0-9a-f]{{4}}-4[0-9a-f]{{3}}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


def image_type(data):
    formats = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    try:
        with Image.open(BytesIO(data), formats=list(formats)) as image:
            if image.width * image.height > 16_000_000 or getattr(image, "is_animated", False):
                return None
            image.verify()
        with Image.open(BytesIO(data), formats=list(formats)) as image:
            image.load()
            return formats[image.format]
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        return None


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
        return await asyncio.to_thread(
            lambda: client(True).generate_presigned_url(
                operation, Params={"Bucket": settings().s3_bucket, **parameters}, ExpiresIn=ttl))
    except (BotoCoreError, ClientError) as exc:
        raise HTTPException(503, "Object storage unavailable") from exc


async def upload_ticket(user_id, namespace, spec):
    if user_id <= 0 or not re.fullmatch(NAMESPACE, namespace):
        raise HTTPException(422, "Invalid upload scope")
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
    target = f"objects/{namespace}/{user_id}/{uuid4()}"

    def copy():
        s3 = client()
        head = s3.head_object(Bucket=bucket, Key=source)
        if head["ContentType"] not in types or not 0 < head["ContentLength"] <= limit:
            raise HTTPException(422, "Invalid upload type or size")
        if types <= PROOF_IMAGE_TYPES:
            result = s3.get_object(Bucket=bucket, Key=source, IfMatch=head["ETag"])
            body = result["Body"]
            try:
                data = body.read(limit + 1)
            finally:
                body.close()
            if len(data) != head["ContentLength"] or image_type(data) != head["ContentType"]:
                raise HTTPException(422, "Invalid receipt image")
            s3.put_object(Bucket=bucket, Key=target, Body=data,
                          ContentType="application/octet-stream", ContentDisposition="attachment")
        else:
            s3.copy_object(Bucket=bucket, Key=target, CopySource={"Bucket": bucket, "Key": source},
                           CopySourceIfMatch=head["ETag"], MetadataDirective="REPLACE",
                           ContentType="application/octet-stream", ContentDisposition="attachment")
        return target

    try:
        return await asyncio.to_thread(copy)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status in {404, 412}:
            raise HTTPException(409, "Upload missing or changed; upload again") from exc
        raise HTTPException(503, "Object storage unavailable") from exc
    except BotoCoreError as exc:
        raise HTTPException(503, "Object storage unavailable") from exc


async def download(key, *, namespace=None, user_id=None):
    if (not isinstance(key, str) or not OBJECT_KEY.fullmatch(key)
            or (namespace is not None and not key.startswith(f"objects/{namespace}/{user_id}/"))):
        raise HTTPException(404, "Object unavailable")
    return await presign("get_object", {"Key": key})
