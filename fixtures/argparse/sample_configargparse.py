import configargparse as cap


def parse_args():
    parser = cap.ArgParser(default_config_files=["settings.ini"])
    parser.add("--count", type=int, default=1)
    print(parser)


if __name__ == "__main__":
    parse_args()
