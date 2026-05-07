from bokeh.plotting import figure


def create_plot_bokeh():
    plot = figure(title="Bokeh Sine Wave")
    plot.line([0, 1, 2], [0, 1, 0])


if __name__ == "__main__":
    create_plot_bokeh()
