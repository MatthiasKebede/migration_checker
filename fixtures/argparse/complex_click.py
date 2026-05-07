import click


@click.group()
def cli():
    pass


@cli.command()
@click.argument("dataset")
@click.option("--format", "output_format", type=click.Choice(["json", "csv"]), default="json")
@click.option("--limit", type=int, default=25)
def inspect(dataset, output_format, limit):
    print(dataset, output_format, limit)


@cli.command()
@click.option("--force", is_flag=True)
def sync(force):
    print(force)


if __name__ == "__main__":
    cli()
