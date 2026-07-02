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
      "enabled": true
    },
    ...
  }
}

Matching rule: a set matches a message if any of its keywords appear
as a case-insensitive substring of the message. If multiple sets match,
one is picked at random. A random response from the matched set is sent
(reproducing the original "some groups had one response, some had a random
pick of several" behaviour uniformly).
"""
import random

from config import load_keywords, save_keywords


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
        """Return (set_id, response) for the first message-matching set,
        chosen at random among all sets that match, or None if no match."""
        self._reload()
        content = content.lower()
        matched = []
        for set_id, s in self._data["sets"].items():
            if not s.get("enabled", True):
                continue
            if any(kw.lower() in content for kw in s.get("keywords", [])):
                matched.append(set_id)
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
        self._data["sets"][set_id] = {"keywords": [], "responses": [], "enabled": True}
        self._save()

    def delete_set(self, set_id: str):
        self.get_set(set_id)  # raises if missing
        del self._data["sets"][set_id]
        self._save()

    def set_enabled(self, set_id: str, enabled: bool):
        s = self.get_set(set_id)
        s["enabled"] = enabled
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
