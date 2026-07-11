"""
Manages "copypasta types": named pools of fill-in-the-blank templates
used by the !copypasta command (see cogs/copypasta_cog.py).

Data shape (data/copypasta_sets.json):
{
  "types": {
    "<type_id>": {
      "templates": ["{text} is handsome", "..."],
      "enabled": true
    },
    ...
  }
}

Every template must contain a `{text}` placeholder, which gets replaced
with whatever the user passed to !copypasta. Templates are strictly kept
in separate pools by type_id - a "tag" template is never picked when the
user asked for a "song", and vice versa. This is what stops mismatched
combinations like a tagging line getting used on a song title.

Global (shared across every server the bot is in), same reasoning as
KeywordManager: this mirrors how a single hardcoded copypasta pool used
to behave everywhere, and keeps management simple. If per-server pools
are wanted later, key the top-level dict by guild_id instead of being flat.
"""
import random

from config import load_copypasta, save_copypasta

PLACEHOLDER = "{text}"


class CopypastaError(Exception):
    """Raised for invalid copypasta-type operations (bad name, duplicate, etc)."""


class CopypastaManager:
    def __init__(self):
        self._data = load_copypasta()
        self._data.setdefault("types", {})

    # -- persistence -----------------------------------------------------
    def _save(self):
        save_copypasta(self._data)

    def _reload(self):
        self._data = load_copypasta()
        self._data.setdefault("types", {})

    # -- read --------------------------------------------------------------
    def list_types(self):
        self._reload()
        return self._data["types"]

    def get_type(self, type_id: str):
        self._reload()
        s = self._data["types"].get(type_id)
        if s is None:
            raise CopypastaError(f"No copypasta type named '{type_id}'.")
        return s

    def pick(self, type_id: str, text: str, avoid_index: int = None):
        """Return (index, rendered_text) for a random template in type_id,
        with `{text}` replaced by `text`.

        If the pool has more than one template and avoid_index is given,
        the same index is rerolled once so the exact same line doesn't
        fire twice in a row for that type - this is the "don't repeat/
        collide with each other" behaviour, on top of pools never mixing
        across types in the first place."""
        s = self.get_type(type_id)
        if not s.get("enabled", True):
            raise CopypastaError(f"Copypasta type '{type_id}' is currently disabled.")
        templates = s.get("templates", [])
        if not templates:
            raise CopypastaError(f"Copypasta type '{type_id}' has no templates yet.")

        index = random.randrange(len(templates))
        if avoid_index is not None and len(templates) > 1 and index == avoid_index:
            choices = [i for i in range(len(templates)) if i != avoid_index]
            index = random.choice(choices)

        return index, templates[index].replace(PLACEHOLDER, text)

    # -- write ---------------------------------------------------------
    def create_type(self, type_id: str):
        self._reload()
        if type_id in self._data["types"]:
            raise CopypastaError(f"Copypasta type '{type_id}' already exists.")
        self._data["types"][type_id] = {"templates": [], "enabled": True}
        self._save()

    def delete_type(self, type_id: str):
        self.get_type(type_id)  # raises if missing
        del self._data["types"][type_id]
        self._save()

    def set_enabled(self, type_id: str, enabled: bool):
        s = self.get_type(type_id)
        s["enabled"] = enabled
        self._save()

    def add_template(self, type_id: str, template: str):
        s = self.get_type(type_id)
        if PLACEHOLDER not in template:
            raise CopypastaError(
                f"Template must contain a {PLACEHOLDER} placeholder, e.g. '{PLACEHOLDER} is handsome'.")
        s["templates"].append(template)
        self._save()

    def remove_template(self, type_id: str, index: int):
        s = self.get_type(type_id)
        if index < 0 or index >= len(s["templates"]):
            raise CopypastaError(f"Template index {index} out of range for '{type_id}'.")
        s["templates"].pop(index)
        self._save()
