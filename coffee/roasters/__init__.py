"""Entity resolution for messy coffee roaster names.

Scraped data spells one roaster several ways -- "Onyx Coffee Lab", "Onyx Coffee
Lab LLC", "onyx coffee lab". This package groups those spellings, picks a
canonical one, and persists the mapping.

Resolution runs as a cascade, cheapest and most certain stage first, and the
modules follow it:

    normalize -> similarity -> location -> cluster
                                    decisions feeds every stage

Precision is favoured over recall throughout, since a false merge is silent
while a false split is visible. That accounts for the conservative behaviour: a
location conflict vetoes a merge but a location match never causes one, the
uncertain band is queued for review rather than decided, and the two known
clustering failure modes are reported rather than prevented.

:mod:`decisions` is the only module here that touches disk. Adjudicated pairs
live in ``roaster_decisions.csv``, the only state that cannot be regenerated;
the crosswalk and the review queue are derived on every run.

``docs/roaster-resolution.md`` covers the design in full. The README section
"Resolving roaster names" covers the workflow.
"""

from coffee.roasters.cluster import DSU, resolve
from coffee.roasters.decisions import (
    DECISION_COLUMNS,
    REVIEW_COLUMNS,
    VERDICT_SYNONYMS,
    Decision,
    Verdict,
    load_decisions,
    parse_verdict,
    promote_reviewed,
    save_decisions,
    unpromoted_verdicts,
)
from coffee.roasters.location import (
    LocationEvidence,
    compare_locations,
    normalize_location,
)
from coffee.roasters.normalize import (
    ABBREV,
    STOPWORDS,
    core_key,
    fingerprint,
    strip_accents,
    tokens,
)
from coffee.roasters.similarity import (
    MAX_SUBSET_TOKEN_DF,
    score,
    token_document_frequency,
)

__all__ = [
    "ABBREV",
    "DECISION_COLUMNS",
    "DSU",
    "MAX_SUBSET_TOKEN_DF",
    "REVIEW_COLUMNS",
    "STOPWORDS",
    "VERDICT_SYNONYMS",
    "Decision",
    "LocationEvidence",
    "Verdict",
    "compare_locations",
    "core_key",
    "fingerprint",
    "load_decisions",
    "normalize_location",
    "parse_verdict",
    "promote_reviewed",
    "resolve",
    "save_decisions",
    "score",
    "strip_accents",
    "token_document_frequency",
    "tokens",
    "unpromoted_verdicts",
]
