"""
ReviewContext — the explicit trust boundary shared by the LLM firewall and the
semantic Reviewer/Rewrite agents.

Authorization and DB-scoping (enforced entirely on the .NET side today — see
MentorChat.cshtml.cs's [Authorize] + `.Where(i => i.UserId == userId)` filters)
does NOT make natural-language content safe to treat as an instruction source.
That is why this context has four tiers instead of a simple trusted/untrusted
split:

- trusted_system_instructions: FYPilot's own fixed prompt text. Authored by
  this codebase, never influenced by user or database content.
- trusted_structural_context: non-free-text facts only (ids, booleans, numeric
  ratings, enums, dates, phase numbers). Nothing here can carry a natural
  -language injection payload because it isn't natural language.
- untrusted_project_text: DB-stored NATURAL LANGUAGE fields (idea description,
  roadmap task text, profile free text). Authorization already scoped these to
  the right user, but that says nothing about whether their *content* is safe.
- untrusted_user_input / untrusted_conversation_history / untrusted_existing_code
  / untrusted_retrieved_web_content / previous_model_outputs: arrived with this
  request, or is model-generated text. previous_model_outputs stays untrusted
  even when a prior output was content-approved — "approved" describes content
  quality, not instruction-following safety.
- allowed_source_metadata: ProviderChain.sources — real URLs only, never
  invented by a model, used by the URL policy check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewContext:
    agent_name: str

    trusted_system_instructions: str = ""
    trusted_structural_context: dict[str, Any] = field(default_factory=dict)

    untrusted_project_text: dict[str, str] = field(default_factory=dict)
    untrusted_user_input: str = ""
    untrusted_conversation_history: list[str] = field(default_factory=list)
    untrusted_existing_code: str | None = None
    untrusted_retrieved_web_content: list[dict[str, Any]] = field(default_factory=list)
    previous_model_outputs: list[str] = field(default_factory=list)

    allowed_source_metadata: list[dict[str, Any]] = field(default_factory=list)

    def untrusted_text_fields(self) -> dict[str, str]:
        """
        Flatten every untrusted natural-language field into a name->text mapping.
        Used by the injection scan, which must cover untrusted content only.
        """
        fields: dict[str, str] = dict(self.untrusted_project_text)
        fields["user_input"] = self.untrusted_user_input

        for index, message in enumerate(self.untrusted_conversation_history):
            fields[f"conversation_history[{index}]"] = message

        if self.untrusted_existing_code:
            fields["existing_code"] = self.untrusted_existing_code

        for index, item in enumerate(self.untrusted_retrieved_web_content):
            fields[f"retrieved_web_content[{index}]"] = str(
                item.get("snippet") or item.get("content") or item
            )

        for index, output in enumerate(self.previous_model_outputs):
            fields[f"previous_model_output[{index}]"] = output

        return fields

    def trusted_text_fields(self) -> dict[str, str]:
        """
        Flatten trusted-only fields (system instructions + structural
        context) into a name->text mapping -- the trusted_parts half of a
        GuardedCallRequest.
        """
        fields: dict[str, str] = {
            "system_instructions": self.trusted_system_instructions,
        }

        for key, value in self.trusted_structural_context.items():
            fields[f"structural_context.{key}"] = str(value)

        return fields

    def all_text_fields(self) -> dict[str, str]:
        """
        Flatten EVERY outbound field (trusted and untrusted) into a name->text
        mapping. Used by the secret scan, which must cover all outbound content
        because a secret can land inside otherwise-trusted DB data.
        """
        fields: dict[str, str] = dict(self.trusted_text_fields())
        fields.update(self.untrusted_text_fields())
        return fields
