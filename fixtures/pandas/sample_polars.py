import polars as pl


def load_polars_frame():
    frame = pl.read_csv("data.csv", separator=",")
    print(frame.shape)


if __name__ == "__main__":
    load_polars_frame()
