"""
Manages "keyword sets": named groups of trigger keywords + candidate
response texts that the bot scans every message against.

This is intentionally global (one shared data/poopenguin/keyword_sets.json for the
whole bot, not per-server) because that mirrors how the original hardcoded
keyword chain behaved - it fired the same way in every server the bot was
in. If per-server keyword sets are wanted later, this is the module to
extend (key the top-level dict by guild_id instead of being flat).

Data shape (data/poopenguin/keyword_sets.json):
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

`trigger_rate` is a percentage (0-100) for the long-run average chance
that THIS set fires once its keyword has matched a message. A
pseudo-random distribution (PRD) raises the chance after each miss and
resets it after a trigger, reducing streaks while keeping approximately
the configured long-run rate. The PRD history is kept per set and guild
in memory; keyword data itself remains global. History resets if the bot
restarts or the set's trigger rate, enabled state, or keywords change.

Matching rule: a set is a "candidate" if any of its keywords appear as
a case-insensitive substring of the message. Each candidate uses its
own PRD history for that guild. If more than one candidate triggers,
one is picked at random. A random response from the chosen set is sent
(reproducing the original "some groups had one response, some had a
random pick of several" behaviour uniformly).
"""
import math
import random
from functools import lru_cache

from .config import load_keywords, save_keywords

DEFAULT_TRIGGER_RATE = 100


def _expected_prd_interval(constant: float) -> float:
    """Return the expected number of attempts between PRD triggers."""
    survival = 1.0
    expected = 1.0
    attempt = 0
    while survival > 1e-12 and attempt < 100_000:
        attempt += 1
        chance = min(constant * attempt, 1.0)
        survival *= 1.0 - chance
        expected += survival
        if chance >= 1.0:
            break
    return expected


@lru_cache(maxsize=256)
def _prd_constant(rate: float) -> float:
    """Find the PRD ramp constant for the configured average percentage."""
    rate = max(0.0, min(100.0, float(rate)))
    probability = rate / 100.0
    if probability <= 0.0:
        return 0.0
    if probability >= 1.0:
        return 1.0

    # At very low rates the PRD constant approaches pi/2 * p^2. Using
    # that limit avoids a long calibration loop for unusually tiny rates.
    if probability < 0.001:
        return (math.pi / 2.0) * probability * probability

    target_interval = 1.0 / probability
    low, high = 0.0, 1.0
    for _ in range(48):
        middle = (low + high) / 2.0
        if _expected_prd_interval(middle) > target_interval:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _prd_chance(rate: float, misses: int) -> float:
    """Return the current PRD chance as a 0.0-1.0 probability."""
    rate = max(0.0, min(100.0, float(rate)))
    if rate <= 0.0:
        return 0.0
    if rate >= 100.0:
        return 1.0
    return min(_prd_constant(rate) * (misses + 1), 1.0)


class KeywordError(Exception):
    """Raised for invalid keyword-set operations (bad name, duplicate, etc)."""


class KeywordManager:
    def __init__(self):
        self._data = load_keywords()
        self._data.setdefault("sets", {})
        # (guild_id, set_id) -> consecutive matching messages without a trigger.
        self._trigger_misses: dict[tuple[int | None, str], int] = {}

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

    def find_match(self, content: str, guild_id: int | None = None):
        """Return a triggered (set_id, response), or None if none trigger."""
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

        matched = []
        for set_id in candidates:
            key = (guild_id, set_id)
            misses = self._trigger_misses.get(key, 0)
            rate = self._data["sets"][set_id].get("trigger_rate", DEFAULT_TRIGGER_RATE)
            if random.random() < _prd_chance(rate, misses):
                self._trigger_misses[key] = 0
                matched.append(set_id)
            elif float(rate) > 0.0:
                self._trigger_misses[key] = misses + 1
        if not matched:
            return None
        chosen_id = random.choice(matched)
        responses = self._data["sets"][chosen_id].get("responses", [])
        if not responses:
            return None
        return chosen_id, random.choice(responses)

    # -- write ---------------------------------------------------------
    def _reset_trigger_history(self, set_id: str):
        for key in tuple(self._trigger_misses):
            if key[1] == set_id:
                del self._trigger_misses[key]

    def create_set(self, set_id: str):
        self._reload()
        if set_id in self._data["sets"]:
            raise KeywordError(f"Keyword set '{set_id}' already exists.")
        self._reset_trigger_history(set_id)
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
        self._reset_trigger_history(set_id)
        self._save()

    def set_enabled(self, set_id: str, enabled: bool):
        s = self.get_set(set_id)
        s["enabled"] = enabled
        self._reset_trigger_history(set_id)
        self._save()

    def set_trigger_rate(self, set_id: str, rate: float):
        """Set the target average fire rate from 0 (never) to 100 (always)."""
        s = self.get_set(set_id)
        if rate < 0 or rate > 100:
            raise KeywordError(f"Trigger rate must be between 0 and 100 (got {rate}).")
        s["trigger_rate"] = rate
        self._reset_trigger_history(set_id)
        self._save()

    def add_keyword(self, set_id: str, keyword: str):
        s = self.get_set(set_id)
        if keyword in s["keywords"]:
            raise KeywordError(f"'{keyword}' is already a trigger for '{set_id}'.")
        s["keywords"].append(keyword)
        self._reset_trigger_history(set_id)
        self._save()

    def remove_keyword(self, set_id: str, keyword: str):
        s = self.get_set(set_id)
        if keyword not in s["keywords"]:
            raise KeywordError(f"'{keyword}' is not a trigger for '{set_id}'.")
        s["keywords"].remove(keyword)
        self._reset_trigger_history(set_id)
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
