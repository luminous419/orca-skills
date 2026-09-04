"""Executor-managed lease renewal for the whole duration of a blocking external call.

Exclusivity and fencing (see :mod:`runtime_state`) are only half of the ownership contract.
A claim is exclusive *while the lease is live*, and the lease is live only while somebody
renews it -- but nothing renewed it: ``claim()`` minted a 60-second lease and the executor
then blocked inside ``adapter.start()`` for the 5-15 minutes a real Claude/Codex dispatch
takes.  A perfectly healthy Coordinator therefore became indistinguishable from a dead one:
its lease lapsed, a second Coordinator took over, and the fence -- doing exactly what it was
built to do -- refused the healthy owner's own receipt and settlement.

This module closes that gap.  :class:`LeaseKeeper` renews the lease on a background daemon
thread for the entire life of the blocking call, at a period *derived from the lease* rather
than hard-coded, and fails closed: the first failed renewal stops the keeper and is re-raised
at the next ownership checkpoint, so an executor that lost ownership writes nothing.

Renewal deliberately does **not** paper over a dead owner.  The keeper lives and dies with
the process that owns the claim: when that process is killed the beats stop, the lease lapses
on schedule, and the existing takeover/recovery ladder runs exactly as before.
"""
from __future__ import annotations

import threading
from typing import Any

from .runtime_state import DEFAULT_LEASE_SECONDS, RuntimeStateConflict

#: How many renewals must fit inside one lease.  Three is the smallest divisor that still
#: tolerates a lost or slow beat without the lease lapsing under a healthy owner.
HEARTBEAT_LEASE_DIVISOR = 3.0
#: A floor, so a pathologically small lease cannot turn the keeper into a busy loop.
MIN_HEARTBEAT_INTERVAL_SECONDS = 0.001
#: The upper bound ``stop()`` puts on retiring the beat thread.  It is a bound, not a pause:
#: a thread between beats is woken immediately by the waiter's ``cancel()`` and joins at once.
#: Exceeding it is a reportable cleanup failure (:class:`LeaseKeeperNotStopped`), never a
#: silent return.  The thread is also a daemon, so a renewal wedged inside a ledger call can
#: never hold up interpreter shutdown on top of that.
DEFAULT_KEEPER_JOIN_SECONDS = 30.0


class LeaseRenewalFailed(RuntimeStateConflict):
    """A lease renewal failed, so this executor may no longer write for this intent.

    It is a :class:`RuntimeStateConflict` (and deliberately *not* a
    :class:`runtime_state.RuntimeStateLeaseHeld`): losing a lease mid-flight is not an
    invitation to observe and take over -- the effect this process just created may already
    exist -- so it stops the run rather than re-entering the claim path.
    """


class LeaseKeeperNotStopped(LeaseRenewalFailed):
    """:meth:`LeaseKeeper.stop` could not retire the beat thread within its join bound.

    Cleanup that silently gives up is the mirror image of the defect this module fixes: a
    beat thread the executor believes it has released can go on renewing the lease of an
    intent nobody is working on any more, and that lease then blocks a *legitimate* takeover.
    The thread is revoked before the join (so it writes no further renewal) and is kept
    referenced (so it stays observable), but a keeper that could not be retired is never
    reported as a clean shutdown -- it fails closed, exactly like a failed renewal.
    """


def heartbeat_interval_for(lease_seconds: Any) -> float:
    """Derive the renewal period from the lease length itself.

    Hard-coding a period is what makes a renewal loop wrong the moment the lease is
    reconfigured; deriving it keeps ``interval < lease`` true by construction.
    """
    try:
        lease = float(lease_seconds)
    except (TypeError, ValueError):
        lease = DEFAULT_LEASE_SECONDS
    if not lease > 0.0:
        lease = DEFAULT_LEASE_SECONDS
    return max(lease / HEARTBEAT_LEASE_DIVISOR, MIN_HEARTBEAT_INTERVAL_SECONDS)


def _default_waiter(stop: threading.Event, interval: float) -> bool:
    """Wait one period, or until stopped.  Returns True when the keeper must stop.

    This waits in *real* seconds on purpose.  The lease clock is injectable and a test clock's
    ``sleep`` returns instantly, which would turn the beat loop into a busy spin; tests pace
    the keeper by injecting a ``waiter`` instead.
    """
    return stop.wait(interval)


class LeaseKeeper:
    """Keep one claim alive while its owner is blocked in an external call.

    Contract:

    * renewal covers the **whole** duration of the wrapped call, at ``interval_seconds``
      beats derived from the lease, so a healthy owner is never mistaken for a dead one;
    * it is **fail-closed** -- the first failed renewal (rotated token, lost lease,
      unreadable ledger) stops the keeper and is re-raised by :meth:`raise_if_lost`, which
      the executor calls before every ownership-sensitive write;
    * :meth:`stop` runs on every exit path (success, exception, cancellation) via the context
      manager, and **verifies** that it worked: the thread is revoked before the join, the
      reference is only dropped once the thread is actually gone, and a join that times out
      is reported as :class:`LeaseKeeperNotStopped` rather than returning as if it were a
      clean shutdown.  The thread is a daemon on top of that, so nothing blocks process exit.

    ``waiter`` is the injection point that makes the whole thing testable without sleeping:
    it is called as ``waiter(stop_event, interval)`` and returns True to stop.  A waiter may
    additionally expose ``cancel()``, which :meth:`stop` calls to wake it immediately.
    """

    def __init__(self, runtime_state: Any, intent_id: str, lease_token: str, *,
                 interval_seconds: float, waiter: Any = None,
                 join_seconds: float = DEFAULT_KEEPER_JOIN_SECONDS) -> None:
        self._runtime_state = runtime_state
        self._intent_id = intent_id
        self._lease_token = lease_token
        self._interval = max(float(interval_seconds), MIN_HEARTBEAT_INTERVAL_SECONDS)
        self._waiter = waiter or _default_waiter
        self._join_seconds = float(join_seconds)
        self._stop = threading.Event()
        # ``_revoked`` is the executor's "I have let go of this keeper" flag, distinct from
        # ``_stop`` (which the beat loop also sets on its own failure).  The beat loop
        # re-reads it immediately before *and* immediately after each renewal, so a thread
        # that outlives ``stop()`` cannot keep an abandoned intent's lease alive.
        self._revoked = threading.Event()
        self._condition = threading.Condition()
        self._beats = 0
        self._failure: BaseException | None = None
        self._orphaned = False
        self._thread: threading.Thread | None = None

    # ---- lifecycle ----

    def start(self) -> "LeaseKeeper":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name=f"lease-keeper-{self._intent_id}",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self) -> bool:
        """Revoke the keeper and retire its thread.  Returns True only on a clean shutdown.

        The order matters and is the whole point:

        1. **revoke first.**  ``_revoked`` is set before anything else, so from this instant
           the beat loop refuses to write another renewal -- including a loop that is
           currently blocked inside ``heartbeat()`` and will unblock later.  An abandoned
           intent must never keep a live lease that blocks a legitimate takeover.
        2. **wake, then join.**  The waiter's ``cancel()`` releases a parked thread
           immediately, so the join bound is a safety limit rather than a pause.
        3. **verify.**  The thread reference is dropped only once ``is_alive()`` is False.
           A thread still running after the bound is recorded as orphaned -- permanently,
           because a keeper whose cleanup failed must never later claim to be clean -- and
           this returns False so the caller can fail closed.

        Safe to call more than once: a repeat call re-attempts the join (reaping a thread
        that has since finished) and re-reports the sticky orphaned state.
        """
        self._revoked.set()
        self._stop.set()
        cancel = getattr(self._waiter, "cancel", None)
        if callable(cancel):
            cancel()
        thread = self._thread
        if thread is not None:
            if thread.is_alive():
                thread.join(self._join_seconds)
            if thread.is_alive():
                with self._condition:
                    self._orphaned = True
                    self._condition.notify_all()
            else:
                self._thread = None
        return not self.orphaned

    def __enter__(self) -> "LeaseKeeper":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        stopped = self.stop()
        if exc_type is None:
            # Fail closed on the success path, on both halves of ``degraded``.  When the body
            # is already raising we leave that exception alone rather than masking the real
            # failure -- the degraded state stays observable on the keeper either way.
            if not stopped:
                # An executor that cannot retire its own beat thread cannot honestly claim
                # it has finished owning the intent.
                raise self.cleanup_error()
            # A renewal that failed after the executor's last checkpoint -- during or after
            # its final write -- has nobody left to report it: the body has finished and will
            # take no further checkpoint.  Exit is that last checkpoint.  Without it a
            # failure the keeper had already recorded died with the keeper, and the node
            # returned success on a claim it could not vouch for.
            self.raise_if_lost()
        return False

    # ---- state ----

    @property
    def beats(self) -> int:
        with self._condition:
            return self._beats

    @property
    def failure(self) -> BaseException | None:
        with self._condition:
            return self._failure

    @property
    def lost(self) -> bool:
        return self.failure is not None

    @property
    def orphaned(self) -> bool:
        """True once a :meth:`stop` join timed out.  Sticky: a failed cleanup stays failed."""
        with self._condition:
            return self._orphaned

    @property
    def degraded(self) -> bool:
        """The keeper can no longer vouch for the claim: renewal failed, or cleanup did."""
        return self.lost or self.orphaned

    @property
    def revoked(self) -> bool:
        """True once :meth:`stop` has been called: no further renewal may be written."""
        return self._revoked.is_set()

    def cleanup_error(self) -> LeaseKeeperNotStopped:
        """The exception a failed shutdown reports, built where the facts are."""
        return LeaseKeeperNotStopped(
            f"LEASE_KEEPER_NOT_STOPPED:{self._intent_id}: the renewal thread was still "
            f"running {self._join_seconds}s after stop(); it is revoked and will write no "
            "further renewal, but this executor cannot report a clean shutdown and must not "
            "be treated as still owning the intent")

    def wait_for_beats(self, count: int, timeout: float = DEFAULT_KEEPER_JOIN_SECONDS) -> bool:
        """Block until ``count`` renewals have landed (or the keeper failed)."""
        with self._condition:
            return bool(self._condition.wait_for(
                lambda: self._beats >= count or self._failure is not None, timeout))

    def raise_if_lost(self) -> None:
        """Fail closed: refuse to let a superseded owner continue writing."""
        failure = self.failure
        if failure is None:
            return
        raise LeaseRenewalFailed(
            f"LEASE_RENEWAL_FAILED:{self._intent_id}: the lease could not be renewed during "
            f"the external call ({type(failure).__name__}: {failure}); this executor no "
            "longer owns the intent and must not record a receipt or settlement") from failure

    # ---- beat loop ----

    def _run(self) -> None:
        while True:
            if self._waiter(self._stop, self._interval) or self._stop.is_set():
                return
            # Re-checked here, immediately before the write, rather than only at the top of
            # the loop: ``stop()`` may have landed while the waiter was returning, and a
            # renewal written after revocation would extend the lease of an intent this
            # executor has already let go of.
            if self._revoked.is_set():
                return
            try:
                self._runtime_state.heartbeat(self._intent_id, self._lease_token)
            except BaseException as exc:            # noqa: BLE001 - fail closed on anything
                # Any renewal failure means ownership can no longer be asserted.  Stop
                # beating and keep the cause: retrying would only paper over a takeover.
                with self._condition:
                    self._failure = exc
                    self._condition.notify_all()
                self._stop.set()
                return
            if self._revoked.is_set():
                # Revoked while this renewal was in flight -- the case ``stop()`` reports as
                # orphaned.  That one already-issued write cannot be recalled, but this
                # thread neither counts it nor ever writes again, so the abandoned lease
                # lapses after at most one further period instead of being held forever.
                return
            with self._condition:
                self._beats += 1
                self._condition.notify_all()


def lease_keeper_factory(*, interval_seconds: float | None = None,
                         waiter: Any = None) -> Any:
    """Build the factory the executor calls once per claimed intent.

    ``interval_seconds`` overrides the lease-derived period; both it and ``waiter`` exist so
    a test can drive renewal deterministically instead of waiting on wall-clock time.
    """

    def factory(runtime_state: Any, intent_id: str, lease_token: str) -> LeaseKeeper:
        period = (interval_seconds if interval_seconds is not None
                  else heartbeat_interval_for(getattr(runtime_state, "lease_seconds",
                                                      DEFAULT_LEASE_SECONDS)))
        return LeaseKeeper(runtime_state, intent_id, lease_token, interval_seconds=period,
                           waiter=waiter)

    return factory
