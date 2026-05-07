# migration_checker/fixtures/matplotlib/sample_plotly.py

import plotly.express as px
import numpy as np
import pandas as pd

def create_plot_plotly():
    """A simple function using the plotly library."""
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    data = pd.DataFrame({'x': x, 'y': y})

    # Plotly Express creates a figure object
    fig = px.line(data, x='x', y='y', title='Plotly Sine Wave')

    # A common mistake might be to try to use matplotlib-style access
    # fig.axes.set_title("New Title") # This would fail
    # Our tool should flag `.axes` as suspicious.

    # The Plotly way to update layout
    fig.update_layout(xaxis_title="New X-axis Title")
    # In a real scenario, you would call fig.show()
    # fig.show()

if __name__ == "__main__":
    create_plot_plotly()

