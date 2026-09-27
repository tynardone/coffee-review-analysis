"""Reduce a raw roaster name to a comparable key.

The whole cascade rests on this: once domain stopwords are removed,
"Stumptown" and "Stumptown Coffee Roasters" become the same string and match
on an exact lookup rather than on a threshold. See docs/roaster-resolution.md.
"""

import re
import unicodedata

__all__ = [
    "ABBREV",
    "STOPWORDS",
    "core_key",
    "fingerprint",
    "strip_accents",
    "tokens",
]


# Words shared by most roaster names, which therefore say little about which
# roaster a name refers to. Removing them before measuring distance collapses
# "Stumptown" and "Stumptown Coffee Roasters" to the same key, so they match on
# an exact lookup rather than on a threshold.
#
# Misspellings of the stopwords belong here too ("coffe", "cofee"); they would
# otherwise survive into the key as noise tokens.
# fmt: off
# Grouped by kind; the grouping is what each line documents. Keep the formatter
# from flattening it to one word per line.
STOPWORDS = {
    "coffee", "coffees", "coffe", "cofee",
    "roaster", "roasters", "roasting", "roastery", "roasterie",
    "cafe", "caffe", "kaffee", "espresso", "bean", "beans",
    "co", "company", "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "the", "and",
}
# fmt: on

# Applied BEFORE stopword removal, so that whatever an abbreviation expands to
# can itself be stopworded if it belongs on the list above.
ABBREV = {
    "bros": "brothers",
    "bro": "brothers",
    "mfg": "manufacturing",
    "intl": "international",
    "st": "saint",
    "mt": "mount",
}


def strip_accents(s: str) -> str:
    """Café -> Cafe.

    NFKD splits an accented character into a base plus combining marks, which
    are then dropped. Scraped pages carry both spellings of the same roaster
    depending on which page a name came from.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def tokens(name: str) -> list[str]:
    """Raw name -> clean token list.

    Each step closes one source of spelling noise, and the order matters. Two
    are easy to undo by accident:

    1. Apostrophes are deleted rather than turned into spaces, so "Peet's"
       reaches the same token as "Peets". Splitting on the apostrophe would
       yield ["peet", "s"] and a stray single letter.
    2. Runs of single letters are collapsed. "J.B.C. Coffee Roasters"
       punctuation-strips to ["j", "b", "c", ...], which shares nothing with
       "JBC Coffee" -> ["jbc", ...]. Gluing the run back together makes an
       initialism agree with its solid form.
    """
    s = strip_accents(str(name)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"['\u2019]", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = [ABBREV.get(t, t) for t in s.split()]

    out: list[str] = []
    run: list[str] = []
    for t in toks:
        if len(t) == 1 and t.isalpha():
            run.append(t)
        else:
            if run:
                out.append("".join(run))
                run = []
            out.append(t)
    if run:
        out.append("".join(run))
    return out


def fingerprint(name: str) -> str:
    """OpenRefine's key-collision fingerprint, reimplemented.

    Dedupe + sort the tokens, so word order stops mattering. Kept as a distinct
    function because it's the safe fallback when stopwording is too aggressive
    (see core_key).
    """
    return " ".join(sorted(set(tokens(name))))


def core_key(name: str) -> str:
    """Fingerprint with domain stopwords removed. The workhorse.

        "Stumptown"                 -> "stumptown"
        "Stumptown Coffee"          -> "stumptown"
        "Stumptown Roasters"        -> "stumptown"
        "Stumptown Coffee Roasters" -> "stumptown"

    A name composed entirely of stopwords, such as "The Coffee Company",
    reduces to nothing; it falls back to the full fingerprint so that it keeps
    an identity. Such a name still matches poorly against its own variants.
    """
    toks = sorted({t for t in tokens(name) if t not in STOPWORDS})
    if not toks:
        return fingerprint(name)
    return " ".join(toks)
