"""Cooperative cancellation shared by turns, providers and nested tools."""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class Cancelled(Exception):
    """An operation stopped at a safe boundary, not an execution failure."""


class CancellationToken:
    def __init__(self, parent: CancellationToken | None = None, *, timeout: float | None = None,
                 event: threading.Event | None = None) -> None:
        self.parent = parent
        self.event = event if event is not None else threading.Event()
        self.deadline = time.monotonic() + timeout if timeout is not None else None
        self.reason = "user"

    def cancel(self, reason: str = "user") -> None:
        self.reason = reason
        self.event.set()

    def is_set(self) -> bool:
        return self.event.is_set() or bool(self.parent and self.parent.is_set()) or self.expired

    @property
    def expired(self) -> bool:
        return (self.deadline is not None and time.monotonic() >= self.deadline) or bool(self.parent and self.parent.expired)

    def check(self) -> None:
        if self.is_set():
            raise Cancelled("deadline" if self.expired else self.reason)

    def wait(self, seconds: float) -> bool:
        end = time.monotonic() + max(0, seconds)
        while not self.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            self.event.wait(min(remaining, 0.05))
        return True


_current: ContextVar[CancellationToken | None] = ContextVar("openmuse_cancellation", default=None)


def current_token() -> CancellationToken | None:
    return _current.get()


@contextmanager
def cancellation_scope(token: CancellationToken) -> Iterator[CancellationToken]:
    binding = _current.set(token)
    try:
        token.check()
        yield token
    finally:
        _current.reset(binding)
