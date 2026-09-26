"""Sustained-anomaly state machine: observations in, events out.

Detection never pauses while an event is being handled. This tracker keeps
evaluating every frame and owns two guarantees discussed for the session
design:

- sustained trigger: anomaly observations must persist for a calibrated
  window before an event fires (filters head-outs and pass-throughs);
- in-flight dedup: while an event is open (from trigger until the anomaly
  state clears), the same anomaly does not start another event. A second,
  spatially separated anomaly may open its own event.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from monitor.cow_fall.rules import FallObservation

EventId = str


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


@dataclass
class OpenEvent:
    """An event from trigger until its anomaly state clears."""

    event_id: EventId
    opened_at: float
    last_seen_at: float
    center: tuple[float, float]
    rules: tuple[str, ...]
    merged_updates: int = 0

    def update(self, now: float, center: tuple[float, float],
               rules: tuple[str, ...]) -> None:
        self.last_seen_at = now
        self.center = center
        self.rules = rules
        self.merged_updates += 1


@dataclass
class FallTracker:
    """One tracker instance per monitoring session."""

    # Anomaly must persist this long before an event fires. Calibrated to
    # 30s: the robot arm dwells inside the forbidden zone for up to ~16s
    # per cycle (measured on production replays), while a fallen cow stays
    # until removed — time scale, not geometry, separates them.
    trigger_sustain_s: float = 30.0
    # Anomaly must be absent this long before an open event closes.
    clear_sustain_s: float = 10.0
    # Observations closer than this to an open event merge into it.
    merge_distance_px: float = 300.0

    open_events: dict[EventId, OpenEvent] = field(default_factory=dict)
    _pending_since: float | None = None
    _pending_center: tuple[float, float] | None = None
    _pending_rules: tuple[str, ...] = ()
    _absent_since: dict[EventId, float] = field(default_factory=dict)
    _next_seq: int = 1

    def update(self, now: float, anomalies: list[FallObservation]) -> list[OpenEvent]:
        """Feed one frame's anomaly observations; return newly fired events."""
        fired: list[OpenEvent] = []
        matched: set[EventId] = set()
        unmatched = 0

        for obs in anomalies:
            owner = self._match_open_event(obs)
            if owner is not None:
                owner.update(now, obs.center, obs.rule_ids)
                matched.add(owner.event_id)
                self._absent_since.pop(owner.event_id, None)
                continue
            # No open event at this spot: accumulate toward a new trigger.
            unmatched += 1
            if (self._pending_center is None
                    or _distance(self._pending_center, obs.center)
                    > self.merge_distance_px):
                # A different spot restarts the sustain clock: two objects
                # taking turns must not add up to one sustained anomaly.
                self._pending_since = now
            if self._pending_since is None:
                self._pending_since = now
            self._pending_center = obs.center
            self._pending_rules = obs.rule_ids
            if now - self._pending_since >= self.trigger_sustain_s:
                event = OpenEvent(
                    event_id=f"fall-{self._next_seq}",
                    opened_at=self._pending_since,
                    last_seen_at=now,
                    center=obs.center,
                    rules=self._pending_rules,
                )
                self._next_seq += 1
                self.open_events[event.event_id] = event
                self._absent_since.pop(event.event_id, None)
                fired.append(event)
                self._pending_since = None
                self._pending_center = None
                self._pending_rules = ()

        if unmatched == 0 and self._pending_since is not None:
            # Every observation merged into an open event (or none at all):
            # nothing is accumulating toward a fresh trigger anymore.
            self._pending_since = None
            self._pending_center = None
            self._pending_rules = ()

        # Track absence for open events that saw no observation this frame.
        for event_id in list(self.open_events):
            if event_id in matched:
                continue
            event = self.open_events[event_id]
            first_absent = self._absent_since.get(event_id)
            if first_absent is None:
                self._absent_since[event_id] = now
            elif now - first_absent >= self.clear_sustain_s:
                # Closed events leave the active table; the session layer
                # keeps the archived record.
                del self.open_events[event_id]
                self._absent_since.pop(event_id, None)
        return fired

    def _match_open_event(self, obs: FallObservation) -> OpenEvent | None:
        best: OpenEvent | None = None
        best_distance = self.merge_distance_px
        for event in self.open_events.values():
            distance = _distance(event.center, obs.center)
            if distance <= best_distance:
                best = event
                best_distance = distance
        return best
