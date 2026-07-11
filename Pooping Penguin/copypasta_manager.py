"""
Manages "copypasta types": named pools of fill-in-the-blank templates
used by the !copypasta command (see cogs/copypasta_cog.py).

Data shape (data/copypasta_sets.json):
{
  "types": {
    "<type_id>": {
      "templates": ["{text} is handsome", "..."],
      "placeholders": ["text"],
      "enabled": true
    },
    "<multi_placeholder_type_id>": {
      "templates": ["{people}的{act}很弱智", "..."],
      "placeholders": ["people", "act"],
      "enabled": true
    },
    ...
  }
}

A template can contain any number of distinct `{name}` placeholders, not
just `{text}`. Every type has a fixed, ordered list of placeholder names
("placeholders") - this is set automatically from the first template
added to the type, and every template added after that must use exactly
that same set of names (order within the template text can differ, but
the set must match). This is what lets `!copypasta <type> <val1> <val2>
...` know how many values to expect and which name each one fills, no
matter which template in the pool ends up getting picked.

For a classic single-blank type this just means `placeholders == ["text"]`
and behaves exactly like before: `!copypasta tag @User`.  For a type like
`{people}的{act}很弱智` it means `placeholders == ["people", "act"]` and
usage becomes `!copypasta thattype Alice 打籃球` (values are matched to
placeholder names positionally, in the order the type's placeholders were
first established).

Templates are strictly kept in separate pools by type_id - a "tag"
template is never picked when the user asked for a "song", and vice
versa. This is what stops mismatched combinations like a tagging line
getting used on a song title.

Global (shared across every server the bot is in), same reasoning as
KeywordManager: this mirrors how a single hardcoded copypasta pool used
to behave everywhere, and keeps management simple. If per-server pools
are wanted later, key the top-level dict by guild_id instead of being flat.
"""
import random
import re

from config import load_copypasta, save_copypasta

# Matches {anything_word_like}, e.g. {text}, {people}, {act}.
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class CopypastaError(Exception):
    """Raised for invalid copypasta-type operations (bad name, duplicate, etc)."""


def extract_placeholders(template: str):
    """Return the distinct {name} placeholders in `template`, in the
    order they first appear (e.g. '{people}的{act}很弱智' -> ['people', 'act'])."""
    seen = []
    for match in PLACEHOLDER_RE.finditer(template):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


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

    def _ensure_placeholders(self, s: dict):
        """Back-fill 'placeholders' for types saved before this field
        existed (old data only had 'templates' + 'enabled'), by deriving
        it from the first template. Doesn't write to disk by itself -
        callers that mutate the type already call self._save()."""
        if "placeholders" not in s:
            templates = s.get("templates", [])
            s["placeholders"] = extract_placeholders(templates[0]) if templates else []
        return s

    # -- read --------------------------------------------------------------
    def list_types(self):
        self._reload()
        for s in self._data["types"].values():
            self._ensure_placeholders(s)
        return self._data["types"]

    def get_type(self, type_id: str):
        self._reload()
        s = self._data["types"].get(type_id)
        if s is None:
            raise CopypastaError(f"No copypasta type named '{type_id}'.")
        return self._ensure_placeholders(s)

    def pick(self, type_id: str, values, avoid_index: int = None):
        """Return (index, rendered_text) for a random template in type_id,
        with each of the type's {placeholder} names replaced by the
        corresponding entry in `values` (matched positionally, in the
        type's established placeholder order).

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

        placeholders = s.get("placeholders") or ["text"]
        values = list(values)
        if len(values) != len(placeholders):
            needed = " ".join(f"{{{name}}}" for name in placeholders)
            raise CopypastaError(
                f"`{type_id}` needs {len(placeholders)} value(s) ({needed}), "
                f"but got {len(values)}.")

        index = random.randrange(len(templates))
        if avoid_index is not None and len(templates) > 1 and index == avoid_index:
            choices = [i for i in range(len(templates)) if i != avoid_index]
            index = random.choice(choices)

        rendered = templates[index]
        for name, value in zip(placeholders, values):
            rendered = rendered.replace(f"{{{name}}}", value)

        return index, rendered

    # -- write ---------------------------------------------------------
    def create_type(self, type_id: str):
        self._reload()
        if type_id in self._data["types"]:
            raise CopypastaError(f"Copypasta type '{type_id}' already exists.")
        self._data["types"][type_id] = {"templates": [], "placeholders": [], "enabled": True}
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
        names = extract_placeholders(template)
        if not names:
            raise CopypastaError(
                "Template must contain at least one {placeholder}, e.g. "
                "'{text} is handsome' or '{people}的{act}很弱智'.")

        existing = s.get("placeholders")
        if existing:
            if set(names) != set(existing):
                needed = " ".join(f"{{{name}}}" for name in existing)
                raise CopypastaError(
                    f"`{type_id}` already uses {needed} - every template in this type "
                    f"must use exactly those placeholders (any order).")
        else:
            s["placeholders"] = names

        s["templates"].append(template)
        self._save()

    def remove_template(self, type_id: str, index: int):
        s = self.get_type(type_id)
        if index < 0 or index >= len(s["templates"]):
            raise CopypastaError(f"Template index {index} out of range for '{type_id}'.")
        s["templates"].pop(index)
        self._save()
