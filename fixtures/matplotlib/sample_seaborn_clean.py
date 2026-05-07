import numpy as np
import pandas as pd
import seaborn as sns


def create_plot_seaborn_clean():
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    data = pd.DataFrame({"x": x, "y": y})
    sns.lineplot(x="x", y="y", data=data)


if __name__ == "__main__":
    create_plot_seaborn_clean()
