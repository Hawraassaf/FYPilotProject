import json
import re
from typing import Any, Dict, List, Optional

import requests


class SEDocSectionLLM:
    """
    Responsible only for small Ollama section calls.

    It does NOT assemble the final document.
    It does NOT build traceability.
    It does NOT build Mermaid diagrams.

    It only asks Ollama for small sections:
    - requirements
    - use cases
    - modules
    - database
    - testing
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434/api/generate",
        default_model: str = "qwen2.5-coder:7b",
    ):
        self.ollama_url = ollama_url
        self.default_model = default_model
        self.last_error: Optional[str] = None
        self.last_raw_response: Optional[str] = None
        self.section_errors: Dict[str, str] = {}

    def generate_all_sections(self, request: Any) -> Dict[str, Any]:
        self.last_error = None
        self.last_raw_response = None
        self.section_errors = {}

        context = self._build_context(request)
        model = getattr(request, "model", None) or self.default_model

        sections: Dict[str, Any] = {}

        sections["requirements"] = self._safe_call(
            section_name="requirements",
            model=model,
            prompt=self._requirements_prompt(context),
        )

        sections["useCases"] = self._safe_call(
            section_name="useCases",
            model=model,
            prompt=self._use_cases_prompt(context),
        )

        sections["modules"] = self._safe_call(
            section_name="modules",
            model=model,
            prompt=self._modules_prompt(context),
        )

        sections["database"] = self._safe_call(
            section_name="database",
            model=model,
            prompt=self._database_prompt(context),
        )

        sections["testing"] = self._safe_call(
            section_name="testing",
            model=model,
            prompt=self._testing_prompt(context),
        )

        return sections

    def _safe_call(self, section_name: str, model: str, prompt: str) -> Dict[str, Any]:
        try:
            return self._call_ollama_json(model=model, prompt=prompt)
        except Exception as e:
            self.section_errors[section_name] = str(e)
            self.last_error = str(e)
            return {}

    def _call_ollama_json(self, model: str, prompt: str) -> Dict[str, Any]:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.15,
                "num_ctx": 4096,
                "num_predict": 1800,
            },
        }

        response = requests.post(
            self.ollama_url,
            json=payload,
            timeout=300,
        )
        response.raise_for_status()

        raw = response.json().get("response", "")
        self.last_raw_response = raw[:3000]

        json_text = self._extract_json(raw)
        return json.loads(json_text)

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError("No valid JSON object found in Ollama response.")

        return text[start:end + 1]

    def _build_context(self, request: Any) -> str:
        profile = getattr(request, "studentProfile", None)
        idea = getattr(request, "selectedIdea", None)
        roadmap = getattr(request, "roadmap", []) or []
        existing_notes = getattr(request, "existingNotes", "") or ""

        roadmap_text = "\n".join(
            [
                f"- Phase {getattr(phase, 'phaseNumber', index + 1)}: "
                f"{getattr(phase, 'name', '')} | "
                f"Objective: {getattr(phase, 'objective', '')} | "
                f"Tasks: {', '.join(getattr(phase, 'tasks', [])[:5])}"
                for index, phase in enumerate(roadmap[:8])
            ]
        )

        return f"""
Student Profile:
Major: {getattr(profile, "major", "Computer Science") if profile else "Computer Science"}
Experience Level: {getattr(profile, "experienceLevel", "intermediate") if profile else "intermediate"}
Team Size: {getattr(profile, "teamSize", 1) if profile else 1}
Available Hours Per Week: {getattr(profile, "availableHoursPerWeek", 10) if profile else 10}
Skills: {getattr(profile, "skills", []) if profile else []}
Skill Ratings: {getattr(profile, "skillRatings", {}) if profile else {}}

Selected Idea:
Title: {getattr(idea, "title", "") if idea else ""}
Problem Statement: {getattr(idea, "problemStatement", "") if idea else ""}
Target Users: {getattr(idea, "targetUsers", "") if idea else ""}
Why Useful: {getattr(idea, "whyUseful", "") if idea else ""}
Required Technologies: {getattr(idea, "requiredTechnologies", "") if idea else ""}
Required Skills: {getattr(idea, "requiredSkills", "") if idea else ""}
Missing Skills: {getattr(idea, "missingSkills", "") if idea else ""}
Difficulty Level: {getattr(idea, "difficultyLevel", "") if idea else ""}
Expected Duration Weeks: {getattr(idea, "expectedDurationWeeks", 10) if idea else 10}
Domain: {getattr(idea, "domain", "") if idea else ""}
Final Deliverables: {getattr(idea, "finalDeliverables", "") if idea else ""}

Roadmap:
{roadmap_text}

Existing Notes:
{existing_notes}
"""

    def _requirements_prompt(self, context: str) -> str:
        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

Context:
{context}

Generate software engineering requirements.

JSON shape:
{{
  "functionalRequirements": [
    {{
      "id": "FR-01",
      "title": "",
      "description": "",
      "priority": "High",
      "source": "Selected idea"
    }}
  ],
  "nonFunctionalRequirements": [
    {{
      "id": "NFR-01",
      "title": "",
      "description": "",
      "priority": "High",
      "source": "Software quality"
    }}
  ]
}}

Rules:
- Exactly 8 functional requirements.
- Exactly 5 non-functional requirements.
- FR IDs must be FR-01 to FR-08.
- NFR IDs must be NFR-01 to NFR-05.
- Requirements must be specific to the selected project.
- Do not invent unrelated features.
"""

    def _use_cases_prompt(self, context: str) -> str:
        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

Context:
{context}

Generate use cases and edge cases.

JSON shape:
{{
  "useCases": [
    {{
      "id": "UC-01",
      "title": "",
      "actor": "",
      "goal": "",
      "preconditions": [],
      "mainFlow": [],
      "alternativeFlow": [],
      "postconditions": [],
      "relatedRequirements": ["FR-01"]
    }}
  ],
  "edgeCases": [
    {{
      "id": "EC-01",
      "scenario": "",
      "expectedHandling": "",
      "relatedRequirement": "FR-01"
    }}
  ]
}}

Rules:
- Exactly 5 use cases.
- Exactly 5 edge cases.
- Use case IDs must be UC-01 to UC-05.
- Edge case IDs must be EC-01 to EC-05.
- relatedRequirements must only use FR-01 to FR-08.
"""

    def _modules_prompt(self, context: str) -> str:
        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

Context:
{context}

Generate system modules.

JSON shape:
{{
  "systemModules": [
    {{
      "id": "M-01",
      "name": "",
      "responsibility": "",
      "inputs": [],
      "outputs": [],
      "relatedRequirements": ["FR-01"]
    }}
  ]
}}

Rules:
- Exactly 5 modules.
- Module IDs must be M-01 to M-05.
- Modules must match the selected project.
- relatedRequirements must only use FR-01 to FR-08.
"""

    def _database_prompt(self, context: str) -> str:
        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

Context:
{context}

Generate database entities and relationships.

JSON shape:
{{
  "databaseEntities": [
    {{
      "name": "",
      "purpose": "",
      "importantFields": [],
      "relationships": []
    }}
  ],
  "entityRelationships": [
    {{
      "fromEntity": "",
      "toEntity": "",
      "type": "one-to-many",
      "description": ""
    }}
  ]
}}

Rules:
- Exactly 5 database entities.
- At least 4 entity relationships.
- Relationships must only use entities that exist in databaseEntities.
- Use realistic entity names for this system.
"""

    def _testing_prompt(self, context: str) -> str:
        return f"""
Return ONLY valid JSON.
No markdown.
No explanation.

Context:
{context}

Generate testing plan.

JSON shape:
{{
  "testingPlan": [
    {{
      "id": "TC-01",
      "title": "",
      "type": "Functional",
      "steps": [],
      "expectedResult": "",
      "relatedRequirements": ["FR-01"]
    }}
  ]
}}

Rules:
- Exactly 5 test cases.
- Test case IDs must be TC-01 to TC-05.
- relatedRequirements must only use FR-01 to FR-08.
- Cover authentication, idea generation, comparison, roadmap, mentor chat, and documentation when relevant.
"""