"""Stateless domain services consumed by use cases (clock, hashing, ...)."""

from sdn_controller.core.services.clock import Clock, SystemClock

__all__ = ["Clock", "SystemClock"]
