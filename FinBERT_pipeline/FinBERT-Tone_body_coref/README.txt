##
In this version of the FinBERT pipeline, will are performing the Coref step to match pronouns and nouns with the ticker entity.

uses fastcoref for proper coreference resolution (resolves pronouns and phrases like "the company" → "AEHL")

Finaly it will perform the yiyanghkust/FinBERT-tone (Directly usable on HuggingFace)

"probably the most relevant upgrade from ProsusAI/FinBERT for you. Trained on 4.9B tokens including corporate 10-K/10-Q reports, earnings call transcripts, and analyst reports, then fine-tuned on tone/sentiment from analyst reports. Better calibrated for formal financial language than ProsusAI's version which was trained on news headlines"
