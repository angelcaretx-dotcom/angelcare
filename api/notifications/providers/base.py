from abc import ABC, abstractmethod


class EmailProvider(ABC):
    """
    Interface every email-sending backend must implement. Business logic
    (NotificationService) only ever talks to this interface, never to a
    specific vendor's SDK -- so replacing the email vendor later means
    writing one new class here, not touching every call site.
    """

    @abstractmethod
    def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        """
        Send an email. `body_text` is always required (the plain-text
        part -- kept for accessibility and inbox deliverability, since
        text-only fallbacks are still expected by some clients and
        spam filters). `body_html` is optional: when given, the email
        is sent as multipart/alternative with the HTML as the
        preferred rendering; when omitted, the email is plain text
        only. Must raise on failure (never fail silently) -- callers
        are responsible for catching and logging it.
        """
        raise NotImplementedError
