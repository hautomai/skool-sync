"""Output sinks for synced member data."""

from .base import Sink
from .google_sheets_sink import GoogleSheetsSink
from .noop_sink import NoOpSink

__all__ = ["Sink", "GoogleSheetsSink", "NoOpSink"]
