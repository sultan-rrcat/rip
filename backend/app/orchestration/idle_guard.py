"""Idle-turn guard for the L3 ReAct loop.

The ReAct loop can spin on turns that produce no observation (provider
errors, unknown executors, malformed inputs, repeats of failed actions).
This module concentrates that guard in one place: each non-progress turn
calls `record_idle()`, each executed step or final answer calls
`record_progress()`. When consecutive idle turns reach the limit,
`record_idle()` returns True and the caller breaks out of the loop.

Previously this pattern was inlined at 10 guard sites inside `run_react`
(`idle_turns += 1; if idle_turns >= 2`), with the threshold hardcoded
everywhere and the reset in two places — a new guard had to remember
both halves.
"""

from __future__ import annotations

#: Consecutive non-progress turns before the loop fails fast instead of
#: burning the remaining iteration budget on identical errors.
MAX_IDLE_TURNS = 2


class IdleGuard:
    """Counts consecutive idle turns; signals when the loop should break."""

    def __init__(self, max_idle_turns: int = MAX_IDLE_TURNS):
        self._idle = 0
        self._max = max_idle_turns

    def record_idle(self) -> bool:
        """Record one non-progress turn. Returns True when the loop should break."""
        self._idle += 1
        return self._idle >= self._max

    def record_progress(self) -> None:
        """Reset the counter — an executed step or final answer is progress."""
        self._idle = 0

    @property
    def idle_turns(self) -> int:
        """Current consecutive idle-turn count (for observability/tests)."""
        return self._idle
