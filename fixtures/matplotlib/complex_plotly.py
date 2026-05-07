import pandas as pd
import plotly.express as px


def build_sales_dashboard():
    data = pd.DataFrame(
        {
            "month": ["Jan", "Feb", "Mar", "Apr"],
            "sales": [12, 18, 16, 23],
            "returns": [1, 2, 1, 3],
        }
    )

    sales_fig = px.line(data, x="month", y="sales", title="Sales trend")
    returns_fig = px.bar(data, x="month", y="returns", title="Returns")

    sales_fig.update_layout(xaxis_title="Month", yaxis_title="Sales")
    returns_fig.update_layout(xaxis_title="Month", yaxis_title="Returns")
    return sales_fig, returns_fig


if __name__ == "__main__":
    build_sales_dashboard()
