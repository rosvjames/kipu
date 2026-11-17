"""Tiny data-access helpers."""
import pandas as pd


def read_table(table, con):
    return pd.read_sql(f"select * from {table}", con)
