"""Async on-demand requests, bounded workers and per-chart result caching."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from .catalog import Song, difficulty_name, normalize
from .worker import SOURCE_FILES


class ChartError(Exception):
    """Safe, human-readable command failure."""


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


class ChartService:
    def __init__(self, settings):
        settings.validate()
        self.settings = settings
        self.fingerprint = settings.engine_fingerprint()
        self.semaphore = asyncio.Semaphore(settings.max_workers)
        self.inflight = {}
        self.lock = asyncio.Lock()
        self.chart_locks = {}

    def chart_folder(self, song, difficulty):
        if not song.song_id.isascii() or not song.song_id.isdecimal():
            raise ChartError('曲目 ID 無效。')
        return self.settings.cache/'charts'/f'chunilib_{song.song_id}_{difficulty_name(difficulty)}'

    def read_cached(self, song, difficulty, detailed=False):
        view_mode = 'full' if detailed else 'focus'
        latest = self.chart_folder(song, difficulty)/f'latest_{view_mode}.json'
        try:
            record = json.loads(latest.read_text(encoding='utf-8'))
            source_time = record.get('source_cached_at', record['cached_at'])
            if (record['fingerprint'] != self.fingerprint
                    or time.time()-min(record['cached_at'], source_time) >= self.settings.cache_ttl
                    or record['song_id'] != song.song_id or record['difficulty'] != difficulty
                    or record['view_mode'] != view_mode):
                return None
            image = self.settings.cache/record['image_relative']
            if not image.resolve().is_relative_to(self.settings.cache.resolve()):
                return None
            if hashlib.sha256(image.read_bytes()).hexdigest() != record['image_sha256']:
                return None
            return {**record, 'image': str(image), 'cache_hit': True, 'source_fetches_this_request': 0,
                    'images_generated_this_request': 0}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def read_snapshot(self, song, difficulty):
        """Reuse a fresh, verified source across views and renderer updates."""
        folder = self.chart_folder(song, difficulty)
        for pointer in (folder/'source.json', folder/'latest.json'):
            try:
                record = json.loads(pointer.read_text(encoding='utf-8'))
                source_time = record.get('source_cached_at', record['cached_at'])
                if (record['song_id'] != song.song_id or record['difficulty'] != difficulty
                        or time.time()-source_time >= self.settings.cache_ttl):
                    continue
                if record.get('snapshot_relative'):
                    snapshot = (self.settings.cache/record['snapshot_relative']).resolve()
                else:
                    snapshot = Path(record['snapshot']).resolve()
                if not snapshot.is_relative_to(self.settings.cache.resolve()):
                    continue
                hashes = record.get('snapshot_sha256')
                if hashes is None:
                    # Old caches already recorded the complete snapshot hashes.
                    plot_manifest = Path(record['plot_manifest']).resolve()
                    if not plot_manifest.is_relative_to(self.settings.cache.resolve()):
                        continue
                    plot = json.loads(plot_manifest.read_text(encoding='utf-8'))
                    if normalize(plot['title']) != normalize(song.title) or plot['difficulty'] != difficulty:
                        continue
                    hashes = plot['source_sha256']
                if set(hashes) != set(SOURCE_FILES) or any(
                        hashlib.sha256((snapshot/name).read_bytes()).hexdigest() != hashes[name]
                        for name in SOURCE_FILES):
                    continue
                manifest = json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
                if (manifest['status'] != 'complete' or manifest['difficulty'] != difficulty
                        or normalize(manifest['title']) != normalize(song.title)):
                    continue
                return {'path': snapshot, 'cached_at': source_time, 'snapshot_sha256': hashes}
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    async def get_chart(self, song, difficulty, seed_snapshot=None, *, detailed=False):
        difficulty = difficulty_name(difficulty)
        if difficulty not in song.difficulties:
            raise ChartError('提供的曲目資料未列出這個難度。')
        cached = self.read_cached(song, difficulty, detailed) if seed_snapshot is None else None
        if cached:
            return cached
        key = song.song_id, difficulty, 'full' if detailed else 'focus'
        async with self.lock:
            task = self.inflight.get(key)
            if task is None:
                if len(self.inflight) >= self.settings.queue_limit:
                    raise ChartError('查圖佇列已滿，請稍後再試。')
                task = asyncio.create_task(self._produce(song, difficulty, seed_snapshot, detailed))
                self.inflight[key] = task
                def cleanup(completed):
                    if self.inflight.get(key) is completed:
                        self.inflight.pop(key, None)
                    if not completed.cancelled():
                        completed.exception()
                task.add_done_callback(cleanup)
        return await asyncio.shield(task)

    async def _produce(self, song, difficulty, seed_snapshot, detailed=False):
        view_mode = 'full' if detailed else 'focus'
        # Different views serialize only for the same chart, sharing one fetch.
        chart_lock = self.chart_locks.setdefault((song.song_id, difficulty), asyncio.Lock())
        async with chart_lock:
            async with self.semaphore:
                cached = self.read_cached(song, difficulty, detailed) if seed_snapshot is None else None
                if cached:
                    return cached
                source = self.read_snapshot(song, difficulty) if seed_snapshot is None else None
                if source:
                    seed_snapshot = source['path']
                folder = self.chart_folder(song, difficulty)
                job = folder/'jobs'/uuid4().hex
                job.mkdir(parents=True, exist_ok=False)
                atomic_json(job/'request.json', {'song_id': song.song_id, 'title': song.title,
                    'difficulty': difficulty, 'view_mode': view_mode, 'requested_at': time.time(),
                    'scope': 'one selected chart and view', 'source_cache_hit': source is not None,
                    'llm_analysis_used': False})
                command = [sys.executable, '-X', 'utf8', '-m', 'ratingbot.worker', '--job-dir', str(job),
                    '--title', song.title, '--difficulty', difficulty, '--min-players', str(self.settings.min_players),
                    '--view-mode', view_mode, '--spread-threshold', str(self.settings.spread_threshold),
                    '--tail-threshold', str(self.settings.tail_threshold), '--eating-rating-margin', str(self.settings.eating_rating_margin),
                    '--signal-min-groups', str(self.settings.signal_min_groups), '--signal-min-span', str(self.settings.signal_min_span)]
                if seed_snapshot is not None:
                    command.extend(['--seed-snapshot', str(Path(seed_snapshot).resolve())])
                if self.settings.offline:
                    command.append('--offline')
                result = await self._run_worker(command, job)
                if result.get('status') != 'complete':
                    raise ChartError(result.get('error', '圖表處理失敗。'))
                image = Path(result['image']).resolve()
                snapshot = Path(result['snapshot']).resolve()
                if (not image.is_relative_to(job.resolve()) or not image.is_file()
                        or not snapshot.is_relative_to(job.resolve()) or result['view_mode'] != view_mode):
                    raise ChartError('圖表輸出路徑或視圖驗證失敗。')
                now = time.time()
                source_time = source['cached_at'] if source else now
                record = {**result, 'song_id': song.song_id, 'catalogue_title': song.title,
                    'difficulty': difficulty, 'view_mode': view_mode,
                    'cached_at': now, 'source_cached_at': source_time, 'fingerprint': self.fingerprint,
                    'image_relative': image.relative_to(self.settings.cache.resolve()).as_posix(),
                    'snapshot_relative': snapshot.relative_to(self.settings.cache.resolve()).as_posix(),
                    'cache_hit': False, 'source_cache_hit': source is not None,
                    'source_fetches_this_request': result['source_fetches'], 'images_generated_this_request': 1}
                atomic_json(folder/'source.json', {'song_id': song.song_id, 'difficulty': difficulty,
                    'cached_at': source_time, 'snapshot_relative': record['snapshot_relative'],
                    'snapshot_sha256': result['snapshot_sha256']})
                atomic_json(folder/f'latest_{view_mode}.json', record)
                atomic_json(folder/'latest.json', record)
                return record

    async def _run_worker(self, command, job):
        environment = os.environ.copy()
        for name in ('DISCORD_TOKEN', 'DISCORD_BOT_TOKEN', 'BOT_TOKEN', 'TOKEN'):
            environment.pop(name, None)
        process = await asyncio.create_subprocess_exec(*command, cwd=self.settings.root, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.settings.worker_timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            atomic_json(job/'result.json', {'status': 'failed', 'error': 'timeout or cancelled', 'images_generated': 0})
            if isinstance(error, asyncio.CancelledError):
                raise
            raise ChartError('來源或產圖逾時，已停止本次請求。') from None
        (job/'process_stderr.txt').write_bytes(stderr)
        try:
            result = json.loads(stdout.decode('utf-8').strip().splitlines()[-1])
            if process.returncode and result.get('status') == 'complete':
                raise ValueError('worker reported success with nonzero exit status')
            return result
        except (ValueError, IndexError):
            raise ChartError('產圖程序未回傳有效結果；已保存錯誤紀錄。') from None

    async def close(self):
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
