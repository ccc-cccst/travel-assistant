"""Nested exclusive and inclusive wall-clock accounting."""

from collections import defaultdict
import contextlib
import functools
import time


class ExclusiveTimer:
    """Nested spans: additive exclusive time, non-additive inclusive time.

    Same-label recursive inclusive spans count only the outermost occurrence.
    Instrumentation remains separate from baseline runs.
    """
    def __init__(self):
        self.stack = []
        self.stats = defaultdict(lambda: {"calls": 0, "exclusive_seconds": 0., "inclusive_seconds": 0.})

    @contextlib.contextmanager
    def span(self, label):
        outer_label = not any(frame[0] == label for frame in self.stack)
        frame = [label, time.perf_counter(), 0.]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = time.perf_counter() - frame[1]
            self.stack.pop()
            row = self.stats[label]
            row["calls"] += 1
            row["exclusive_seconds"] += elapsed - frame[2]
            if outer_label:
                row["inclusive_seconds"] += elapsed
            if self.stack:
                self.stack[-1][2] += elapsed

    def wrap(self, obj, name, label, root=False):
        original = getattr(obj, name)
        @functools.wraps(original)
        def measured(*args, **kwargs):
            if not self.stack and not root:
                return original(*args, **kwargs)
            with self.span(label):
                return original(*args, **kwargs)
        setattr(obj, name, measured)
        return original
