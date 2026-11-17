"""Loads the trained model and scores new orders."""
import joblib

model = joblib.load("models/demand.pkl")
