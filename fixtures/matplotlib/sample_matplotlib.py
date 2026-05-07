# migration_checker/fixtures/matplotlib/sample_matplotlib.py

import matplotlib.pyplot as plt
import numpy as np

def create_plot_matplotlib():
    """A simple function using the matplotlib library."""
    x = np.linspace(0, 10, 100)
    y = np.sin(x)

    plt.plot(x, y)
    plt.title("Matplotlib Sine Wave")
    plt.xlabel("x")
    plt.ylabel("sin(x)")
    # In a real scenario, you would call plt.show() or plt.savefig()
    # plt.show()

if __name__ == "__main__":
    create_plot_matplotlib()

