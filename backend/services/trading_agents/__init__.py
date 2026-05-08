"""Trading agents package — autonomous paper-trading strategies.

Each agent exposes a single ``run(db)`` coroutine registered with
APScheduler in ``server.py``. They operate under the global kill
switch and narrate every decision into the agent activity feed.
"""
