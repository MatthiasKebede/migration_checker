# migration_checker/fixtures/pandas/sample_tablib.py

import tablib

def process_data_tablib():
    """A simple function using the tablib library."""
    data = tablib.Dataset()
    data.headers = ['first_name', 'last_name']
    data.append(('John', 'Doe'))
    data.append(('Jane', 'Smith'))

    # This would be a common mistake when migrating from pandas
    # tablib uses .headers, not .columns
    # Our tool should flag access to a `.columns` attribute as suspicious.
    # cols = data.columns

    # Correct usage in tablib
    print(data.headers)
    print(data.export('df')) # tablib can export to a pandas DataFrame

if __name__ == "__main__":
    process_data_tablib()

