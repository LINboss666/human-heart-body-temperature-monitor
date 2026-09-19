"""Reusable Qt widgets for the monitor window.

Nothing in here owns data: each widget exposes setters that take plain values,
so :mod:`pc_monitor.app` stays the single place where protocol state becomes
screen state, and each widget can be reparented or restyled without dragging
protocol logic with it.
"""

from .connection_panel import ConnectionPanel
from .ecg_plot import EcgPane
from .metric_cards import MetricCard, MetricStrip
from .status_bar import StatusStrip

__all__ = ["ConnectionPanel", "EcgPane", "MetricCard", "MetricStrip", "StatusStrip"]
