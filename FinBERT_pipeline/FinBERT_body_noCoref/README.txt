##
In this version of the FinBERT pipeline, there will be no Coreference execution to save time in the overall process.

uses spaCy-only PronounResolver (heuristic pronouns only, no fastcoref). Faster startup, lower-quality coreference — won't catch "the company" / "the firm" → entity name.
