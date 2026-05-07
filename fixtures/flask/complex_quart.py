from quart import Quart, abort, jsonify, render_template, request


app = Quart(__name__)


@app.get("/items/<item_id>")
async def get_item(item_id):
    return jsonify({"item_id": item_id, "status": "ok"})


@app.post("/items")
async def create_item():
    payload = await request.get_json()
    if not payload or "name" not in payload:
        abort(400)

    return await render_template("created.html", item=payload), 201
