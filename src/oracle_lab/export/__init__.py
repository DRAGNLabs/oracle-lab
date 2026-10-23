"""Export an output root as a dataset: flat Parquet tables beside the raw files.

`tables` reads runs, classification attempts, and their reviews through the
persisted contracts; `writer` writes the dataset directory; `hub` uploads it.
"""
