"""Score two core keys against each other.

The maximum of a subset metric and a reordering metric, guarded so that a bare
generic key cannot match everything containing it.
"""

from collections import Counter
from collections.abc import Iterable, Mapping

from rapidfuzz import fuzz

__all__ = [
    "MAX_SUBSET_TOKEN_DF",
    "score",
    "token_document_frequency",
]


# A one-token core key is trusted as a subset match only when that token is
# rare across the corpus. See the subset guard in score().
MAX_SUBSET_TOKEN_DF = 2


def token_document_frequency(keys: Iterable[str]) -> Counter[str]:
    """How many distinct core keys each token appears in.

    Separates a generic token such as "kona" (15 keys) from an identifying one
    such as "stumptown" (1). Measured from the corpus, so it adapts to whatever
    stopwording leaves behind.
    """
    return Counter(tok for key in keys for tok in set(key.split()))


def score(a: str, b: str, token_df: Mapping[str, int] | None = None, **kwargs) -> float:
    """Similarity of two core keys (not raw names) in [0, 100].

    Two kinds of variation survive normalization, and no single metric covers
    both, so the result is the maximum of two:

      token_set_ratio   Subset relationships. "Onyx Coffee" -> "onyx" and
                        "Onyx Coffee Lab" -> "lab onyx"; one key is a strict
                        subset of the other, which token_set scores 100.

      token_sort_ratio  Typos and reordering. Sorts tokens, then edit distance.

    Taking the maximum is permissive: a pair becomes a candidate if either
    metric rates it a match. That is affordable because resolve() applies
    strict thresholds and because of the subset guard below.

    Subset guard
        token_set scores any strict subset 100 regardless of how little the
        shorter key says, and stopwording produces bare generic keys that then
        match everything: "Kona Cafe" -> "kona", "Coffee Bros." -> "brothers".
        Unguarded, each such key merges with every key containing it and
        union-find chains the neighbourhood into a single cluster; on the
        current corpus that placed 219 of 1,591 spellings in chained clusters.

        A subset reading is therefore trusted only when the shorter key is
        distinctive: at least 2 tokens, or a single token that is rare in the
        corpus (document frequency <= MAX_SUBSET_TOKEN_DF). The property that
        matters is genericness rather than length -- "international" is long
        and uninformative, "coffeeam" is short and identifying.

    `token_df` comes from token_document_frequency(). When it is None, as in
    direct calls and tests, one-token keys are not trusted for subset matching.

    **kwargs absorbs the `score_cutoff` that rapidfuzz.process.cdist injects
    into scorer callables; without it, cdist raises TypeError.
    """
    shorter = a if len(a) <= len(b) else b
    shorter_tokens = shorter.split()

    if len(shorter_tokens) >= 2:
        subset_is_trustworthy = True
    elif not shorter_tokens or token_df is None:
        subset_is_trustworthy = False
    else:
        df = token_df.get(shorter_tokens[0], MAX_SUBSET_TOKEN_DF + 1)
        subset_is_trustworthy = df <= MAX_SUBSET_TOKEN_DF

    result = float(fuzz.token_sort_ratio(a, b))
    if subset_is_trustworthy:
        result = max(result, float(fuzz.token_set_ratio(a, b)))
    return result
