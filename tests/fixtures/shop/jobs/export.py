"""Exports daily revenue to parquet."""
import pandas as pd

from db import read_table

OUT = "exports/daily_revenue.parquet"


def main(con, where):
    df = read_table("STAGING_MARTS.FCT_DAILY_SALES", con)
    df.to_parquet(OUT)
    pd.read_csv(where)  # dynamic path: must stay unresolved, never guessed


if __name__ == "__main__":
    main(None, None)
