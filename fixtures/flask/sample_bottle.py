import bottle


app = bottle.Bottle()


@app.get("/health")
def health():
    return {"ok": True}
