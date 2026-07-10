import asyncio

from forge.engine.bus import EventBus
from forge.engine.events import TextDelta
from forge.engine.scheduler import Scheduler


async def test_bus_fans_out():
    bus = EventBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish(TextDelta(session_id="s1", text="x"))
    assert (await q1.get()).text == "x" and (await q2.get()).text == "x"
    bus.unsubscribe(q2)
    bus.publish(TextDelta(session_id="s1", text="y"))
    assert q2.empty()


async def test_scheduler_queues_beyond_cap():
    sched = Scheduler(max_concurrent=1)
    order: list[str] = []

    async def job(name: str, hold: float):
        def on_queued(): order.append(f"{name}:queued")
        async with sched.slot(on_queued):
            order.append(f"{name}:run")
            await asyncio.sleep(hold)

    t1 = asyncio.create_task(job("a", 0.2))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(job("b", 0))
    await asyncio.gather(t1, t2)
    assert order == ["a:run", "b:queued", "b:run"]
