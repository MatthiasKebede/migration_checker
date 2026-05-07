# migration_checker/fixtures/invalid_api_calls.py

import polars as pl

# Invalid call using a renamed keyword argument ('sep' instead of 'separator')
df1 = pl.read_csv("data.csv", sep=",")

# Invalid call using a forbidden keyword argument ('engine')
df2 = pl.read_csv("data.csv", engine="python")

# Correct call
df3 = pl.read_csv("data.csv", separator=",")

print(df1, df2, df3)

