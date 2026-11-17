"""Trains the demand model and saves it."""
import joblib
import streamlit as st

MODEL_PATH = "models/demand.pkl"


def train(df, model):
    model.fit(df)
    st.table(df)  # a UI table, not a data source
    joblib.dump(model, MODEL_PATH)
