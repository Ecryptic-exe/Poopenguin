from discord.ext.commands import CheckFailure


class ServiceUnavailable(CheckFailure):
    def __init__(self, service: str):
        self.service = service
        super().__init__(
            f"The {service} service is unavailable. Please try again later."
        )
