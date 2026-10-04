"""
Central place for file paths and generic JSON load/save helpers.

Every persistent JSON file the bot uses lives under DATA_DIR so the
project root stays clean.
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

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

# DEBUG: prints once, when config.py is first imported, so you can confirm
# the bot process is reading/writing the exact file you're checking on disk
# (e.g. rules out a second copy of the project, or a container without a
# persistent volume mounted for data/). Safe to delete once confirmed.
print(f"[config.py] DATA_DIR resolved to: {DATA_DIR}")
print(f"[config.py] COPYPASTA_FILE resolved to: {COPYPASTA_FILE}")
print(f"[config.py] COPYPASTA_FILE exists on disk right now: {os.path.exists(COPYPASTA_FILE)}")

def _load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if content.strip():
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                print(f"[config.py] WARNING: {path} contains invalid JSON ({e}). "
                      f"Falling back to default instead of crashing. "
                      f"The bad file was left on disk for inspection.")
        else:
            print(f"[config.py] WARNING: {path} is empty. Falling back to default.")
    return json.loads(json.dumps(default))  # deep copy

def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # DEBUG: confirms every write, with the absolute path and the size of
    # what was actually written to disk. Safe to delete once confirmed.
    size = os.path.getsize(path)
    print(f"[config.py] wrote {size} bytes to: {os.path.abspath(path)}")

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
