"""Fixed-capacity numeric ring buffers feeding the realtime plot.

Two rules shape this module:

* **Bounded memory.**  Every buffer is preallocated at construction; the plot can
  never grow the heap by receiving data.  Oldest samples are overwritten and the
  overwrites are counted, because "the plot forgot the beginning" must be
  distinguishable from "the device never sent it".
* **The writer is never the repainter.**  ``extend()`` is O(n) on the incoming
  block and ``snapshot()`` is one numpy copy, both under a lock.  A worker thread
  appends as data arrives; only the 20-30 FPS timer reads.  Neither blocks the
  other for longer than a microsecond-scale copy.

The sample *index* is stored alongside the value so the x axis is rebuilt from
``first_sample_index`` (a true 1 kHz axis) rather than from a host-side sample
count that would silently compress every dropout into a straight line.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Iterator

import numpy as np

__all__ = ["RingBuffer", "IndexedRingBuffer", "EventRateMeter", "IntAccumulator"]


class RingBuffer:
    """A fixed-capacity, oldest-truncated numeric buffer.

    ``dtype`` defaults to ``float64`` because pyqtgraph wants float arrays and
    because the plotted quantity (pin millivolts) is fractional.
    """

    __slots__ = ("_cap", "_data", "_head", "_filled", "_dropped", "_lock", "dtype")

    def __init__(self, capacity: int, dtype: np.dtype = np.float64) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive, got %r" % (capacity,))
        self._cap = int(capacity)
        self.dtype = np.dtype(dtype)
        self._data = np.zeros(self._cap, dtype=self.dtype)
        self._head = 0  # index of the next write
        self._filled = 0
        self._dropped = 0
        self._lock = threading.Lock()

    # -- geometry ----------------------------------------------------------
    @property
    def capacity(self) -> int:
        return self._cap

    def __len__(self) -> int:
        with self._lock:
            return self._filled

    @property
    def filled(self) -> int:
        with self._lock:
            return self._filled

    @property
    def is_full(self) -> bool:
        with self._lock:
            return self._filled >= self._cap

    @property
    def dropped(self) -> int:
        """Elements overwritten because the window was too small."""
        with self._lock:
            return self._dropped

    # -- writes ------------------------------------------------------------
    def append(self, value: float) -> None:
        self.extend((value,))

    def extend(self, values) -> int:
        """Append a block; returns the number of *new* elements stored.

        Elements that fall out of the window are counted in :attr:`dropped`,
        whether they were pushed out in this call or had already been waiting at
        the write cursor.
        """
        arr = np.asarray(values, dtype=self.dtype)
        n = arr.size
        if n == 0:
            return 0
        with self._lock:
            overwritten = max(0, self._filled + n - self._cap)
            if n >= self._cap:
                # Keep only the newest window: everything previously held plus
                # the excess of this block is gone.
                self._data[:] = arr[n - self._cap :]
                self._head = 0
                self._filled = self._cap
                self._dropped += overwritten
                return n
            end = self._head + n
            if end <= self._cap:
                self._data[self._head : end] = arr
            else:
                first = self._cap - self._head
                self._data[self._head:] = arr[:first]
                self._data[: end - self._cap] = arr[first:]
            self._head = end % self._cap
            self._filled = min(self._filled + n, self._cap)
            self._dropped += overwritten
            return n

    # -- reads -------------------------------------------------------------
    def snapshot(self) -> np.ndarray:
        """A contiguous oldest -> newest copy.  Safe to keep after the lock."""
        with self._lock:
            if self._filled == 0:
                return np.empty(0, dtype=self.dtype)
            if self._filled < self._cap:
                return self._data[: self._filled].copy()
            # Full: oldest sits at _head (the next write position).
            return np.concatenate((self._data[self._head :], self._data[: self._head]))

    @property
    def latest(self) -> float | None:
        with self._lock:
            if self._filled == 0:
                return None
            return float(self._data[(self._head - 1) % self._cap])

    def clear(self) -> None:
        with self._lock:
            self._head = 0
            self._filled = 0
            self._dropped = 0
            self._data[:] = 0


class IndexedRingBuffer:
    """Values plus their absolute sample index, for a truthful time axis.

    The plot window is expressed in *seconds of device time*, so trimming drops
    by index rather than by count: a dropout shortens the visible waveform
    instead of stretching it, which is the whole point of ``first_sample_index``.
    """

    __slots__ = ("_values", "_indices", "_lock", "_cap", "_filled", "_first_index", "_last_index")

    def __init__(self, capacity: int) -> None:
        self._cap = int(capacity)
        if self._cap <= 0:
            raise ValueError("capacity must be positive")
        self._values = RingBuffer(self._cap, np.float64)
        self._indices = RingBuffer(self._cap, np.int64)
        self._lock = threading.Lock()
        self._filled = 0
        self._first_index: int | None = None
        self._last_index: int | None = None

    @property
    def capacity(self) -> int:
        return self._cap

    def __len__(self) -> int:
        with self._lock:
            return self._filled

    @property
    def index_range(self) -> tuple[int | None, int | None]:
        with self._lock:
            return self._first_index, self._last_index

    @property
    def dropped(self) -> int:
        return self._values.dropped

    def append_block(self, first_index: int, values) -> int:
        """Store ``values`` whose absolute indices start at ``first_index``."""
        arr = np.asarray(values, dtype=np.float64)
        n = arr.size
        if n == 0:
            return 0
        idx = np.arange(first_index, first_index + n, dtype=np.int64)
        with self._lock:
            self._values.extend(arr)
            self._indices.extend(idx)
            self._filled = min(self._filled + n, self._cap)
            if self._first_index is None:
                self._first_index = int(first_index)
            self._last_index = int(first_index + n - 1)
            # Re-derive the oldest index still held, so trimming stays honest.
            held = self._indices.snapshot()
            if held.size:
                self._first_index = int(held[0])
        return n

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """``(indices, values)`` oldest -> newest, both freshly allocated."""
        with self._lock:
            values = self._values.snapshot()
            indices = self._indices.snapshot()
        size = min(values.size, indices.size)
        return indices[:size], values[:size]

    def snapshot_window(self, span_seconds: float, sample_rate_hz: float) -> tuple[np.ndarray, np.ndarray]:
        """Snapshot trimmed to the last ``span_seconds`` of *device* time."""
        indices, values = self.snapshot()
        if indices.size == 0 or span_seconds <= 0:
            return indices, values
        newest = int(indices[-1])
        # Inclusive on both ends: a 2 s window at 1 kHz is 2000 samples, so the
        # oldest one kept is newest - 1999.
        span = max(1, int(round(span_seconds * sample_rate_hz)))
        oldest_allowed = newest - span + 1
        keep = indices >= max(oldest_allowed, 0)
        return indices[keep], values[keep]

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._indices.clear()
            self._filled = 0
            self._first_index = None
            self._last_index = None


class EventRateMeter:
    """Sliding-window events per second (packets/s in the status bar).

    A fixed-capacity ``deque`` of ``time.monotonic()`` stamps. Growth is bounded
    by ``maxlen``, not by the window: stale stamps are dropped when :attr:`rate`
    is read, which the GUI does on every repaint. A meter nobody reads therefore
    holds up to ``maxlen`` stamps rather than ``window_seconds`` worth.
    """

    __slots__ = ("_stamps", "_window", "_total")

    def __init__(self, window_seconds: float = 1.0, maxlen: int = 20000) -> None:
        self._window = float(window_seconds)
        self._stamps: deque[float] = deque(maxlen=maxlen)
        self._total = 0

    def tick(self, when: float | None = None) -> None:
        self._stamps.append(when if when is not None else time.monotonic())
        self._total += 1

    def ticks(self, count: int, when: float | None = None) -> None:
        now = time.monotonic() if when is None else when
        for _ in range(int(count)):
            self._stamps.append(now)
        self._total += int(count)

    @property
    def total(self) -> int:
        return self._total

    @property
    def rate(self) -> float:
        """Events/second over the window, referenced to the newest stamp."""
        if not self._stamps:
            return 0.0
        newest = self._stamps[-1]
        horizon = newest - self._window
        while self._stamps and self._stamps[0] < horizon:
            self._stamps.popleft()
        span = newest - (self._stamps[0] if self._stamps else horizon)
        if not self._stamps:
            return 0.0
        if span <= 0:
            # Everything landed in one instant: report the raw count per window.
            return len(self._stamps) / self._window
        return (len(self._stamps) - 1) / span

    def reset(self) -> None:
        self._stamps.clear()
        self._total = 0

    def __iter__(self) -> Iterator[float]:
        return iter(self._stamps)


class IntAccumulator:
    """A lock-guarded counter with a readable label, for the status strip."""

    __slots__ = ("_value", "_lock")

    def __init__(self, initial: int = 0) -> None:
        self._value = int(initial)
        self._lock = threading.Lock()

    def add(self, delta: int = 1) -> int:
        with self._lock:
            self._value += int(delta)
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def set(self, value: int) -> None:
        with self._lock:
            self._value = int(value)

    def reset(self) -> None:
        self.set(0)
