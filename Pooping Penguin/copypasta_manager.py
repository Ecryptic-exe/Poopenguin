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
just `{text}`. Each type keeps a "placeholders" list, but this is now
just the *union* of every placeholder name any template in that type has
ever used - it grows automatically as templates using new names are
added (see add_template()). Templates within a type do NOT have to share
an identical placeholder set any more: one template can be `{text} is
handsome` and another in the same type can be `{people}的{act}很弱智`.

What actually gets filled in for a given render is always that specific
template's OWN placeholders, extracted fresh from its text (see _fill()).
This means when a specific template is known ahead of time - e.g. picked
by index via `render()`, or from the dropdown in `/copypasta info` - only
the placeholders that exact template contains are requested/filled, not
the type's full pool. For the blind `!copypasta <type> <val1> <val2> ...`
command, which template will be picked is only known after picking it
(see pick()), so the number of values needed can vary by invocation; if
what's given doesn't match the placeholders of whichever template ends up
chosen, CopypastaError reports exactly which placeholders that pick
needed so the user can retry.

For a classic single-blank type this just means each template's own
placeholders end up being `["text"]` and behaves exactly like before:
`!copypasta tag @User`. For a type like `{people}的{act}很弱智` it means
that template's own placeholders are `["people", "act"]` and usage becomes
`!copypasta thattype Alice 打籃球` (values are matched to that template's
placeholder names positionally, in the order they first appear in its
text).

Templates are strictly kept in separate pools by type_id - a "tag"
template is never picked when the user asked for a "song", and vice
versa. This is what stops mismatched combinations like a tagging line
getting used on a song title.

Global (shared across every server the bot is in), same reasoning as
KeywordManager: this mirrors how a single hardcoded copypasta pool used
to behave everywhere, and keeps management simple. If per-server pools
are wanted later, key the top-level dict by guild_id instead of being flat.

--- Optional: per-game terminology (`game_terms`) ---
A type can optionally carry a "game_terms" dict, one entry per game,
used to swap out jargon in the template text based on which game a
`{game}`-style placeholder was filled in with. Shape:

  "game_terms": {
    "<game_key>": {
      "display": "IIDX",                 // canonical name shown wherever
                                          // the plain {game} placeholder
                                          // appears in the template
      "aliases": ["iidx-alias", ...],    // other strings that also match
                                          // this game when typed by hand
      "<term_key>": "六段",              // any other key is a jargon
      "<term_key2>": "皆傳",             // term for this game, referenced
      ...                                //   in templates as {{term_key}}
    },
    "<other_game_key>": { ... },
    ...
  }

In the template text, a *double*-braced token like `{{score_rate}}`
looks up `term_key = "score_rate"` in whichever game's dict was
selected (by the `{game}` value the user typed matching that game's key,
`display` name, or an alias, case-insensitively) and substitutes the
result. This is deliberately a separate syntax from the normal single-
braced `{name}` placeholders: `{name}` is filled in from a value the
user supplies, `{{term_key}}` is filled in automatically from
game_terms based on the `{game}` value, so a type using both needs a
"game" placeholder in its `placeholders` list, but never needs the user
to supply the jargon terms themselves.

If a type has no "game_terms", or has one but its template contains no
`{{...}}` tokens, this whole layer is a no-op - existing types are
unaffected. If `{game}` is filled with something that doesn't match any
game_key/display/alias, rendering fails with a clear CopypastaError
listing the valid options, rather than silently leaving `{{...}}`
tokens unresolved in the output.
"""
import random
import re

from config import load_copypasta, save_copypasta

# Matches {anything_word_like}, e.g. {text}, {people}, {act} - but NOT
# a {word} that's itself wrapped in an extra pair of braces (the
# lookaround guards keep this from matching the inner "{key}" that's
# part of a "{{key}}" game-term token below).
PLACEHOLDER_RE = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")

# Matches {{anything_but_braces}}, e.g. {{score_rate}}, {{15+}}, {{彩R}}.
# These are game-terminology references, resolved from a type's
# "game_terms" dict (see module docstring) rather than from user-supplied
# values the way {name} placeholders are.
TERM_RE = re.compile(r"\{\{([^{}]+)\}\}")


class CopypastaError(Exception):
    """Raised for invalid copypasta-type operations (bad name, duplicate, etc)."""


def extract_placeholders(template: str):
    """Return the distinct {name} placeholders in `template`, in the
    order they first appear (e.g. '{people}的{act}很弱智' -> ['people', 'act']).
    Ignores {{term_key}} game-term tokens - see PLACEHOLDER_RE."""
    seen = []
    for match in PLACEHOLDER_RE.finditer(template):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def resolve_game_key(game_terms: dict, raw: str):
    """Match a user-typed game string against a type's game_terms dict,
    case-insensitively, by game_key, "display" name, or any "aliases"
    entry. Returns the matching game_key, or None if nothing matches."""
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    for game_key, info in game_terms.items():
        if raw == game_key.lower():
            return game_key
        if raw == str(info.get("display", "")).lower():
            return game_key
        if raw in (str(a).lower() for a in info.get("aliases", [])):
            return game_key
    return None


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

    def _substitute(self, type_id: str, s: dict, template: str, placeholders: list, values: list) -> str:
        """Core substitution shared by both value-matching strategies
        below: resolves any {{term_key}} game-term tokens (if the type
        has "game_terms" and a "game" placeholder), then replaces each
        {name} in `template` with the corresponding entry in `values`
        (`placeholders`/`values` are already the same length and in the
        same order at this point - name-vs-position matching happens
        before this is called)."""
        values = list(values)
        game_terms = s.get("game_terms")
        if game_terms and "game" in placeholders:
            game_index = placeholders.index("game")
            game_raw = values[game_index]
            game_key = resolve_game_key(game_terms, game_raw)
            if game_key is None:
                options = ", ".join(
                    f"`{key}`" + (f" ({info['display']})" if info.get("display") else "")
                    for key, info in game_terms.items())
                raise CopypastaError(
                    f"`{type_id}` doesn't recognise game '{game_raw}'. Try one of: {options}.")
            terms = game_terms[game_key]

            def resolve_term(match):
                key = match.group(1)
                return str(terms[key]) if key in terms else match.group(0)

            template = TERM_RE.sub(resolve_term, template)
            # Show the game's canonical display name wherever the plain
            # {game} placeholder appears, regardless of how the user
            # capitalised/spelled the alias they typed.
            if terms.get("display"):
                values[game_index] = terms["display"]

        rendered = template
        for name, value in zip(placeholders, values):
            rendered = rendered.replace(f"{{{name}}}", value)
        return rendered

    def _fill(self, type_id: str, s: dict, template: str, values: list) -> str:
        """Render one template's text from a plain positional `values`
        list, matched 1:1 against THIS template's own placeholders (in
        the order they first appear in its text) - not the type's
        overall declared set, so a template that only uses some of the
        type's placeholder pool only asks for (and inserts) that subset.
        Used by render() (a specific template is already known) and by
        pick() when it's given a plain list (the classic
        `!copypasta <type> <values>` text command, which has no
        placeholder *names* to match by, only values in pool order)."""
        placeholders = extract_placeholders(template) or ["text"]
        values = list(values)
        if len(values) != len(placeholders):
            needed = " ".join(f"{{{name}}}" for name in placeholders)
            raise CopypastaError(
                f"This `{type_id}` template needs {len(placeholders)} value(s) "
                f"({needed}), but got {len(values)}.")
        return self._substitute(type_id, s, template, placeholders, values)

    def _fill_named(self, type_id: str, s: dict, template: str, values_by_name: dict) -> str:
        """Render one template's text from a {placeholder_name: value}
        dict instead of a positional list - used by pick() when the
        caller already knows which name each value belongs to (the
        Random-button form in /copypasta info, which has one labeled
        field per placeholder in the type's *whole* pool). This picked
        template may only use a subset of that pool, so any names in
        values_by_name it doesn't need are simply ignored, matched by
        name rather than truncated by position (which could silently
        keep the wrong ones if the unused placeholder isn't the last
        one, e.g. pool [name, game, character] but this template only
        uses {game}/{character})."""
        placeholders = extract_placeholders(template) or ["text"]
        missing = [p for p in placeholders if p not in values_by_name]
        if missing:
            needed = " ".join(f"{{{name}}}" for name in placeholders)
            raise CopypastaError(
                f"This `{type_id}` template needs {needed}, but no value was given for "
                f"{{{missing[0]}}}.")
        values = [values_by_name[name] for name in placeholders]
        return self._substitute(type_id, s, template, placeholders, values)

    def pick(self, type_id: str, values, avoid_index: int = None):
        """Return (index, rendered_text) for a random template in type_id,
        with each of the type's {placeholder} names replaced by the
        corresponding entry in `values`, and any {{game_term}} tokens
        resolved via the type's optional "game_terms" (see module
        docstring and _fill()).

        `values` may be either a plain list (matched positionally, in
        the type's established placeholder order - the classic
        `!copypasta <type> <values>` text command) or a
        {placeholder_name: value} dict (matched by name, so values for
        placeholders the picked template doesn't use are ignored rather
        than erroring - see pick_named()/_fill_named()).

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

        template = templates[index]
        if isinstance(values, dict):
            rendered = self._fill_named(type_id, s, template, values)
        else:
            rendered = self._fill(type_id, s, template, values)
        return index, rendered

    def render(self, type_id: str, index: int, values):
        """Return rendered_text for a *specific* template (by index) in
        type_id, with placeholders (and any {{game_term}} tokens) filled
        in the same way pick() does. Used when a user picks an exact
        template (e.g. from the dropdown in /copypasta info) instead of
        getting a random one."""
        s = self.get_type(type_id)
        if not s.get("enabled", True):
            raise CopypastaError(f"Copypasta type '{type_id}' is currently disabled.")
        templates = s.get("templates", [])
        if index < 0 or index >= len(templates):
            raise CopypastaError(f"Template index {index} out of range for '{type_id}'.")

        return self._fill(type_id, s, templates[index], values)

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

        # "placeholders" is the union of every placeholder name any
        # template in this type has ever used, not a required set -
        # templates don't have to match each other, so this just grows
        # to include any name this template introduces (see _fill(),
        # which always uses THIS template's own placeholders to decide
        # what's actually needed when rendering it).
        existing = s.get("placeholders") or []
        for name in names:
            if name not in existing:
                existing.append(name)
        s["placeholders"] = existing

        s["templates"].append(template)
        self._save()

    def remove_template(self, type_id: str, index: int):
        s = self.get_type(type_id)
        if index < 0 or index >= len(s["templates"]):
            raise CopypastaError(f"Template index {index} out of range for '{type_id}'.")
        s["templates"].pop(index)
        self._save()
