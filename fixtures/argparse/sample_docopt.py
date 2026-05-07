from docopt import docopt


DOC = """
Usage:
  sample_docopt.py [--count=<count>]
"""


def parse_args():
    args = docopt(DOC)
    print(args)


if __name__ == "__main__":
    parse_args()
