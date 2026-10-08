"""Train the climate analytics (src/ml/climate_ml.py) on the verified cache and keep the results in MongoDB"""
import json
import sys
from datetime import datetime, timezone

from .settings import ROOT

sys.path.insert(0, str(ROOT / "src" / "ml"))
import climate_ml


def train(db, cache_dir, artifact_root=ROOT / "artifacts" / "ml"):
    res = json.loads(json.dumps(climate_ml.run(cache_dir, artifact_root)))
    res["created_at"] = datetime.now(timezone.utc)
    res["data_checksum"] = json.loads((cache_dir / "meta.json").read_text(encoding="utf-8")).get("daily", {}).get("sha256")
    db.ml_runs.insert_one(res)
    return {"model_version": res["model_version"], "artifact_dir": res["artifact_dir"]}


def latest(db):
    return db.ml_runs.find_one(sort=[("created_at", -1)])
