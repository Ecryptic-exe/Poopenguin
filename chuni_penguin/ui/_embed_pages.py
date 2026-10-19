import discord


def split_text(text: str, limit: int = 3500) -> list[str]:
    """Keep all text, including unusually long names, within Discord's limit."""
    chunks = []
    while len(text) > limit:
        end = text.rfind("\n", 0, limit + 1)
        if end <= 0:
            end = limit
        chunks.append(text[:end])
        text = text[end:].lstrip("\n")
    chunks.append(text)
    return chunks


def embed_pages(embed: discord.Embed) -> list[list[discord.Embed]]:
    pages = []
    for description in split_text(embed.description or ""):
        page = embed.copy()
        page.title = (page.title or "")[:256] or None
        page.description = description or None
        pages.append([page])
    return pages
