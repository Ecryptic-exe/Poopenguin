import json
import os

SETTINGS_FILE = 'vote_settings.json'

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return {'required_votes': 3, 'admin_only': False, 'language': {}, 'autoreact': {}}

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def cleanup_autoreact_settings():
    settings = load_settings()
    if 'autoreact' not in settings:
        print("No autoreact settings found.")
        return

    updated = False
    for channel_id, autoreact_settings in list(settings['autoreact'].items()):
        if isinstance(autoreact_settings, str):
            print(f"Converting legacy autoreact setting for channel {channel_id}: {autoreact_settings}")
            settings['autoreact'][channel_id] = {
                'emoji': autoreact_settings,
                'user_id': None
            }
            updated = True

    if updated:
        save_settings(settings)
        print("Legacy autoreact settings converted successfully.")
    else:
        print("No legacy autoreact settings found.")

if __name__ == "__main__":
    cleanup_autoreact_settings()
