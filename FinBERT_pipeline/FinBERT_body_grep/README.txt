##
In this version of the FinBERT pipeline, there will be no Coreference execution to save time in the overall process.

uses spaCy-only PronounResolver (heuristic pronouns only, no fastcoref). Faster startup, lower-quality coreference — won't catch "the company" / "the firm" → entity name.

Also we will use a list of pronouns that will be used to perform a final grep command and replace the common pronoun strings with the entity/company name 
