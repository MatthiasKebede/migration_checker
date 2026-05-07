from bokeh.plotting import figure


def create_plot_bokeh_invalid():
    plot = figure(title="Bokeh Sine Wave")
    print(plot.axes)


if __name__ == "__main__":
    create_plot_bokeh_invalid()
