import argparse
import configargparse as cap


def parse_args_invalid():
    parser = argparse.ArgumentParser()
    parser = cap.ArgParser(default_config_files=["settings.ini"])
    print(parser)


if __name__ == "__main__":
    parse_args_invalid()
