"""Library stats: words in names (the counting; the queries are in api/routes/library_stats)."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

# Words left out of the word clouds: articles, prepositions, conjunctions and pronouns in the
# languages most music titles are in, plus what titles are full of ("feat", "remastered").
STOP_WORDS = frozenset(
    """
    a an the and or but nor of in on at to for from by with without into onto over under
    up down out off about as is are was were be been am it its it's i i'm im me my you
    you're your we our us he she his her they them their this that these those there here
    not no so if do don't dont can can't cant will won't just all what when where who why
    how one two oh ah la na da de del della di du des le les un une et en au aux ou sur
    der die das den dem ein eine einen und oder mit von zu im am ist ich du er sie es wir
    ihr el los las y o con por para al lo mi tu su se que och att det som en ett jag du
    han hon vi ni de dem är var på av med för till från om men inte så het een van op
    met voor naar niet je ik il lo gli e per non che ma o da na um uma os as do da em no
    feat ft featuring vs remaster remastered remix mix version edit live demo mono stereo
    deluxe edition bonus track tracks single ep lp vol volume part pt intro outro interlude
    reprise instrumental original disc cd
    """.split()
)
APOSTROPHE = "\u2019"  # the typographic one, read as a plain apostrophe
WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)?", re.UNICODE)
# Brackets hold versions and guests ("(2011 Remaster)", "[feat. X]"), not the name.
BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def words(names: Iterable[str], limit: int = 120) -> list[tuple[str, int]]:
    """The most common words in these names (each name counts a word once), skipping stop
    words and words shorter than two letters. Case-folded; shown as most often written."""
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    for name in names:
        seen = set()
        for match in WORD.finditer(BRACKETS.sub(" ", unicodedata.normalize("NFC", name))):
            word = match.group().replace(APOSTROPHE, "'")
            key = word.casefold()
            if len(key) < 2 or key in STOP_WORDS or key in seen:
                continue
            seen.add(key)
            counts[key] += 1
            spellings.setdefault(key, Counter())[word] += 1
    return [(spellings[k].most_common(1)[0][0], n) for k, n in counts.most_common(limit)]
