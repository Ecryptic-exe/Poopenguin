"""Read existing Discord command scopes, never log credentials or guild names."""
import asyncio
import json
from pathlib import Path

import httpx

from chuni_penguin.application.credentials import read_env_file, token_from_env

ROOT = Path(__file__).resolve().parents[1]


async def main():
    token = token_from_env(read_env_file(ROOT / '.env'))
    async with httpx.AsyncClient(base_url='https://discord.com/api/v10', headers={'Authorization': 'Bot ' + token}, timeout=20) as client:
        async def get(path):
            response = await client.get(path)
            response.raise_for_status()
            return response.json()
        app = await get('/oauth2/applications/@me')
        app_id = app['id']
        global_commands = await get(f'/applications/{app_id}/commands')
        guilds = await get('/users/@me/guilds')
        scopes = [{'guild_id': None, 'commands': global_commands}]
        for guild in guilds[:50]:
            commands = await get(f'/applications/{app_id}/guilds/{guild["id"]}/commands')
            scopes.append({'guild_id': guild['id'], 'commands': commands})
    payload = {'application_id': app_id, 'scopes': scopes, 'guild_count': len(guilds), 'write_operations': 0}
    destination = ROOT / 'data/runtime/command-scopes-before.json'
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'global_roots': [x['name'] for x in global_commands], 'guild_count': len(guilds), 'registered_guild_scopes': sum(bool(s['commands']) for s in scopes if s['guild_id']), 'remote_writes': 0}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
