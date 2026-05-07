# migration_checker/fixtures/suspicious_downstream_use.py

import polars as pl

# This should be flagged by the dataflow detector.
# The 'values' attribute is common in pandas but not in polars.
df = pl.DataFrame({"a": [1, 2], "b": [3, 4]})
numpy_array = df.values

# This should also be flagged.
# Iterating over a DataFrame with iterrows() is a pandas pattern.
for index, row in df.iterrows():
    print(row)

