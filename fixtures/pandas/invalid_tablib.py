import tablib


def process_tablib_invalid():
    data = tablib.Dataset()
    print(data.columns)


if __name__ == "__main__":
    process_tablib_invalid()
