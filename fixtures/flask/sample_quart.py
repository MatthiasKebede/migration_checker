from quart import Quart, jsonify


app = Quart(__name__)


@app.get("/health")
async def health():
    return jsonify({"ok": True})
