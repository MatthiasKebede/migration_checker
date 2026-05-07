import polars as pl


def build_metrics_report(path):
    frame = pl.read_csv(path, separator=",")
    recent = frame.select(["service", "status", "duration_ms"])
    grouped = recent.group_by("status").len().sort("status")
    audit = pl.DataFrame({"rows": [grouped.height], "source": ["daily"]})

    print(recent.columns)
    print(grouped.shape)
    print(audit.head())
    return grouped


if __name__ == "__main__":
    build_metrics_report("events.csv")
