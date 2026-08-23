from django.conf import settings
from django.core.mail import send_mail

from .base import EmailProvider


class DjangoEmailProvider(EmailProvider):
    """
    Sends email via Django's own mail framework, which in turn uses
    whatever EMAIL_BACKEND is configured (console backend for local
    dev, SMTP for production, in-memory for tests) -- see
    config/settings.py. This class never talks to a specific vendor
    directly; Django's SMTP backend works with any SMTP provider.
    """

    def send(self, *, to: str, subject: str, body_text: str) -> None:
        send_mail(
            subject=subject,
            message=body_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=False,
        )
