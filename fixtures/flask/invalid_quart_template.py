from quart import render_template


async def render_home_invalid():
    return render_template("index.html")
