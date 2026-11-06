"""Verifier adapter interface. free LocalVerifier now, a paid tool can plug in later."""
from dataclasses import dataclass, field
from typing import Protocol

OK, RISKY, FAIL = "ok", "risky", "fail"


@dataclass
class VerifyResult:
    status: str                       # ok, risky, fail
    reasons: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)   # check name -> outcome, for provenance

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) or "all checks passed"


class Verifier(Protocol):
    name: str

    def verify(self, email: str, company_domain: str | None, source_status: str | None) -> VerifyResult: ...
