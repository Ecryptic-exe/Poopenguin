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

DEFAULT_SETTINGS = {"required_votes": 3, "admin_only": False, "language": {}, "autoreact": {}}
DEFAULT_KEYWORDS = {"sets": {}}
DEFAULT_COPYPASTA = {"types": {}}


def _load(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(json.dumps(default))  # deep copy


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
