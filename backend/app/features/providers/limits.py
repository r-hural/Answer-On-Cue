"""Every retry limit of a provider call, in one place (bounded retries).

- SDK retries for rate limits, timeouts, connection errors and 5xx (OpenAI, Anthropic, Azure).
- One app retry on invalid output for assessment and follow-ups.
- None for job steps: Resume is the learner's choice. A spending, auth or bad-request error is
  never retried. Manual "Try again" and Resume are not capped.
"""

SDK_MAX_RETRIES = 2
INVALID_OUTPUT_RETRIES = 1


def attempt_ids(base: str) -> list[str]:
    """The operation ids of one call and its retries on invalid output: base, base:r1, …"""
    return [base, *(f"{base}:r{n}" for n in range(1, INVALID_OUTPUT_RETRIES + 1))]
