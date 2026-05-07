import numpy as np
import pandas as pd
import plotly.express as px


def create_plot_plotly_invalid():
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    data = pd.DataFrame({"x": x, "y": y})
    fig = px.line(data, x="x", y="y")
    print(fig.axes)


if __name__ == "__main__":
    create_plot_plotly_invalid()
