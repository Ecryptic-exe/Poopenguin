import asyncio

from discord.ext import tasks
from discord.ext.track_edits import EditTrackerCog


async def cancel_loop(loop: tasks.Loop):
    """Stop a Cog-owned loop now, including a sleeping/before-ready iteration."""
    task = loop.get_task()
    loop.cancel()
    if task is not None and task is not asyncio.current_task():
        await asyncio.gather(task, return_exceptions=True)


class ManagedEditTrackerCog(EditTrackerCog, name=EditTrackerCog.__cog_name__):
    async def cog_unload(self):
        await super().cog_unload()
        await cancel_loop(self.purge_cache)
