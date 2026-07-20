"""
Data models for the LLM firewall.

FirewallFinding.detail must always be human-readable and already redacted —
never the raw matched secret/injection substring. See app/llm_firewall/firewall.py
for the scanning rules that produce these.
"""

from typing import Literal

from pydantic import BaseModel, Field

FirewallSeverity = Literal["critical", "high", "medium", "low"]
FirewallAction = Literal["allow", "flag", "redact", "block"]


class FirewallFinding(BaseModel):
    rule: str
    severity: FirewallSeverity
    action: FirewallAction
    detail: str
    field: str = ""


class FirewallVerdict(BaseModel):
    findings: list[FirewallFinding] = Field(default_factory=list)

    def has_blocking_finding(self) -> bool:
        return any(finding.action == "block" for finding in self.findings)

    def has_redaction(self) -> bool:
        return any(finding.action == "redact" for finding in self.findings)
