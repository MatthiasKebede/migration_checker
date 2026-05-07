import numpy as np
import pandas as pd
import seaborn as sns


def create_plot_seaborn_invalid():
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    data = pd.DataFrame({"x": x, "y": y})
    ax = sns.lineplot(x="x", y="y", data=data)
    ax.set_xlabel("Custom Label")


if __name__ == "__main__":
    create_plot_seaborn_invalid()
