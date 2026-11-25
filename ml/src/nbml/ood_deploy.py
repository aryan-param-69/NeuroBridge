"""Install the recalibrated unfamiliar-EEG detector (see ood_recal.py) into the sedation_v2 bundle and refresh its fingerprint and model card.
The classifier, calibration and conformal threshold are NOT touched. Run after ood_recal:  uv run python -m nbml.ood_deploy
"""

# ruff: noqa: E501
import hashlib
import json

import joblib
import numpy as np

from . import config as C
from . import ood_recal as R
from . import sed_ood as ood
from .store import Store

MODEL_DIR = C.ML_DIR / "models" / "sedation_v2"


def main():
    res = json.loads((R.OUT / "results.json").read_text(encoding="utf-8"))
    fin = joblib.load(R.OUT / "ood_final.joblib")
    bundle = joblib.load(MODEL_DIR / "model.joblib")
    assert list(fin["features"]) == list(bundle["features"])
    old = bundle["ood"]
    bundle["ood"] = fin["ood"]
    bundle["ood_meta"] = {"reference_sources": fin["sources"], "percentile": fin["pct"], "recording_rule_epoch_fraction": R.REC_FRAC,
                          "replaced": {"components": int(old["k"]), "note": "fitted on the two propofol laboratories only"}}
    path = MODEL_DIR / "model.joblib"
    joblib.dump(bundle, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    # behaviour of the whole frozen model on two sources it never trained on (EEGMMIDB awake people; Figshare patient-type, no labels)
    pool = R.build_pool()
    names = [str(n) for n in pool["features"]]
    X = pool["X"][:, [names.index(n) for n in bundle["features"]]]
    ds, rec = pool["dataset"], pool["recording"]
    q = bundle["conformal_q"]
    a, b = bundle["platt"]
    out = {}
    for s in ("eegmmidb", "figshare"):
        m = ds == s
        raw = np.clip(bundle["model"].predict_proba(X[m])[:, 1], 1e-4, 1 - 1e-4)
        cal = 1 / (1 + np.exp(-(a * np.log(raw / (1 - raw)) + b)))
        fl = ood.flags(bundle["ood"], X[m])
        verdicts = {"awake-like": 0, "sedated-like": 0, "uncertain": 0}
        unf = 0
        for r in np.unique(rec[m]):
            k = rec[m] == r
            p = float(cal[k].mean())
            ps = [i for i, pk in ((0, 1 - p), (1, p)) if 1 - pk <= q]
            verdicts["uncertain" if len(ps) != 1 else ("awake-like", "sedated-like")[ps[0]]] += 1
            unf += int(fl[k].mean() > R.REC_FRAC)
        out[s] = {"n_recordings": int(len(np.unique(rec[m]))), "verdicts": verdicts, "unfamiliar_recordings": unf}
    res["v2_frozen_on_other_sources"] = out
    res["deployed"] = {"model_sha256": sha}
    (R.OUT / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    card = json.loads((MODEL_DIR / "model_card.json").read_text(encoding="utf-8"))
    card["model_sha256"] = sha
    card["unfamiliar_eeg_detector"] = {
        "method": "PCA, Hotelling T-squared and SPE; epoch flagged above the training percentile; recording called unfamiliar if more than 25 % of its epochs are flagged; non-finite features always flagged",
        "reference_sources": ["Cambridge propofol", "Oslo propofol", "routine clinical EEG (ds004504)", "EEGMMIDB resting EEG"], "percentile": fin["pct"],
        "false_flag_on_unseen_source": {k: round(v["recording_unfamiliar_rate"], 3) for k, v in res["lodo"][-1]["lodo_false_flag"].items()},
        "detects_corruptions": res["lodo"][-1]["corruption_detection"], "external_patient_type_figshare_unfamiliar": res["external_figshare"]["recording_unfamiliar_rate"]}
    lim = [x for x in card["known_limits"] if not x.startswith("other devices/montages")]
    lim.append("unfamiliar-EEG flag catches noise, dead channels and many bad electrodes but NOT shuffled electrode order (region averages hide it); check the montage")
    card["known_limits"] = lim
    (MODEL_DIR / "model_card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    st = Store()
    if st.ok:
        st.upsert("model_registry", "sedation_level/sedation_v2", {"name": "sedation_level", "version": "sedation_v2", "status": "staging", **card})
    print("deployed", sha[:12], json.dumps(out))


if __name__ == "__main__":
    main()
