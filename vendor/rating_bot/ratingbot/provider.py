"""Resolve only the selected title to an explicitly matching Chunirec page."""
from __future__ import annotations

import json
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .catalog import normalize

SEARCH_URL = 'https://db.chunirec.net/search'


class SourceError(Exception):
    pass


def find_song(title, folder):
    query = json.dumps(title, ensure_ascii=False)
    with requests.get(SEARCH_URL, params={'q': query}, timeout=(8, 20), stream=True,
                      headers={'User-Agent': 'Mozilla/5.0 (compatible; ChuniRatingBot/0.1)'}) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > 4*1024*1024:
                raise SourceError('搜尋回應超過大小限制。')
        (folder/'search.html').write_bytes(bytes(content))
        provenance = {'query': query, 'url': response.url, 'status': response.status_code}
    soup = BeautifulSoup(bytes(content), 'html.parser')
    matches = {}
    for anchor in soup.find_all('a', href=True):
        source_title = anchor.get_text(' ', strip=True)
        url = urljoin(SEARCH_URL, anchor['href'])
        parts = urlsplit(url)
        if (normalize(source_title) == normalize(title) and parts.scheme == 'https'
                and parts.netloc == 'db.chunirec.net' and parts.path.startswith('/music/')):
            matches[url] = source_title
    provenance['exact_matching_urls'] = matches
    (folder/'search_provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    if len(matches) != 1:
        raise SourceError('Chunirec 沒有找到唯一對應曲目；已保存失敗紀錄。')
    url, canonical = next(iter(matches.items()))
    return canonical, url
