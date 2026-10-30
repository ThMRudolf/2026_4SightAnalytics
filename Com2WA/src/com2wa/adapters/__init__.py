"""Ready-made signal mappings for the 4Sight dashboards. Pure functions, no extra dependencies."""
from .haas import feed_haas, haas_signals
from .ufactory import feed_ufactory, ufactory_signals

__all__ = ["feed_haas", "haas_signals", "feed_ufactory", "ufactory_signals"]
