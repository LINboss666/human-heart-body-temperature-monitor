"""PC host tool for the STM32 ECG + body-temperature course project.

Run the application with ``python -m pc_monitor`` (add ``--demo`` for synthetic
data).  The modules are import-safe without a display: only :mod:`pc_monitor.app`
and :mod:`pc_monitor.widgets` pull in Qt.
"""

__version__ = "1.0.0-phase1"
__all__ = ["__version__"]
