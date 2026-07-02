"""
One-off migration script.

The original vto.py had a single giant if/elif chain in on_message() that
matched ~15 different keyword groups against hardcoded copypasta text
imported from copypasta.py. That made it impossible to add/remove/edit a
keyword trigger without touching source code and redeploying.

This script converts that hardcoded chain into data/keyword_sets.json,
which is read and managed at runtime by keyword_manager.py and the
!keyword bot commands (see cogs/keywords_cog.py).

You only need to run this once (it has already been run to produce the
data/keyword_sets.json shipped alongside this script). It's kept here so
the mapping from "old hardcoded branch" -> "new keyword set" is documented
and reproducible.
"""
import json
import os

from legacy_copypasta import (
    COPYPASTA_BBSONG, COPYPASTA_AIAIAI, COPYPASTA_CRY, COPYPASTA_DIUDIUDIU,
    COPYPASTA_XEVEL, COPYPASTA_X7124P1, COPYPASTA_X7124P2, COPYPASTA_X7124P3,
    COPYPASTA_X7124P4, COPYPASTA_MARSHMELLOWRABBIT1, COPYPASTA_MARSHMELLOWRABBIT2,
    COPYPASTA_MARSHMELLOWRABBIT3, COPYPASTA_MARSHMELLOWRABBIT4,
    COPYPASTA_MARSHMELLOWRABBIT5, COPYPASTA_MARSHMELLOWRABBIT6,
    COPYPASTA_MARSHMELLOWRABBIT7, COPYPASTA_MARSHMELLOWRABBIT8,
    COPYPASTA_MARSHMELLOWRABBIT9, COPYPASTA_MARSHMELLOWRABBIT10,
    COPYPASTA_INSTANTNOODLES, COPYPASTA_7381, COPYPASTA_WHOFINGER,
    COPYPASTA_0KNOW, COPYPASTA_MEMEME, COPYPASTA_4PITAYA,
    COPYPASTA_HARRYCH1, COPYPASTA_HARRYCH2, COPYPASTA_HARRYCH3, COPYPASTA_HARRYCH4,
    COPYPASTA_LOONG91, COPYPASTA_LOONG92, COPYPASTA_LOONG93, COPYPASTA_LOONG94,
    COPYPASTA_LOONG95, COPYPASTA_LOONG96, COPYPASTA_LOONG97,
)

# set_id -> (keywords, responses)
# This mirrors the old elif branches exactly, so behaviour is unchanged
# after migration.
SETS = {
    "bbsong": (["兒歌"], [COPYPASTA_BBSONG]),
    "aiaiai": (["唉唉唉", "哎哎哎"], [COPYPASTA_AIAIAI]),
    "cry": (["嗚嗚嗚", "😭😭😭"], [COPYPASTA_CRY]),
    "diudiudiu": (["Diu Diu Diu", "DiuDiuDiu", "屌屌屌"], [COPYPASTA_DIUDIUDIU]),
    "mememe": (["我我我", "me me me", "mememe", "私私私", "吾吾吾"], [COPYPASTA_MEMEME]),
    "giselle": (["giselle", "吉賽兒", "鷄飼料", "雞飼料"], ["狗也不屌"]),
    "pitaya": (["火龍果", "火龍威果", "pitaya", "dragon fruit", "果龍火"], [COPYPASTA_4PITAYA]),
    "wup7381": (
        ["wup", "what's up? pop!", "我操破譜", "臥槽破譜", "woc破譜", "whats up pop", "7381"],
        [COPYPASTA_7381],
    ),
    "whofinger": (["who finger", "誰手指", "世界衛生組織手指"], [COPYPASTA_WHOFINGER]),
    "zeroknow": (["0識", "希望你教", "希望教"], [COPYPASTA_0KNOW]),
    "harry": (
        ["harry", "哈利陳", "狼鬼", "ウルガレオン"],
        [COPYPASTA_HARRYCH1, COPYPASTA_HARRYCH2, COPYPASTA_HARRYCH3, COPYPASTA_HARRYCH4],
    ),
    "loong9": (
        ["loong9", "西龍九", "kyouran"],
        [COPYPASTA_LOONG91, COPYPASTA_LOONG92, COPYPASTA_LOONG93, COPYPASTA_LOONG94,
         COPYPASTA_LOONG95, COPYPASTA_LOONG96, COPYPASTA_LOONG97],
    ),
    "nomoney": (["沒錢", "窮", "冇錢", "無錢", "no money", "身無分文"], ["團長出資"]),
    # Everything else in the original giant keyword list fell through to
    # this "misc/default" bucket and got a random copypasta from it.
    "misc_default": (
        ["nemu", "眠夢", "眼老", "ねむ", "nemumi", "marshmellow rabbit",
         "棉花糖兔", "cinaeco", "海洋", "貓男"],
        [COPYPASTA_XEVEL, COPYPASTA_X7124P1, COPYPASTA_X7124P2, COPYPASTA_X7124P3,
         COPYPASTA_X7124P4, COPYPASTA_MARSHMELLOWRABBIT1, COPYPASTA_MARSHMELLOWRABBIT2,
         COPYPASTA_MARSHMELLOWRABBIT3, COPYPASTA_MARSHMELLOWRABBIT4, COPYPASTA_MARSHMELLOWRABBIT5,
         COPYPASTA_MARSHMELLOWRABBIT6, COPYPASTA_MARSHMELLOWRABBIT7, COPYPASTA_MARSHMELLOWRABBIT8,
         COPYPASTA_MARSHMELLOWRABBIT9, COPYPASTA_MARSHMELLOWRABBIT10, COPYPASTA_INSTANTNOODLES],
    ),
}

# NOTE: legacy_copypasta.py also defines COPYPASTA_REBELLION, COPYPASTA_REBELLION2
# and COPYPASTA_LOONG98, none of which were ever referenced by the old
# on_message() chain. They look like leftover/unused drafts, so they are
# NOT migrated automatically. If you want them available, add them as a
# new set with `!keyword create <name>` and `!keyword addresponse`.


def build():
    data = {"sets": {}}
    for set_id, (keywords, responses) in SETS.items():
        data["sets"][set_id] = {
            "keywords": keywords,
            "responses": responses,
            "enabled": True,
        }
    return data


if __name__ == "__main__":
    out_path = os.path.join(os.path.dirname(__file__), "data", "keyword_sets.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(build(), f, ensure_ascii=False, indent=2)
    print(f"Wrote {out_path} with {len(SETS)} keyword sets.")
