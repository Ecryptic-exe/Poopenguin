"""Original Discord score-image extraction without network access."""

import itertools

import discord


def extract_images_from_component(
    component: discord.components.Component, url_whitelist: list[str] | None = None
):
    image_urls: list[str] = []

    if isinstance(component, discord.components.ThumbnailComponent) and (
        url_whitelist is None
        or any(url in component.media.url for url in url_whitelist)
    ):
        image_urls.append(component.media.url)

    if isinstance(component, discord.components.MediaGalleryComponent):
        image_urls.extend(
            [
                item.media.url
                for item in component.items
                if url_whitelist is None
                or any(url in item.media.url for url in url_whitelist)
            ]
        )

    if isinstance(component, discord.components.SectionComponent):
        image_urls.extend(
            extract_images_from_component(component.accessory, url_whitelist)
        )

    if isinstance(component, discord.components.Container):
        image_urls.extend(
            itertools.chain.from_iterable(
                [
                    extract_images_from_component(child, url_whitelist)
                    for child in component.children
                ]
            )
        )

    return image_urls


def extract_images_from_message(
    message: discord.Message, url_whitelist: list[str] | None = None
):
    image_urls: list[str] = []

    embeds = message.embeds.copy()
    components = message.components.copy()

    for snapshot in message.message_snapshots:
        embeds.extend(snapshot.embeds)
        components.extend(snapshot.components)

    image_urls.extend(
        [
            embed.thumbnail.url
            for embed in embeds
            if embed.thumbnail.url is not None
            and (
                url_whitelist is None
                or any(url in embed.thumbnail.url for url in url_whitelist)
            )
        ]
    )
    image_urls.extend(
        itertools.chain.from_iterable(
            extract_images_from_component(component, url_whitelist)
            for component in components
        )
    )

    return image_urls
