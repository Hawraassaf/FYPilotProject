import json
import os
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError


class GeminiClient:
    """
    Shared Gemini cloud AI client for FYPilot.

    Supports:
    - normal cloud generation
    - Google Search grounding when quota allows
    - graceful fallback when search quota is exhausted
    """

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is missing. Set it before starting FastAPI."
            )

        # SEC-3: an explicit per-call timeout (milliseconds) so a hung Gemini
        # request cannot block a review-pipeline attempt indefinitely -- see
        # the matching comment on GroqProvider in app/services/llm_provider.py.
        timeout_ms = int(float(os.getenv("GEMINI_TIMEOUT_SECONDS", "60")) * 1000)

        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        self.default_model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.last_error: str | None = None
        self.search_used: bool = False
        self.search_failed: bool = False

    def generate_text(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        fallback_without_search: bool = True,
    ) -> str:
        self.last_error = None
        self.search_used = False
        self.search_failed = False

        if use_search:
            try:
                response = self.client.models.generate_content(
                    model=self.default_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[
                            types.Tool(
                                google_search=types.GoogleSearch()
                            )
                        ]
                    ),
                )

                self.search_used = True
                return (response.text or "").strip()

            except (ClientError, ServerError) as ex:
                self.last_error = str(ex)
                self.search_failed = True

                if not fallback_without_search:
                    raise

        response = self.client.models.generate_content(
            model=self.default_model,
            contents=prompt,
        )

        return (response.text or "").strip()

    def generate_json(
        self,
        prompt: str,
        *,
        use_search: bool = False,
        fallback_without_search: bool = True,
    ) -> dict[str, Any]:
        text = self.generate_text(
            prompt,
            use_search=use_search,
            fallback_without_search=fallback_without_search,
        )

        try:
            return json.loads(text)
        except Exception:
            return self._extract_json(text)

    def _extract_json(self, text: str) -> dict[str, Any]:
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            raise ValueError(
                f"Gemini response did not contain valid JSON. Preview: {text[:500]}"
            )

        return json.loads(text[start:end + 1])