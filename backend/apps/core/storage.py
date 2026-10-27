"""File links for browsers."""

from django.conf import settings
from django.core.files.storage import default_storage


def signed_url(name: str, expires: int = 300) -> str:
    """
    A download link valid for `expires` seconds. On AWS this is the storage's own presigned URL.
    Locally the API reaches S3 at a Docker host name the browser can't resolve, so links are
    signed for AWS_S3_PUBLIC_ENDPOINT_URL (e.g. http://localhost:8333) instead.
    """
    public = getattr(settings, "AWS_S3_PUBLIC_ENDPOINT_URL", None)
    if not public or not hasattr(default_storage, "bucket"):
        return default_storage.url(name)
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=public,
        region_name=settings.AWS_S3_REGION_NAME,
        aws_access_key_id=default_storage.access_key,
        aws_secret_access_key=default_storage.secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": default_storage._normalize_name(name),
        },
        ExpiresIn=expires,
    )
