"""Model zoo for the sedation task: the six models named in the project brief, on the 74 interpretable features.
Grids are small and fixed in advance (see reports/sedation/PRESPEC.md); nothing is tuned after seeing results."""

from lightgbm import LGBMClassifier
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from xgboost import XGBClassifier

from .config import SEED
from .models import ModelSpec

ZOO: dict[str, ModelSpec] = {s.name: s for s in [
    ModelSpec("logreg", "tab", lambda: Pipeline([("sc", StandardScaler()), ("clf", LogisticRegression(max_iter=3000, random_state=SEED))]),
              {"clf__C": [0.001, 0.01, 0.1, 1.0]}, 1, 1),
    ModelSpec("svm_rbf", "tab", lambda: Pipeline([("sc", StandardScaler()), ("clf", SVC(kernel="rbf", probability=True, random_state=SEED))]),
              {"clf__C": [0.1, 1.0, 10.0]}, 3, 2),
    ModelSpec("random_forest", "tab", lambda: RandomForestClassifier(n_estimators=300, n_jobs=1, random_state=SEED),
              {"max_depth": [None, 8], "min_samples_leaf": [1, 5]}, 3, 3),
    ModelSpec("gradient_boosting", "tab", lambda: GradientBoostingClassifier(n_estimators=150, subsample=0.8, random_state=SEED),
              {"learning_rate": [0.05, 0.1], "max_depth": [2, 3]}, 3, 4),
    ModelSpec("xgboost", "tab", lambda: XGBClassifier(n_estimators=200, subsample=0.8, colsample_bytree=0.8, n_jobs=1,
                                                      eval_metric="logloss", random_state=SEED, verbosity=0),
              {"max_depth": [2, 4], "learning_rate": [0.05, 0.1]}, 3, 3),
    ModelSpec("lightgbm", "tab", lambda: LGBMClassifier(n_estimators=200, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                                        n_jobs=1, random_state=SEED, verbose=-1),
              {"num_leaves": [7, 15], "learning_rate": [0.05, 0.1]}, 3, 3),
]}
