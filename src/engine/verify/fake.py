from .base import VerifyResult


class FakeVerifier:
    """returns a fixed result per email, default ok. for tests."""
    name = "fake_verifier"

    def __init__(self, results: dict | None = None, default: str = "ok"):
        self.results = results or {}
        self.default = default

    def verify(self, email, company_domain=None, source_status=None) -> VerifyResult:
        status = self.results.get(email, self.default)
        return VerifyResult(status=status, reasons=[] if status == "ok" else [f"fake {status}"])
