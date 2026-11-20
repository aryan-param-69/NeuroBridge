"""Stage 1 of the multi-site experiment: pooled leave-one-person-out over 40 people (Cambridge + Oslo), six models.
Run:  uv run python -m nbml.sedm_cv [primary|full]
"""

import json
import sys
import time

import numpy as np
from joblib import Parallel, delayed

from . import config as C
from . import sedm_data as S
from .sed_cv import fold_job, labels
from .sed_models import ZOO

OUT = C.REPORT_DIR / "sedation_multisite"


def main(featureset: str = "primary", n_jobs: int = 10):
    OUT.mkdir(parents=True, exist_ok=True)
    d = S.pooled(featureset)
    X, level, subject = d["X"], d["level"], d["subject"]
    y = labels(level)
    subs = sorted(set(subject.tolist()))
    jobs = [delayed(fold_job)(m, s, X, y, subject, level, C.SEED) for s in subs for m in sorted(ZOO, key=lambda m: -ZOO[m].cost)]
    t0 = time.time()
    res = Parallel(n_jobs=n_jobs, verbose=5)(jobs)
    n = len(y)
    p_task = {m: np.full(n, np.nan) for m in ZOO}
    p_mild = {m: np.full(n, np.nan) for m in ZOO}
    shap_abs = {m: np.zeros((len(subs), X.shape[1])) for m in ZOO}
    shap_sgn = {m: np.zeros((len(subs), X.shape[1])) for m in ZOO}
    have, params = {m: False for m in ZOO}, {m: [] for m in ZOO}
    for r in res:
        p_task[r["model"]][r["te"]] = r["p_te"]
        if len(r["mild"]):
            p_mild[r["model"]][r["mild"]] = r["p_mild"]
        params[r["model"]].append({"held": r["held"], "best": r["best_params"], "inner_ba": r["inner"], "seconds": round(r["seconds"], 1)})
        if r["shap_abs"] is not None:
            held_i = subs.index(r["held"])
            shap_abs[r["model"]][held_i], shap_sgn[r["model"]][held_i] = r["shap_abs"], r["shap_signed"]
            have[r["model"]] = True
    np.savez_compressed(OUT / f"oof_{featureset}.npz", y=y, level=level, subject=subject, recording=d["recording"], site=d["site"],
                        **{f"p_task__{m}": p_task[m] for m in ZOO}, **{f"p_mild__{m}": p_mild[m] for m in ZOO},
                        **{f"shap_abs__{m}": shap_abs[m] for m in ZOO if have[m]}, **{f"shap_sgn__{m}": shap_sgn[m] for m in ZOO if have[m]})
    (OUT / f"cv_params_{featureset}.json").write_text(json.dumps(params, indent=1), encoding="utf-8")
    print(f"done {featureset} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "primary")
