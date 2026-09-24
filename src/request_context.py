"""Request-scoped correlation metadata shared by HTTP and logging layers."""

from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="")
