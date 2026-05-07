from docopt import docopt


DOC = """
Usage:
  invalid_docopt.py [--count=<count>]
"""


def parse_args():
    args = docopt(DOC, namespace={})
    print(args)


if __name__ == "__main__":
    parse_args()
