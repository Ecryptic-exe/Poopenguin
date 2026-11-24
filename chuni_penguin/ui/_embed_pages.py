import discord


def split_text(text: str, limit: int = 3500) -> list[str]:
    """Prefer a newline over cutting a long paragraph or field in two."""
    chunks = []
    while len(text) > limit:
        end = text.rfind("\n", 0, limit + 1)
        if end <= 0:
            end = limit
        chunks.append(text[:end])
        text = text[end:].removeprefix("\n")
    chunks.append(text)
    return chunks


def embed_pages(embed: discord.Embed) -> list[list[discord.Embed]]:
    """Paginate one card, reserving space for the reader's chart header and footer."""
    metadata = embed.to_dict()
    # discord.Embed.copy() shares the field list. Each page needs its own list.
    metadata.pop("fields", None)
    base = discord.Embed.from_dict(metadata)
    base.title = (base.title or "")[:256] or None
    base.description = None
    if base.author.name:
        base.set_author(
            name=base.author.name[:256],
            url=base.author.url,
            icon_url=base.author.icon_url,
        )
    if base.footer.text:
        base.set_footer(text=base.footer.text[:1600], icon_url=base.footer.icon_url)

    # SongDetailsView decorates each page with at most 256 + 385 more characters.
    budget = 5359
    pages = []
    for description in split_text(
        embed.description or "", min(3500, budget - len(base))
    ):
        page = base.copy()
        page.description = description or None
        pages.append([page])

    for field in embed.fields:
        name = str(field.name)[:256]
        for value in split_text(str(field.value), 1024):
            page = pages[-1][0]
            if len(page.fields) == 25 or len(page) + len(name) + len(value) > budget:
                page = base.copy()
                pages.append([page])
            page.add_field(name=name, value=value, inline=field.inline)
    return pages
