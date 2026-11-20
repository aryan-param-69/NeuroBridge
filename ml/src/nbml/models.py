"""The candidate model zoo. Grids are small and fixed in advance (not tuned after seeing results)."""

from dataclasses import dataclass

from lightgbm import LGBMClassifier
from mne.decoding import CSP
from pyriemann.tangentspace import TangentSpace
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from xgboost import XGBClassifier

from .config import SEED


@dataclass(frozen=True)
class ModelSpec:
    name: str
    input: str  # "bandpower" (n,192) | "cov" (n,64,64) | "raw" (n,64,560)
    build: callable
    grid: dict
    interpretability: int  # 1 = most interpretable (used only by the pre-specified tie-break)
    cost: int  # 1 = cheapest on CPU (tie-break after interpretability)
    note: str = ""


def _lr(C=1.0):
    return LogisticRegression(C=C, max_iter=2000, random_state=SEED)


MODELS: dict[str, ModelSpec] = {s.name: s for s in [
    ModelSpec("csp_lda", "raw",
              lambda: Pipeline([("csp", CSP(n_components=6, reg="ledoit_wolf", log=True, norm_trace=False)),
                                ("clf", LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto"))]),
              {"csp__n_components": [4, 6, 8]}, 1, 1, "classic motor-imagery baseline"),
    ModelSpec("logreg", "bandpower",
              lambda: Pipeline([("sc", StandardScaler()), ("clf", _lr())]),
              {"clf__C": [0.001, 0.01, 0.1, 1.0]}, 1, 1),
    ModelSpec("svm_rbf", "bandpower",
              lambda: Pipeline([("sc", StandardScaler()),
                                ("clf", SVC(kernel="rbf", probability=True, random_state=SEED))]),
              {"clf__C": [0.1, 1.0, 10.0]}, 3, 2),
    ModelSpec("random_forest", "bandpower",
              lambda: RandomForestClassifier(n_estimators=300, n_jobs=1, random_state=SEED),
              {"max_depth": [None, 8], "min_samples_leaf": [1, 5]}, 3, 3),
    ModelSpec("gradient_boosting", "bandpower",
              lambda: GradientBoostingClassifier(n_estimators=200, subsample=0.8, random_state=SEED),
              {"learning_rate": [0.05, 0.1], "max_depth": [2, 3]}, 3, 4),
    ModelSpec("xgboost", "bandpower",
              lambda: XGBClassifier(n_estimators=300, subsample=0.8, colsample_bytree=0.8, n_jobs=1,
                                    eval_metric="logloss", random_state=SEED, verbosity=0),
              {"max_depth": [2, 4], "learning_rate": [0.05, 0.1]}, 3, 3),
    ModelSpec("lightgbm", "bandpower",
              lambda: LGBMClassifier(n_estimators=300, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                     n_jobs=1, random_state=SEED, verbose=-1),
              {"num_leaves": [7, 15], "learning_rate": [0.05, 0.1]}, 3, 3),
    ModelSpec("ts_logreg", "cov",
              lambda: Pipeline([("ts", TangentSpace(metric="riemann")), ("sc", StandardScaler()),
                                ("clf", _lr())]),
              {"clf__C": [0.0001, 0.001, 0.01, 0.1]}, 1, 2, "Riemannian tangent space + logistic regression"),
    ModelSpec("ts_svm_linear", "cov",
              lambda: Pipeline([("ts", TangentSpace(metric="riemann")), ("sc", StandardScaler()),
                                ("clf", SVC(kernel="linear", probability=True, random_state=SEED))]),
              {"clf__C": [0.00001, 0.0001, 0.001]}, 2, 3, "Riemannian tangent space + linear SVM"),
]}

# The nine models of the pre-specified comparison. Anything else is EXPLORATORY and reported separately.
PRIMARY = list(MODELS)

MODELS["ts_ra_logreg"] = ModelSpec(
    "ts_ra_logreg", "cov_ra",
    lambda: Pipeline([("ts", TangentSpace(metric="riemann")), ("sc", StandardScaler()), ("clf", _lr())]),
    {"clf__C": [0.0001, 0.001, 0.01, 0.1]}, 1, 2,
    "EXPLORATORY: tangent space + per-subject recentring + logistic regression")
