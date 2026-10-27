import time

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the S3 bucket if it doesn't exist (local S3-compatible storage). Safe to repeat."

    def add_arguments(self, parser):
        parser.add_argument("--retries", type=int, default=15, help="Wait for storage to start.")

    def handle(self, *args, retries, **options):
        if not hasattr(default_storage, "bucket"):
            self.stdout.write("Default storage is not S3; nothing to do.")
            return
        client = default_storage.connection.meta.client
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        for attempt in range(retries):
            try:
                existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
                if bucket not in existing:
                    client.create_bucket(Bucket=bucket)
                    self.stdout.write(self.style.SUCCESS(f"Created bucket {bucket}"))
                else:
                    self.stdout.write(f"Bucket {bucket} exists")
                return
            except Exception as exc:  # storage still starting
                if attempt == retries - 1:
                    raise CommandError(f"Storage not reachable: {exc}") from exc
                time.sleep(2)
