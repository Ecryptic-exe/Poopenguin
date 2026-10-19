"""Offline search/index utilities and explicit single-chart commands."""
import argparse
import asyncio
import json
from pathlib import Path

from .catalog import DIFFICULTIES, build_catalog
from .config import Settings, ensure_catalog
from .service import ChartService, ChartError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    index = sub.add_parser('build-index', help='Extract all catalogue metadata; zero chart requests')
    index.add_argument('--source', type=Path)
    search = sub.add_parser('search', help='Offline BM25 candidates')
    search.add_argument('query')
    search.add_argument('--difficulty', choices=DIFFICULTIES)
    search.add_argument('--limit', type=int, default=10)
    for name in ('chart', 'seed-cache'):
        command = sub.add_parser(name, help='One explicitly selected chart only')
        command.add_argument('--song', required=True, help='Exact title or id:SOURCE_ID')
        command.add_argument('--difficulty', choices=DIFFICULTIES, default='MASTER')
        command.add_argument('--snapshot', type=Path, required=name == 'seed-cache')
        command.add_argument('--offline', action='store_true')
        command.add_argument('--detailed', action='store_true', help='Full score and player range instead of the default SS focus')
    sub.add_parser('check', help='Offline startup check, without token disclosure')
    args = parser.parse_args()
    settings = Settings.load()
    if args.command == 'build-index' and args.source:
        result = build_catalog(args.source.resolve(), settings.root/'data/catalog.json')
        print(json.dumps({k:v for k,v in result.items() if k != 'songs'}, ensure_ascii=False))
        return
    if args.command == 'check':
        from .bot import run
        run(['--check'])
        return
    catalog = ensure_catalog(settings.root)
    if args.command == 'build-index':
        print(json.dumps({k:v for k,v in catalog.metadata.items() if k != 'songs'}, ensure_ascii=False))
    elif args.command == 'search':
        print(json.dumps([{'song_id':r['song'].song_id, 'title':r['song'].title,
            'difficulties':r['song'].difficulties, 'bm25_score':round(r['score'], 5), 'exact':r['exact']}
            for r in catalog.search(args.query, args.difficulty, min(25,max(1,args.limit)))], ensure_ascii=False, indent=2))
    else:
        matches = catalog.exact_matches(args.song, args.difficulty)
        if len(matches) != 1:
            raise SystemExit('先使用 search 選定曲目，再傳入唯一正式曲名或 id:SOURCE_ID。')
        settings.offline = args.offline
        async def produce_one():
            service = ChartService(settings)
            try:
                result = await service.get_chart(matches[0], args.difficulty, args.snapshot, detailed=args.detailed)
                print(json.dumps(result, ensure_ascii=False))
            finally:
                await service.close()
        try:
            asyncio.run(produce_one())
        except ChartError as error:
            raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
