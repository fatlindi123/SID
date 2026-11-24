"""Small OpenAI-compatible HTTP adapter; provider credentials stay in settings."""

import asyncio
import json
import logging
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.core.config import Settings

logger = logging.getLogger("software_intelligence.ai_provider")

_GEMMA_FREE_MODEL = "google/gemma-4-26b-a4b-it:free"


class AIProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.response_model: str | None = None
        self.response_diagnostics: dict[str, Any] = {}

    async def complete(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        if not self.settings.ai_api_base_url or not self.settings.ai_model:
            raise AIProviderError(
                "ai_provider_not_configured",
                "AI analysis is not configured. Set AI_API_BASE_URL and AI_MODEL; set AI_API_KEY if the provider requires it.",
            )
        return await asyncio.to_thread(
            self._complete_sync, system_prompt, user_payload, response_schema
        )

    def _complete_sync(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        response_schema: dict[str, Any] | None,
    ) -> str:
        base_url = self.settings.ai_api_base_url.rstrip("/")
        try:
            parsed = urlsplit(base_url)
        except ValueError as exc:
            raise AIProviderError(
                "ai_provider_configuration_invalid",
                "AI_API_BASE_URL must be a valid HTTP or HTTPS URL.",
            ) from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise AIProviderError(
                "ai_provider_configuration_invalid",
                "AI_API_BASE_URL must be an HTTP or HTTPS URL without embedded credentials.",
            )
        endpoint = base_url if parsed.path.rstrip("/").endswith("/chat/completions") else f"{base_url}/chat/completions"
        is_openrouter = parsed.hostname.lower() == "openrouter.ai"
        uses_json_schema = (
            response_schema is not None
            and is_openrouter
            and self.settings.ai_model != _GEMMA_FREE_MODEL
        )
        if uses_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "software_intelligence_analysis",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        payload: dict[str, Any] = {
            "model": self.settings.ai_model,
            "temperature": 0.2,
            "response_format": response_format,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        if response_schema is not None and is_openrouter:
            payload["provider"] = {"require_parameters": True}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.ai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ai_api_key.get_secret_value()}"
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        self.response_model = None
        self.response_diagnostics = {
            "response_type": "unknown",
            "finish_reason": None,
            "model": None,
            "requested_model": (self.settings.ai_model or "").replace("\r", "").replace("\n", "")[:200],
            "response_length": 0,
            "has_reasoning": False,
            "has_refusal": False,
            "has_parsed_field": False,
            "validation_error": None,
        }
        response_body = b""
        try:
            with urlopen(request, timeout=self.settings.ai_request_timeout_seconds) as response:
                response_body = response.read()
                envelope = json.loads(response_body.decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                raise AIProviderError(
                    "ai_provider_rate_limited", "The AI provider rate limited this analysis. Retry later."
                ) from exc
            if exc.code in {401, 403}:
                raise AIProviderError(
                    "ai_provider_authentication_failed", "The AI provider rejected its configured credentials."
                ) from exc
            logger.warning("ai_provider_http_error", extra={"status_code": exc.code})
            raise AIProviderError(
                "ai_provider_request_failed", f"The AI provider returned HTTP {exc.code}."
            ) from exc
        except (TimeoutError, URLError, OSError) as exc:
            logger.warning("ai_provider_connection_failed", extra={"error_type": type(exc).__name__})
            raise AIProviderError(
                "ai_provider_unavailable", "The AI provider could not be reached or timed out."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            self.response_diagnostics.update(
                response_type="invalid_json_envelope",
                response_length=len(response_body),
                validation_error="invalid_json_envelope",
            )
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_invalid_response", "The AI provider returned a malformed response."
            ) from exc

        reported_model = envelope.get("model") if isinstance(envelope, dict) else None
        if isinstance(reported_model, str) and reported_model.strip():
            safe_model = reported_model.strip().replace("\r", "").replace("\n", "")[:200]
            self.response_model = safe_model
            self.response_diagnostics["model"] = safe_model
        self.response_diagnostics["response_length"] = len(response_body)

        if not isinstance(envelope, dict):
            self.response_diagnostics["response_type"] = type(envelope).__name__
            self.response_diagnostics["validation_error"] = "expected_object_envelope"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_invalid_response", "The AI provider returned a malformed response."
            )

        if envelope.get("error") is not None:
            self.response_diagnostics["response_type"] = "error_envelope"
            self.response_diagnostics["validation_error"] = "provider_error_envelope"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_invalid_response", "The AI provider returned an error response."
            )

        choices = envelope.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            self.response_diagnostics["response_type"] = "missing_choices"
            self.response_diagnostics["validation_error"] = "missing_choices"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_invalid_response", "The AI provider returned a malformed response."
            )

        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str):
            self.response_diagnostics["finish_reason"] = finish_reason[:80]
        message = choice.get("message")
        if not isinstance(message, dict):
            self.response_diagnostics["response_type"] = "missing_message"
            self.response_diagnostics["validation_error"] = "missing_message"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_invalid_response", "The AI provider returned a malformed response."
            )

        self.response_diagnostics["has_reasoning"] = bool(
            message.get("reasoning") or message.get("reasoning_details")
        )
        self.response_diagnostics["has_refusal"] = bool(message.get("refusal"))
        self.response_diagnostics["has_parsed_field"] = "parsed" in message

        content = message.get("content")
        self.response_diagnostics["response_type"] = type(content).__name__
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                    self.response_diagnostics["validation_error"] = "unexpected_content_part"
                    self._log_invalid_response()
                    raise AIProviderError(
                        "ai_provider_invalid_response", "The AI provider returned an unsupported content format."
                    )
                parts.append(item["text"])
            content = "".join(parts)

        if isinstance(content, str):
            self.response_diagnostics["response_length"] = len(content)
        else:
            self.response_diagnostics["response_length"] = 0

        if self.response_diagnostics["has_refusal"]:
            self.response_diagnostics["validation_error"] = "refusal"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_refused", "The AI provider refused to generate this analysis."
            )
        if finish_reason == "length":
            self.response_diagnostics["validation_error"] = "truncated"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_truncated_response", "The AI provider response was truncated."
            )
        if not isinstance(content, str) or not content.strip():
            self.response_diagnostics["validation_error"] = "empty_content"
            self._log_invalid_response()
            raise AIProviderError(
                "ai_provider_invalid_response", "The AI provider returned an empty response."
            )

        content = self._unwrap_json_fence(content)
        self.response_diagnostics["response_length"] = len(content)
        if re.match(r"\s*<(?:think|analysis|reasoning)>", content, flags=re.IGNORECASE):
            self.response_diagnostics["has_reasoning"] = True
        return content

    @staticmethod
    def _unwrap_json_fence(content: str) -> str:
        """Remove only a complete, standalone Markdown fence around JSON text."""
        stripped = content.strip()
        match = re.fullmatch(
            r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```",
            stripped,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return match.group(1).strip() if match else content

    def _log_invalid_response(self) -> None:
        # This context contains metadata only. Never include the provider body or request.
        logger.warning(
            "ai_provider_response_rejected",
            extra={
                **self.response_diagnostics,
                "validation_error": self.response_diagnostics.get("validation_error")
                or self.response_diagnostics.get("response_type", "invalid_response"),
            },
        )
