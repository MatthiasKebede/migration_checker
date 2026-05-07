import dask.dataframe as dd


def load_dask_frame():
    frame = dd.read_csv("data.csv")
    print(frame.head())


if __name__ == "__main__":
    load_dask_frame()
