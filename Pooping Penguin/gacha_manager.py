"""
Manages the gacha pull system.

Two pieces of persistent state, both plain JSON so either can be
hand-edited at any time without touching code (see config.py):

  data/gacha_pool.json   the "banner": pull rates + the three/two/one
                          star character rosters, plus which 3-star
                          character is currently "featured". This is
                          global - one banner for every server the
                          bot is in, same as keyword_sets.json.

  data/gacha_users.json  one independent record per user: total pull
                          count, pity counter, personal pity TARGET,
                          and how many of each 2-star/3-star character
                          they've ever pulled. 1-star results aren't
                          tracked per-character.

  data/gacha_images.json character -> artwork. Each entry is either a
                          plain URL string (full artwork; thumbnails
                          fall back to it) or a {"image", "thumbnail"}
                          dict for characters that also have a separate
                          smaller/cropped thumbnail on file - see
                          get_image/get_thumbnail/set_thumbnail below.

Personal target:
  Each user has their own `target` - a 3-star character THEY chose
  via GachaCog's `!gacha target`. Pity works toward that user's own
  choice, completely independent of every other user; two users can
  be chasing two different characters off the same banner at the
  same time with no interaction between their counters.

  A user who hasn't set a personal target yet falls back to the
  banner-wide `featured` character from gacha_pool.json (admin-set
  via set_featured()) - see effective_target(). This just keeps
  pulling meaningful for someone who hasn't picked a target yet; the
  moment they set one with set_user_target(), theirs takes over.

Pity rule:
  Every pull increments the user's pity_counter by 1. The counter
  resets to 0 only when the user obtains their OWN effective target
  (personal target, or the banner featured character as fallback) -
  whether that happened from a normal roll landing on it, or from
  the hard-pity force below. Pulling a *different* 3-star character
  (including someone else's target) does NOT reset the counter,
  since the guarantee is specifically about that user's own target.

  If pity_counter reaches PITY_LIMIT (200) without the effective
  target having come up naturally, that pull is forced to BE the
  effective target, guaranteeing it by the 200th pull. If the user
  has no personal target AND no banner featured character is set,
  there's nothing to force toward, so the pull falls back to a
  normal roll instead (pity_counter is left as-is, still counting,
  in case a target gets set later).

  Changing a personal target (set_user_target) or the banner
  featured character (set_featured) does NOT reset any pity progress
  - it only changes what the next guarantee pays out.
"""
import random

from config import (
    load_gacha_pool, save_gacha_pool,
    load_gacha_users, save_gacha_users,
    load_gacha_images, save_gacha_images,
)

PITY_LIMIT = 200
RARITIES = ("three_star", "two_star", "one_star")
STARS = {"three_star": "★★★", "two_star": "★★", "one_star": "★"}


class GachaError(Exception):
    """Raised for invalid gacha operations (unknown character, etc)."""


class GachaManager:
    def __init__(self):
        self._pool = load_gacha_pool()
        self._users = load_gacha_users()
        self._users.setdefault("users", {})
        self._images = load_gacha_images()

    # -- persistence -----------------------------------------------------
    def _save_pool(self):
        save_gacha_pool(self._pool)

    def _save_users(self):
        save_gacha_users(self._users)

    def _save_images(self):
        save_gacha_images(self._images)

    def _reload_pool(self):
        self._pool = load_gacha_pool()

    def _reload_users(self):
        self._users = load_gacha_users()
        self._users.setdefault("users", {})

    def _reload_images(self):
        self._images = load_gacha_images()

    def reload_pool(self):
        """Re-reads data/gacha_pool.json AND data/gacha_images.json from
        disk. Lets an admin hand-edit rates/rosters/artwork links and
        have the running bot pick the change up without a restart."""
        self._reload_pool()
        self._reload_images()

    # -- pool info ---------------------------------------------------------
    def get_pool(self):
        self._reload_pool()
        return self._pool

    def get_featured(self):
        self._reload_pool()
        return self._pool.get("featured")

    def set_featured(self, character: str):
        self._reload_pool()
        if character not in self._pool.get("three_star", []):
            raise GachaError(f"'{character}' is not in the 3-star pool.")
        self._pool["featured"] = character
        self._save_pool()

    # -- character artwork ---------------------------------------------
    # Each entry in data/gacha_images.json is either the legacy plain
    # URL string (full artwork only, thumbnail falls back to it) or a
    # {"image": ..., "thumbnail": ...} dict for characters that also
    # have a separate, smaller/cropped thumbnail on file. Both forms
    # can be freely hand-mixed in the file - see _normalize_image_entry.
    def _normalize_image_entry(self, entry):
        if isinstance(entry, str):
            return {"image": entry, "thumbnail": None}
        if isinstance(entry, dict):
            return {"image": entry.get("image"), "thumbnail": entry.get("thumbnail")}
        return {"image": None, "thumbnail": None}

    def get_image(self, character):
        """Full-size artwork URL for a character, or None if nothing's
        been set for them yet in data/gacha_images.json."""
        self._reload_images()
        entry = self._images.get(character)
        return self._normalize_image_entry(entry)["image"] if entry else None

    def get_thumbnail(self, character):
        """Small thumbnail URL for a character. Falls back to the full
        artwork URL if no separate thumbnail is on file, so existing
        (plain-string) entries keep working with no edits needed."""
        self._reload_images()
        entry = self._images.get(character)
        if not entry:
            return None
        norm = self._normalize_image_entry(entry)
        return norm["thumbnail"] or norm["image"]

    def set_image(self, character: str, url: str):
        """Admin-only in practice (gated in the cog): points a character
        at its full-artwork image URL. Doesn't require the character to
        currently be in the pool, so artwork can be set up ahead of a
        roster change. Preserves an existing thumbnail, if any."""
        self._reload_images()
        existing = self._normalize_image_entry(self._images.get(character, {}))
        existing["image"] = url
        # Keep the on-disk format minimal: plain string when there's no
        # separate thumbnail, dict only once one is actually set.
        self._images[character] = existing if existing["thumbnail"] else url
        self._save_images()

    def set_thumbnail(self, character: str, url: str):
        """Admin-only in practice (gated in the cog): points a character
        at a separate, smaller thumbnail URL - shown on pull results and
        the collection view instead of the full artwork. Doesn't require
        the character to have full artwork set first."""
        self._reload_images()
        existing = self._normalize_image_entry(self._images.get(character, {}))
        existing["thumbnail"] = url
        self._images[character] = existing
        self._save_images()

    def remove_image(self, character: str):
        self._reload_images()
        if character in self._images:
            del self._images[character]
            self._save_images()

    def remove_thumbnail(self, character: str):
        """Clears just the separate thumbnail, leaving full artwork (if
        any) in place - future thumbnail lookups fall back to it."""
        self._reload_images()
        entry = self._images.get(character)
        if not entry:
            return
        norm = self._normalize_image_entry(entry)
        norm["thumbnail"] = None
        if norm["image"]:
            self._images[character] = norm["image"]
        else:
            del self._images[character]
        self._save_images()

    # -- user records ------------------------------------------------------
    def _new_user(self):
        return {
            "total_pulls": 0,
            "pity_counter": 0,
            "target": None,
            "three_star": {},
            "two_star": {},
        }

    def get_user(self, user_id):
        self._reload_users()
        user = self._users["users"].get(str(user_id), self._new_user())
        user.setdefault("target", None)  # older records predate this field
        return user

    def reset_user(self, user_id):
        self._reload_users()
        self._users["users"][str(user_id)] = self._new_user()
        self._save_users()

    # -- personal pity target ----------------------------------------------
    def get_user_target(self, user_id):
        """The character this user personally chose to pity toward, or
        None if they haven't set one yet."""
        return self.get_user(user_id).get("target")

    def effective_target(self, user_id):
        """What pity actually resolves against for this user: their own
        target if set, otherwise the banner-wide featured character as a
        fallback for users who haven't picked one yet."""
        return self.get_user_target(user_id) or self.get_featured()

    def set_user_target(self, user_id: str, character: str):
        """Sets this user's personal pity target. Does not touch their
        pity_counter - only what the next guarantee pays out."""
        self._reload_pool()
        if character not in self._pool.get("three_star", []):
            raise GachaError(f"'{character}' is not in the 3-star pool.")
        self._reload_users()
        uid = str(user_id)
        user = self._users["users"].setdefault(uid, self._new_user())
        user.setdefault("target", None)
        user["target"] = character
        self._save_users()

    def clear_user_target(self, user_id: str):
        """Unsets this user's personal target, falling back to the
        banner featured character (if any)."""
        self._reload_users()
        uid = str(user_id)
        user = self._users["users"].setdefault(uid, self._new_user())
        user.setdefault("target", None)
        user["target"] = None
        self._save_users()

    # -- rolling -------------------------------------------------------
    def _roll_rarity(self):
        rates = self._pool["rates"]
        roll = random.uniform(0, 100)
        if roll < rates["three_star"]:
            return "three_star"
        if roll < rates["three_star"] + rates["two_star"]:
            return "two_star"
        return "one_star"

    def _pull_single(self, user):
        # Each user's pity is resolved against THEIR OWN target (falling
        # back to the banner featured character if they haven't set one) -
        # never the global featured character directly, so two users can
        # be independently chasing two different 3-stars off one banner.
        target = user.get("target") or self._pool.get("featured")
        user["total_pulls"] += 1
        user["pity_counter"] += 1
        forced = user["pity_counter"] >= PITY_LIMIT and bool(target)

        if forced:
            rarity = "three_star"
            character = target
        else:
            rarity = self._roll_rarity()
            pool_list = self._pool.get(rarity, [])
            character = random.choice(pool_list) if pool_list else None

        if rarity == "three_star" and character is not None and character == target:
            user["pity_counter"] = 0

        if rarity in ("three_star", "two_star") and character is not None:
            bucket = user[rarity]
            bucket[character] = bucket.get(character, 0) + 1

        return {
            "rarity": rarity,
            "character": character,
            "pity_counter": user["pity_counter"],
            "forced": forced,
            "target": target,
        }

    def pull(self, user_id: str, count: int = 1):
        """Runs `count` pulls for user_id in one persisted batch.
        Returns (results, updated_user_record)."""
        self._reload_pool()
        self._reload_users()
        uid = str(user_id)
        user = self._users["users"].setdefault(uid, self._new_user())
        results = [self._pull_single(user) for _ in range(count)]
        self._save_users()
        return results, user