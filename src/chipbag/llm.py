"""One wrapper for every structured LLM call in the pipeline (MVDv2 §5).

- Structured output only: the response is parsed and validated against a Pydantic model.
- Every call is appended to an audit log (request, response, usage, cost).
- Cost is tracked per instance and a hard cap is enforced before each call.
- Refusals fall back server-side (`fallbacks="default"`); a refusal that survives the
  fallback raises.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# USD per million tokens: (input, output). Cache reads/writes are billed as input here,
# which slightly overstates cost; good enough for budgeting.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class BudgetExceeded(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, model: str, log_path: Path, max_cost_usd: float, concurrency: int = 4):
        self.model = model
        self.log_path = log_path
        self.max_cost_usd = max_cost_usd
        self.spent_usd = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0
        self._client = anthropic.AsyncAnthropic()
        self._sem = asyncio.Semaphore(concurrency)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def _cost(self, usage) -> float:
        p_in, p_out = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) + (
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        return (tokens_in * p_in + (usage.output_tokens or 0) * p_out) / 1_000_000

    async def parse(self, purpose: str, system: str, user: str, output: type[T], max_tokens: int = 16000,
                    attempts: int = 2) -> T:
        """One structured call. A response whose JSON fails to parse or validate (e.g. cut
        off mid-stream) is logged and retried; after `attempts` failures it raises LLMError,
        so callers can skip one chunk instead of aborting the run."""
        last_error = ""
        for attempt in range(1, attempts + 1):
            async with self._sem:
                if self.spent_usd >= self.max_cost_usd:
                    raise BudgetExceeded(f"cost cap ${self.max_cost_usd:.2f} reached (spent ${self.spent_usd:.2f})")
                started = dt.datetime.now(dt.UTC)
                response, request_id, parse_error = None, None, None
                # Streaming, so long outputs don't hit request timeouts; the SDK parses the
                # final message against `output` as with messages.parse.
                async with self._client.beta.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=output,
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                ) as stream:
                    request_id = stream.request_id
                    try:
                        response = await stream.get_final_message()
                    except ValidationError as e:
                        parse_error = str(e).splitlines()[1].strip() if "\n" in str(e) else str(e)
                        try:
                            response = stream.current_message_snapshot
                        except Exception:  # noqa: BLE001 - snapshot unavailable; log without usage
                            response = None
                if response is not None:
                    cost = self._cost(response.usage)
                    self.spent_usd += cost
                    self.input_tokens += response.usage.input_tokens or 0
                    self.output_tokens += response.usage.output_tokens or 0
                else:
                    cost = 0.0
                self.calls += 1
                self._log(purpose, user, response, cost, started, request_id, parse_error, attempt)

            if parse_error is None and response is not None:
                if response.stop_reason == "refusal":
                    raise LLMError(f"{purpose}: refused ({response.stop_details})")
                if response.stop_reason == "max_tokens":
                    raise LLMError(f"{purpose}: output truncated at max_tokens={max_tokens}")
                if response.parsed_output is not None:
                    return response.parsed_output
                parse_error = f"no parsed output (stop_reason={response.stop_reason})"
            stop = getattr(response, "stop_reason", None)
            last_error = f"{parse_error} (stop_reason={stop}, request_id={request_id})"
        raise LLMError(f"{purpose}: invalid structured output after {attempts} attempts: {last_error}")

    def _log(self, purpose: str, user: str, response, cost: float, started: dt.datetime,
             request_id: str | None, parse_error: str | None, attempt: int) -> None:
        parsed = None
        if response is not None and parse_error is None:
            parsed = response.parsed_output
        usage = getattr(response, "usage", None)
        entry = {
            "t": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "purpose": purpose,
            "model": getattr(response, "model", self.model),
            "request_id": request_id,
            "attempt": attempt,
            "stop_reason": getattr(response, "stop_reason", None),
            "parse_error": parse_error,
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "cost_usd": round(cost, 6),
            "latency_s": round((dt.datetime.now(dt.UTC) - started).total_seconds(), 2),
            "request": user,
            "response": parsed.model_dump() if parsed is not None else None,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
