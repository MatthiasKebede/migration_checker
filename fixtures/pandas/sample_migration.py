# migration_checker/fixtures/sample_migration.py

import pandas as pd
import polars as pl

# A correct migration
df_pl = pl.read_csv("data.csv")

# A leftover use of the source library
df_pd = pd.DataFrame({"a": [1, 2], "b": [3, 4]})

print(df_pl)
print(df_pd.columns)

