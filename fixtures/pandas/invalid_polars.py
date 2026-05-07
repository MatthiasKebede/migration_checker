import polars as pl


def load_polars_frame_invalid():
    frame = pl.read_csv("data.csv", sep=",")
    print(frame)


if __name__ == "__main__":
    load_polars_frame_invalid()
