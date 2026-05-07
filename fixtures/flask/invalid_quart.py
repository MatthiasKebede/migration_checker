import flask
from quart import Quart


def create_app_invalid():
    app = flask.Flask(__name__)
    app = Quart(__name__)
    return app
