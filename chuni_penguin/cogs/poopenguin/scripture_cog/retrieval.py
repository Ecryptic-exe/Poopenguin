"""Local BM25 candidate retrieval for Chinese and English verse records."""

import re
from array import array
from collections import Counter, defaultdict
from heapq import nlargest
from math import log1p

CJK_LETTERS = r"\u2e80-\u9fff\uf900-\ufaff\U00020000-\U000323af"
CJK = re.compile(rf"[{CJK_LETTERS}]")
WORDS = re.compile(rf"[{CJK_LETTERS}]+|(?:(?![{CJK_LETTERS}])[^\W_])+")


def terms(text: str):
    for word in WORDS.findall(text):
        if CJK.match(word):
            yield from word
            yield from (word[index : index + 2] for index in range(len(word) - 1))
        else:
            yield word
            yield from ("~" + word[index : index + 3] for index in range(len(word) - 2))


class BM25:
    def __init__(self, documents: tuple[str, ...]) -> None:
        self._documents = documents
        postings = defaultdict(lambda: (array("I"), array("f")))
        lengths = []
        for index, document in enumerate(documents):
            frequencies = Counter(terms(document))
            lengths.append(sum(frequencies.values()))
            for term, frequency in frequencies.items():
                rows, weights = postings[term]
                rows.append(index)
                weights.append(frequency)

        count = len(documents)
        average = sum(lengths) / count if count and any(lengths) else 1.0
        norms = [1.2 * (0.25 + 0.75 * length / average) for length in lengths]
        self._idf = {}
        for term, (rows, weights) in postings.items():
            self._idf[term] = log1p((count - len(rows) + 0.5) / (len(rows) + 0.5))
            for position, (index, frequency) in enumerate(
                zip(rows, weights, strict=True)
            ):
                weights[position] = 2.2 * frequency / (frequency + norms[index])
        self._postings = postings

    def candidates(self, query: str, limit: int = 24) -> list[str]:
        scores = defaultdict(float)
        # Preserve term order so floating-point accumulation is reproducible.
        for term in dict.fromkeys(terms(query)):
            entry = self._postings.get(term)
            if entry is not None:
                idf = self._idf[term]
                if term.startswith("~"):
                    idf *= 0.25
                for index, weight in zip(*entry, strict=True):
                    scores[index] += idf * weight
        ranked = nlargest(limit, scores, key=lambda index: (scores[index], -index))
        return [self._documents[index] for index in ranked]
