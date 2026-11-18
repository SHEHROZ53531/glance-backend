from abc import ABC, abstractmethod

class EmailSender(ABC):
    @abstractmethod
    def send_verification_code(self, to_email: str, code: str, purpose: str) -> None:
        pass

class ConsoleEmailSender(EmailSender):
    """Logs 6-digit codes to backend console during local dev/testing."""
    def send_verification_code(self, to_email: str, code: str, purpose: str) -> None:
        print(f"\n==========================================")
        print(f"[GLANCE EMAIL] To: {to_email}")
        print(f"[GLANCE EMAIL] Purpose: {purpose}")
        print(f"[GLANCE EMAIL] 6-Digit Code: {code} (Valid for 10 mins)")
        print(f"==========================================\n", flush=True)

def get_email_sender() -> EmailSender:
    return ConsoleEmailSender()
