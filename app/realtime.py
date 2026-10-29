"""Live order updates (Server-Sent Events).

Every change to an order is first saved in the database and only then published here, so what the admin sees
live is always exactly what a page refresh will show. Subscribers are admin dashboards.

In-process broker: correct for a single server process (the Procfile runs one). If you ever scale to several
processes, replace publish/subscribe with Redis pub/sub; the dashboard also re-syncs through
GET /api/admin/orders?updated_since=... after a reconnect, so nothing is lost.
"""
import asyncio
import threading

from . import models
from .serializers import order_detail


class Broker:
    def __init__(self) -> None:
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subs.add((asyncio.get_running_loop(), q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = {s for s in self._subs if s[1] is not q}

    @staticmethod
    def _put(q: asyncio.Queue, item: tuple[str, dict]) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:  # a stuck client: it will re-sync from the API when it reconnects
            pass

    def publish(self, event: str, data: dict) -> None:
        """Safe to call from request threads and from the event loop."""
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, (event, data))
            except RuntimeError:  # that loop has shut down
                self.unsubscribe(q)


broker = Broker()


def publish_order(event: str, o: models.Order) -> None:
    """event is 'order.created' or 'order.updated'. Call only after the change has been committed."""
    broker.publish(event, order_detail(o))
