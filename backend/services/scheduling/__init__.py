"""APScheduler job registration package.

Strangler-split out of the monolithic ``server._start_schedulers``
(2026-05-08, Architecture Split Step 3). Every scheduler.add_job
call lives in ``jobs.register_all`` — same job IDs, same intervals,
same replace_existing flags as the previous in-place registration.

server.py retains:
  - AsyncIOScheduler instantiation
  - calling ``register_all(scheduler, db, server)``
  - ``scheduler.start()``
  - self-test wiring
  - outer try/except + Health-panel error pump
"""
from .jobs import register_all  # noqa: F401
