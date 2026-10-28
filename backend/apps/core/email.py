"""Django email backend for Amazon SES (API v2): boto3 with the task's IAM role, no SMTP keys."""

import logging

import boto3
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class SesEmailBackend(BaseEmailBackend):
    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.client = None

    def open(self):
        if self.client is None:
            self.client = boto3.client("sesv2", region_name=settings.AWS_SES_REGION_NAME)
        return True

    def close(self):
        self.client = None

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        self.open()
        sent = 0
        for message in email_messages:
            if not message.recipients():
                continue
            request = {
                "FromEmailAddress": message.from_email or settings.DEFAULT_FROM_EMAIL,
                "Destination": {"ToAddresses": message.recipients()},
                "Content": {"Raw": {"Data": message.message().as_bytes(linesep="\r\n")}},
            }
            if settings.AWS_SES_CONFIGURATION_SET:
                request["ConfigurationSetName"] = settings.AWS_SES_CONFIGURATION_SET
            try:
                self.client.send_email(**request)
                sent += 1
            except Exception:
                logger.exception("SES send failed")
                if not self.fail_silently:
                    raise
        return sent
