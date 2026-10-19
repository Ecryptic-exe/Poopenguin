"""Collect one public source by the catalog's exact Chunirec ID for C's engine."""

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DIFFICULTIES = {
    "BAS": "BASIC",
    "ADV": "ADVANCED",
    "EXP": "EXPERT",
    "MAS": "MASTER",
    "ULT": "ULTIMA",
}
SOURCE_FILES = (
    "manifest.json",
    "selected_stats.json",
    "embedded_stats.txt",
    "rating_score_table.csv",
    "rating_rank_counts.csv",
    "source.html",
    "music.js",
)


class DistributionUnavailable(Exception):
    """A safe message suitable for the selected chart's distribution page."""


def source_id(chart):
    if chart.difficulty not in DIFFICULTIES:
        raise DistributionUnavailable(
            "Chunirec 沒有提供 WORLD’S END 的這項成績統計，請選擇一般難度。"
        )
    native_id = chart.song.chunirec_id
    if not isinstance(native_id, str) or not re.fullmatch(
        r"[0-9a-fA-F]{16}", native_id
    ):
        raise DistributionUnavailable(
            "此曲尚未建立可信的 Chunirec 對應，暫時無法取得成績分佈。"
        )
    return native_id.lower()


def source_url(title, native_id):
    if not re.fullmatch(r"[0-9a-f]{16}", native_id):
        raise ValueError("Invalid Chunirec identity")
    return f"https://db.chunirec.net/music/{quote(title, safe='')}/{native_id}"


def validate_snapshot(path, *, title, difficulty, native_id):
    """Check identity and provenance before a snapshot can reach the renderer."""
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "complete"
        or manifest.get("title") != title
        or manifest.get("difficulty") != difficulty
    ):
        raise DistributionUnavailable(
            "統計來源與所選歌曲／難度不一致，未顯示這份資料。"
        )
    urls = [manifest.get("source_url", "")]
    fetches = manifest.get("fetches", [])
    if fetches:
        urls.append(fetches[0].get("final_url", ""))
    for url in urls:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "db.chunirec.net"
            or parsed.path.rstrip("/").split("/")[-1].lower() != native_id
        ):
            raise DistributionUnavailable("統計來源的曲目編號不符，未顯示這份資料。")
    observed = (
        fetches[0].get("retrieved_at") if fetches else manifest.get("completed_at")
    )
    if not observed or type(manifest.get("total_players")) is not int:
        raise DistributionUnavailable("統計來源缺少樣本或擷取日期，請稍後再試。")
    hashes = {
        name: hashlib.sha256((path / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }
    return {
        "chunirec_id": native_id,
        "source_url": manifest["source_url"],
        "players": manifest["total_players"],
        "observed_at": observed,
        "constant": manifest["chart"]["const"],
        "hashes": hashes,
    }


async def collect_source(root, *, title, difficulty, native_id, timeout=100):
    """The collector runs in a killable process; it never imports a bot client."""
    job = root / "data/cache/statistics/sources" / uuid4().hex
    job.mkdir(parents=True)
    snapshot = job / "source_snapshot"
    environment = os.environ.copy()
    for name in ("DISCORD_TOKEN", "DISCORD_BOT_TOKEN", "BOT_TOKEN", "TOKEN"):
        environment.pop(name, None)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "chuni_penguin.services.distribution_source",
        "--title",
        title,
        "--difficulty",
        difficulty,
        "--source-id",
        native_id,
        "--output",
        str(snapshot.resolve()),
        cwd=root,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
            await process.communicate()
        raise
    (job / "process_stderr.txt").write_bytes(stderr)
    if process.returncode != 0:
        raise DistributionUnavailable(
            "Chunirec 統計暫時無法取得，請稍後重新選擇「成績分佈」重試。"
        )
    try:
        result = json.loads(stdout.decode("utf-8").splitlines()[-1])
        if result["status"] != "complete":
            raise ValueError
        validate_snapshot(
            snapshot, title=title, difficulty=difficulty, native_id=native_id
        )
    except (OSError, ValueError, KeyError, IndexError) as error:
        raise DistributionUnavailable(
            "Chunirec 回傳的統計資料不完整，請稍後重試。"
        ) from error
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--difficulty", choices=DIFFICULTIES.values(), required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / "data/cache/statistics/sources").resolve()):
        raise ValueError("Invalid statistics output path")
    spec = importlib.util.spec_from_file_location(
        "_distribution_collector",
        ROOT / "vendor/rating_bot/engine/collect_chunirec_rating_table.py",
    )
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            collector.run(
                SimpleNamespace(
                    title=args.title,
                    difficulty=args.difficulty,
                    url=source_url(args.title, args.source_id),
                    output=output,
                )
            )
        validate_snapshot(
            output,
            title=args.title,
            difficulty=args.difficulty,
            native_id=args.source_id,
        )
        result = {"status": "complete"}
    except (Exception, SystemExit):
        result = {"status": "failed"}
    (output.parent / "result.json").write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
