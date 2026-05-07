# migration_checker/fixtures/matplotlib/sample_seaborn.py

import seaborn as sns
import numpy as np
import pandas as pd

def create_plot_seaborn():
    """A simple function using the seaborn library."""
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    data = pd.DataFrame({'x': x, 'y': y})

    # Seaborn works well with pandas DataFrames
    sns.lineplot(x="x", y="y", data=data)
    
    # A common pattern is to get the matplotlib axes object to customize
    ax = sns.lineplot(x="x", y="y", data=data)
    ax.set_title("Seaborn Sine Wave")
    # Our tool might flag set_xlabel if it's in the suspicious list,
    # which could be a useful check for style consistency.
    ax.set_xlabel("Custom X Label")

if __name__ == "__main__":
    create_plot_seaborn()

