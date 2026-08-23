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
    ) -> None:
        """
        Send an email. Must raise on failure (never fail silently) --
        callers are responsible for catching and logging it.
        """
        raise NotImplementedError
