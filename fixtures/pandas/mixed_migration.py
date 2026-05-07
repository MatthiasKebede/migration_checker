# migration_checker/fixtures/mixed_migration.py

import pandas as pd
import polars as pl

def process_data():
    # This is a suspicious pattern: a variable is assigned from both libraries.
    my_var = pd.DataFrame({"a": [1, 2]})
    print(my_var)
    
    # Later, the same variable is used for the target library.
    my_var = pl.DataFrame({"a": [3, 4]})
    print(my_var)

def another_function():
    # This is fine, as it's a different variable.
    df_pd = pd.DataFrame({"c": [5, 6]})
    df_pl = pl.DataFrame({"d": [7, 8]})
    print(df_pd, df_pl)

process_data()
another_function()

