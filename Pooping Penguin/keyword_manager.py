"""
Manages "keyword sets": named groups of trigger keywords + candidate
response texts that the bot scans every message against.

This is intentionally global (one shared data/keyword_sets.json for the
whole bot, not per-server) because that mirrors how the original hardcoded
keyword chain behaved - it fired the same way in every server the bot was
in. If per-server keyword sets are wanted later, this is the module to
extend (key the top-level dict by guild_id instead of being flat).

Data shape (data/keyword_sets.json):
{
  "sets": {
    "<set_id>": {
      "keywords": ["kw1", "kw2", ...],
      "responses": ["resp1", "resp2", ...],
      "enabled": true,
      "trigger_rate": 100
    },
    ...
  }
}

`trigger_rate` is an integer percentage (0-100) giving the independent
chance that THIS set fires once its keyword has matched a message.
Defaults to 100 (always fires) for newly-created sets and for any set
loaded from older data files that predates this field, so existing
keyword sets keep behaving exactly as before unless someone lowers
their rate.

Matching rule: a set is a "candidate" if any of its keywords appear as
a case-insensitive substring of the message. Each candidate then rolls
independently against its own trigger_rate - a set with a 30% rate
only actually fires on ~30% of the messages that contain its keyword,
regardless of what any other set's rate is. If more than one candidate
survives its roll, one of the survivors is picked at random (same as
before). A random response from the chosen set is sent (reproducing
the original "some groups had one response, some had a random pick of
several" behaviour uniformly).
"""
import random

from config import load_keywords, save_keywords

DEFAULT_TRIGGER_RATE = 100


class KeywordError(Exception):
    """Raised for invalid keyword-set operations (bad name, duplicate, etc)."""


class KeywordManager:
    def __init__(self):
        self._data = load_keywords()
        self._data.setdefault("sets", {})

    # -- persistence -----------------------------------------------------
    def _save(self):
        save_keywords(self._data)

    def _reload(self):
        self._data = load_keywords()
        self._data.setdefault("sets", {})

    # -- read --------------------------------------------------------------
    def list_sets(self):
        self._reload()
        return self._data["sets"]

    def get_set(self, set_id: str):
        self._reload()
        s = self._data["sets"].get(set_id)
        if s is None:
            raise KeywordError(f"No keyword set named '{set_id}'.")
        return s

    def find_match(self, content: str):
        """Return (set_id, response) for a matching set, chosen at random
        among the sets that both match by keyword AND survive their own
        independent trigger_rate roll, or None if no set matches / rolls
        through."""
        self._reload()
        content = content.lower()
        candidates = []
        for set_id, s in self._data["sets"].items():
            if not s.get("enabled", True):
                continue
            if any(kw.lower() in content for kw in s.get("keywords", [])):
                candidates.append(set_id)
        if not candidates:
            return None

        matched = [
            set_id for set_id in candidates
            if random.uniform(0, 100) < self._data["sets"][set_id].get("trigger_rate", DEFAULT_TRIGGER_RATE)
        ]
        if not matched:
            return None
        chosen_id = random.choice(matched)
        responses = self._data["sets"][chosen_id].get("responses", [])
        if not responses:
            return None
        return chosen_id, random.choice(responses)

    # -- write ---------------------------------------------------------
    def create_set(self, set_id: str):
        self._reload()
        if set_id in self._data["sets"]:
            raise KeywordError(f"Keyword set '{set_id}' already exists.")
        self._data["sets"][set_id] = {
            "keywords": [],
            "responses": [],
            "enabled": True,
            "trigger_rate": DEFAULT_TRIGGER_RATE,
        }
        self._save()

    def delete_set(self, set_id: str):
        self.get_set(set_id)  # raises if missing
        del self._data["sets"][set_id]
        self._save()

    def set_enabled(self, set_id: str, enabled: bool):
        s = self.get_set(set_id)
        s["enabled"] = enabled
        self._save()

    def set_trigger_rate(self, set_id: str, rate: float):
        """Set the independent per-message fire chance for a set, as a
        percentage from 0 (never fires) to 100 (always fires)."""
        s = self.get_set(set_id)
        if rate < 0 or rate > 100:
            raise KeywordError(f"Trigger rate must be between 0 and 100 (got {rate}).")
        s["trigger_rate"] = rate
        self._save()

    def add_keyword(self, set_id: str, keyword: str):
        s = self.get_set(set_id)
        if keyword in s["keywords"]:
            raise KeywordError(f"'{keyword}' is already a trigger for '{set_id}'.")
        s["keywords"].append(keyword)
        self._save()

    def remove_keyword(self, set_id: str, keyword: str):
        s = self.get_set(set_id)
        if keyword not in s["keywords"]:
            raise KeywordError(f"'{keyword}' is not a trigger for '{set_id}'.")
        s["keywords"].remove(keyword)
        self._save()

    def add_response(self, set_id: str, response: str):
        s = self.get_set(set_id)
        s["responses"].append(response)
        self._save()

    def remove_response(self, set_id: str, index: int):
        s = self.get_set(set_id)
        if index < 0 or index >= len(s["responses"]):
            raise KeywordError(f"Response index {index} out of range for '{set_id}'.")
        s["responses"].pop(index)
        self._save()
