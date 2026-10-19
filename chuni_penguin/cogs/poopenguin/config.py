"""
Central place for file paths and generic JSON load/save helpers.

Every persistent JSON file the bot uses lives under DATA_DIR so the
project root stays clean.
"""
import json
import os

DATA_DIR = os.path.abspath(os.path.join("data", "poopenguin"))

SETTINGS_FILE = os.path.join(DATA_DIR, "vote_settings.json")
VOTES_FILE = os.path.join(DATA_DIR, "votes.json")
KEYWORDS_FILE = os.path.join(DATA_DIR, "keyword_sets.json")
COPYPASTA_FILE = os.path.join(DATA_DIR, "copypasta_sets.json")
GACHA_POOL_FILE = os.path.join(DATA_DIR, "gacha_pool.json")
GACHA_USERS_FILE = os.path.join(DATA_DIR, "gacha_users.json")
GACHA_IMAGES_FILE = os.path.join(DATA_DIR, "gacha_images.json")

DEFAULT_SETTINGS = {"required_votes": 3, "admin_only": False, "language": {}, "autoreact": {}}
DEFAULT_KEYWORDS = {"sets": {}}
DEFAULT_COPYPASTA = {"types": {}}

# Default gacha pool - only used if data/gacha_pool.json doesn't exist yet
# (first run). Once created, the file on disk is the source of truth and
# can be hand-edited at any time (rates, "featured" rate-up character,
# and the three/two/one-star rosters) - see gacha_manager.py.
DEFAULT_GACHA_POOL = {
    "rates": {"three_star": 1.6, "two_star": 8.4, "one_star": 90.0},
    "featured": "ショウニペンギン",
    "three_star": [
        "ショウニペンギン",
        "チュウニペンギン",
        "チュウニペンギン/サウンドパレード!!",
        "チュウニペンギン/メシア",
        "チュウニペンギン/ラブリーハート",
        "チュウニペンギン/コンダクター",
        "チュウニペンギン/8bit",
        "チュウニペンギン/ボクノリレイション",
        "チュウニペンギン/Re:Generation",
        "チュウニペンギン/10th Anniversary",
        "ショウニペンギン/10th Anniversary",
    ],
    "two_star": [
        "虹限スタチュウ",
        "ペンギンスタチュウ",
        "ショウニスタチュウ",
        "ソウルオブスタチュウ",
        "CHUNITHM 10TH ANNIVERSARY"
    ],
    "one_star": ["💩"],
}
DEFAULT_GACHA_USERS = {"users": {}}

# Maps character name (must match the exact roster string in gacha_pool.json)
# -> a direct image URL. Hand-editable like gacha_pool.json - fill it in with
# artwork links, then run `!gacha reload` to pick the changes up live. A
# character with no entry here just won't show an image, everything else
# still works normally.
DEFAULT_GACHA_IMAGES = {}

def _load(path, default):
    if not os.path.exists(path):
        return json.loads(json.dumps(default))
    try:
        with open(path, encoding="utf-8-sig") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("Invalid storage document")
        return value
    except (ValueError, OSError):
        raise RuntimeError("Poopenguin storage is unreadable; restore the backup before writing.") from None


def _save(path, data):
    import shutil
    import tempfile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        _load(path, {})  # fail closed on corrupt existing data
        shutil.copy2(path, path + ".bak")
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_settings():
    return _load(SETTINGS_FILE, DEFAULT_SETTINGS)

def save_settings(settings):
    _save(SETTINGS_FILE, settings)

def load_votes():
    return _load(VOTES_FILE, {})

def save_votes(votes):
    _save(VOTES_FILE, votes)

def load_keywords():
    return _load(KEYWORDS_FILE, DEFAULT_KEYWORDS)

def save_keywords(data):
    _save(KEYWORDS_FILE, data)

def load_copypasta():
    return _load(COPYPASTA_FILE, DEFAULT_COPYPASTA)


def save_copypasta(data):
    _save(COPYPASTA_FILE, data)


def load_gacha_pool():
    return _load(GACHA_POOL_FILE, DEFAULT_GACHA_POOL)


def save_gacha_pool(data):
    _save(GACHA_POOL_FILE, data)


def load_gacha_users():
    return _load(GACHA_USERS_FILE, DEFAULT_GACHA_USERS)


def save_gacha_users(data):
    _save(GACHA_USERS_FILE, data)


def load_gacha_images():
    return _load(GACHA_IMAGES_FILE, DEFAULT_GACHA_IMAGES)


def save_gacha_images(data):
    _save(GACHA_IMAGES_FILE, data)
