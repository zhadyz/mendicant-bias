"""Custom Claude provider with OAuth Bearer auth, prompt caching, and smart thinking.

Supports two authentication modes:

  1. **Standard API key** (``x-api-key`` header) -- default ``ChatAnthropic`` behaviour
  2. **Claude Code OAuth token** (``Authorization: Bearer`` header)
     - Detected by the ``sk-ant-oat`` prefix
     - Requires ``anthropic-beta: oauth-2025-04-20,claude-code-20250219``
     - Requires a billing header as the first system-prompt block

Auto-loads credentials from the user's Claude Code installation so Mendicant
Bias can run on a Claude subscription without a separate API key.

Usage::

    from mendicant_runtime.claude_model import ClaudeChatModel

    model = ClaudeChatModel(model="claude-sonnet-4-20250514", max_tokens=16384)
"""

import hashlib
import json
import logging
import os
import socket
import time
import uuid
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
THINKING_BUDGET_RATIO = 0.8

# Billing header required by the Anthropic API for OAuth token access.
# Must be the first system-prompt block.  Override via env var if the
# hardcoded version drifts from the Claude Code CLI.
_DEFAULT_BILLING_HEADER = (
    "x-anthropic-billing-header: cc_version=2.1.85.351; cc_entrypoint=cli; cch=6c6d5;"
)
OAUTH_BILLING_HEADER = os.environ.get("ANTHROPIC_BILLING_HEADER", _DEFAULT_BILLING_HEADER)


class ClaudeChatModel(ChatAnthropic):
    """``ChatAnthropic`` with OAuth Bearer auth, prompt caching, and smart thinking.

    On construction the model automatically loads credentials via
    :func:`mendicant_runtime.credentials.load_claude_credential`.  If an OAuth
    token is detected it patches the underlying Anthropic SDK client to use
    ``Authorization: Bearer`` instead of ``x-api-key`` and injects the required
    beta and billing headers.

    Config example (YAML)::

        model: claude-sonnet-4-20250514
        max_tokens: 16384
        enable_prompt_caching: true
    """

    # Custom fields ---------------------------------------------------------
    enable_prompt_caching: bool = True
    prompt_cache_size: int = 3
    auto_thinking_budget: bool = True
    retry_max_attempts: int = MAX_RETRIES
    _is_oauth: bool = False
    _oauth_access_token: str = ""

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _validate_retry_config(self) -> None:
        if self.retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be >= 1")

    def model_post_init(self, __context: Any) -> None:
        """Auto-load credentials and configure OAuth if needed."""
        from pydantic import SecretStr

        from mendicant_runtime.credentials import (
            OAUTH_ANTHROPIC_BETAS,
            is_oauth_token,
            load_claude_credential,
        )

        self._validate_retry_config()

        # Extract the current key value (SecretStr.str() returns '**********')
        current_key = ""
        if self.anthropic_api_key:
            if hasattr(self.anthropic_api_key, "get_secret_value"):
                current_key = self.anthropic_api_key.get_secret_value()
            else:
                current_key = str(self.anthropic_api_key)

        # Try Claude Code OAuth handoff when no valid key is present.
        if not current_key or current_key in ("your-anthropic-api-key",):
            cred = load_claude_credential()
            if cred:
                current_key = cred.access_token
                logger.info(
                    "Using Claude Code credential (source: %s, oauth: %s)",
                    cred.source,
                    cred.is_oauth,
                )
            else:
                logger.warning(
                    "No Anthropic API key or Claude Code OAuth credential found."
                )

        # Detect OAuth token and configure Bearer auth
        if is_oauth_token(current_key):
            self._is_oauth = True
            self._oauth_access_token = current_key
            self.anthropic_api_key = SecretStr(current_key)
            # Add required beta headers for OAuth
            self.default_headers = {
                **(self.default_headers or {}),
                "anthropic-beta": OAUTH_ANTHROPIC_BETAS,
            }
            # OAuth tokens have a limit of 4 cache_control blocks — disable
            # prompt caching to stay within the limit.
            self.enable_prompt_caching = False
            logger.info("OAuth token detected — will use Authorization: Bearer header")
        else:
            if current_key:
                self.anthropic_api_key = SecretStr(current_key)

        # Ensure the key is always SecretStr
        if isinstance(self.anthropic_api_key, str):
            self.anthropic_api_key = SecretStr(self.anthropic_api_key)

        super().model_post_init(__context)

        # Patch SDK clients immediately after creation for OAuth Bearer auth.
        # Must happen *after* super() because clients are lazily created.
        if self._is_oauth:
            self._patch_client_oauth(self._client)
            self._patch_client_oauth(self._async_client)

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def _patch_client_oauth(self, client: Any) -> None:
        """Swap ``api_key`` -> ``auth_token`` on an Anthropic SDK client."""
        if hasattr(client, "api_key") and hasattr(client, "auth_token"):
            client.api_key = None
            client.auth_token = self._oauth_access_token

    # ------------------------------------------------------------------
    # Payload overrides
    # ------------------------------------------------------------------

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Override to inject prompt caching, thinking budget, and OAuth billing."""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        if self._is_oauth:
            self._apply_oauth_billing(payload)

        if self.enable_prompt_caching:
            self._apply_prompt_caching(payload)

        if self.auto_thinking_budget:
            self._apply_thinking_budget(payload)

        return payload

    def _apply_oauth_billing(self, payload: dict) -> None:
        """Inject the billing header block required for all OAuth requests.

        The billing block is always placed first in the system list, removing
        any existing occurrence to avoid duplication or out-of-order positioning.
        """
        billing_block = {"type": "text", "text": OAUTH_BILLING_HEADER}

        system = payload.get("system")
        if isinstance(system, list):
            filtered = [
                b
                for b in system
                if not (isinstance(b, dict) and OAUTH_BILLING_HEADER in b.get("text", ""))
            ]
            payload["system"] = [billing_block] + filtered
        elif isinstance(system, str):
            if OAUTH_BILLING_HEADER in system:
                payload["system"] = [billing_block]
            else:
                payload["system"] = [billing_block, {"type": "text", "text": system}]
        else:
            payload["system"] = [billing_block]

        # Add metadata.user_id required by the API for OAuth billing validation
        if not isinstance(payload.get("metadata"), dict):
            payload["metadata"] = {}
        if "user_id" not in payload["metadata"]:
            hostname = socket.gethostname()
            device_id = hashlib.sha256(f"mendicant-bias-{hostname}".encode()).hexdigest()
            session_id = str(uuid.uuid4())
            payload["metadata"]["user_id"] = json.dumps(
                {
                    "device_id": device_id,
                    "account_uuid": "mendicant-bias",
                    "session_id": session_id,
                }
            )

    def _apply_prompt_caching(self, payload: dict) -> None:
        """Apply ephemeral ``cache_control`` to system and recent messages."""
        # Cache system messages
        system = payload.get("system")
        if system and isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["cache_control"] = {"type": "ephemeral"}
        elif system and isinstance(system, str):
            payload["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        # Cache recent messages
        messages = payload.get("messages", [])
        cache_start = max(0, len(messages) - self.prompt_cache_size)
        for i in range(cache_start, len(messages)):
            msg = messages[i]
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        block["cache_control"] = {"type": "ephemeral"}
            elif isinstance(content, str) and content:
                msg["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]

        # Cache the last tool definition
        tools = payload.get("tools", [])
        if tools and isinstance(tools[-1], dict):
            tools[-1]["cache_control"] = {"type": "ephemeral"}

    def _apply_thinking_budget(self, payload: dict) -> None:
        """Auto-allocate thinking budget (80% of ``max_tokens``)."""
        thinking = payload.get("thinking")
        if not thinking or not isinstance(thinking, dict):
            return
        if thinking.get("type") != "enabled":
            return
        if thinking.get("budget_tokens"):
            return

        max_tokens = payload.get("max_tokens", 8192)
        thinking["budget_tokens"] = int(max_tokens * THINKING_BUDGET_RATIO)

    # ------------------------------------------------------------------
    # Generation with retry logic
    # ------------------------------------------------------------------

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Override with OAuth patching and exponential-backoff retry."""
        if self._is_oauth:
            self._patch_client_oauth(self._client)

        last_error: Exception | None = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                return super()._generate(messages, stop=stop, **kwargs)
            except anthropic.RateLimitError as exc:
                last_error = exc
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, exc)
                logger.warning(
                    "Rate limited, retrying %d/%d after %dms",
                    attempt,
                    self.retry_max_attempts,
                    wait_ms,
                )
                time.sleep(wait_ms / 1000)
            except anthropic.InternalServerError as exc:
                last_error = exc
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, exc)
                logger.warning(
                    "Server error, retrying %d/%d after %dms",
                    attempt,
                    self.retry_max_attempts,
                    wait_ms,
                )
                time.sleep(wait_ms / 1000)
        raise last_error  # type: ignore[misc]

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Async override with OAuth patching and exponential-backoff retry."""
        import asyncio

        if self._is_oauth:
            self._patch_client_oauth(self._async_client)

        last_error: Exception | None = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                return await super()._agenerate(messages, stop=stop, **kwargs)
            except anthropic.RateLimitError as exc:
                last_error = exc
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, exc)
                logger.warning(
                    "Rate limited, retrying %d/%d after %dms",
                    attempt,
                    self.retry_max_attempts,
                    wait_ms,
                )
                await asyncio.sleep(wait_ms / 1000)
            except anthropic.InternalServerError as exc:
                last_error = exc
                if attempt >= self.retry_max_attempts:
                    raise
                wait_ms = self._calc_backoff_ms(attempt, exc)
                logger.warning(
                    "Server error, retrying %d/%d after %dms",
                    attempt,
                    self.retry_max_attempts,
                    wait_ms,
                )
                await asyncio.sleep(wait_ms / 1000)
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Backoff calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_backoff_ms(attempt: int, error: Exception) -> int:
        """Exponential backoff with a fixed 20% jitter buffer.

        Respects ``Retry-After`` header when present on the response.
        """
        backoff_ms = 2000 * (1 << (attempt - 1))
        jitter_ms = int(backoff_ms * 0.2)
        total_ms = backoff_ms + jitter_ms

        if hasattr(error, "response") and error.response is not None:
            retry_after = error.response.headers.get("Retry-After")
            if retry_after:
                try:
                    total_ms = int(retry_after) * 1000
                except (ValueError, TypeError):
                    pass

        return total_ms
