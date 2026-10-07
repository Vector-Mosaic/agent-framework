"""Owned source integration mechanics; no task backup or workspace lifecycle service."""

from .core import ConcurrentDevelopment, ConcurrentDevelopmentError

__all__ = ["ConcurrentDevelopment", "ConcurrentDevelopmentError"]
