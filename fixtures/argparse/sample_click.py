import click


@click.command()
@click.option("--count", type=int, default=1)
def main(count):
    print(count)


if __name__ == "__main__":
    main()
