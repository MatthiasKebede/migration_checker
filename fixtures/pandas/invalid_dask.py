import dask.dataframe as dd


def load_dask_frame_invalid():
    frame = dd.read_csv("data.csv")
    print(frame.values)


if __name__ == "__main__":
    load_dask_frame_invalid()
