import contextlib
import itertools
from typing import TYPE_CHECKING

from discord.ext import commands

from chuni_penguin.adapters.errors import NetworkError
from chuni_penguin.context import PenguinContext
from chuni_penguin.logging import logged_prefix_command
from chuni_penguin.types import CourseRecord
from chuni_penguin.ui import CourseListView

if TYPE_CHECKING:
    from chuni_penguin.bot import ChuniBot


class CoursesCog(commands.Cog, name="Courses"):
    def __init__(self, bot: "ChuniBot"):
        self.bot = bot
        self.repository = bot.services.courses

    @commands.hybrid_command("courses")
    @logged_prefix_command
    async def courses(self, ctx: PenguinContext):
        """Get a list of all courses.

        If you are logged in to CHUNITHM-NET, this also displays your course records.
        """

        async with ctx.typing():
            # This is mainly an informative command, so we don't wanna stress too hard
            # that the user isn't logged in.
            course_records: list[CourseRecord] = []

            with contextlib.suppress(NetworkError, commands.CommandError):
                async with self.bot.services.accounts.network(ctx) as client:
                    with contextlib.suppress(NotImplementedError):
                        course_records = await client.get_course_records()

            ids_versions = await self.repository.version_index()

        if len(ids_versions) == 0:
            msg = "No course data."
            raise commands.CommandError(msg)

        version_groups = [
            [version[1] for version in group]
            for _, group in itertools.groupby(ids_versions, key=lambda r: r[0])
        ]
        view = CourseListView(
            ctx, version_groups, course_records, repository=self.repository
        )
        await view.start()


async def setup(bot: "ChuniBot"):
    await bot.add_cog(CoursesCog(bot))
