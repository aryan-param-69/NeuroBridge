"""MongoDB persistence for ML experiments and the model registry.

Database `neurobridge_ml` (never touches other databases on the server). Collections:
  experiments     one document per experiment: config, dataset manifest, subject lists, selection, hold-out
  cv_results      one document per (experiment, model): per-fold + pooled metrics, out-of-fold probabilities
  model_registry  one document per trained model version, with status staging|approved|retired
  permutation     label-permutation null distributions
Every write is also mirrored to JSON files in ml/reports so results survive without a database.
"""

import datetime as dt
import math

import numpy as np
from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError

DB_NAME = "neurobridge_ml"


def clean(obj):
    """Make numpy / NaN values BSON- and JSON-safe."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return clean(obj.tolist())
    if isinstance(obj, np.generic):
        return clean(obj.item())
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    return obj


class Store:
    def __init__(self, uri: str = "mongodb://localhost:27017", enabled: bool = True):
        self.db = None
        self.error: str | None = None
        if not enabled:
            self.error = "disabled"
            return
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=3000)
            client.admin.command("ping")
            self.db = client[DB_NAME]
            self.db.cv_results.create_index([("experiment_id", ASCENDING), ("model", ASCENDING)], unique=True)
            self.db.model_registry.create_index([("name", ASCENDING), ("version", ASCENDING)], unique=True)
        except PyMongoError as e:  # keep going: JSON mirrors still get written
            self.error = f"{type(e).__name__}: {str(e)[:120]}"

    @property
    def ok(self) -> bool:
        return self.db is not None

    def upsert(self, collection: str, doc_id: str, doc: dict) -> None:
        if not self.ok:
            return
        doc = clean(doc)
        doc["_id"] = doc_id
        doc["updated_at"] = dt.datetime.now(dt.UTC)
        self.db[collection].replace_one({"_id": doc_id}, doc, upsert=True)

    def set_fields(self, collection: str, doc_id: str, fields: dict) -> None:
        if not self.ok:
            return
        fields = clean(fields)
        fields["updated_at"] = dt.datetime.now(dt.UTC)
        self.db[collection].update_one({"_id": doc_id}, {"$set": fields}, upsert=True)
