import argparse
import click


@click.command()
def main():
    parser = argparse.ArgumentParser()
    return parser.parse_args()


if __name__ == "__main__":
    main()
