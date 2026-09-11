"""Only the repository's trusted RF artifact is loaded; failures are rules-only."""
import hashlib
import logging
import math
import os

import joblib
from sklearn.ensemble import RandomForestClassifier

from .. import config

logger = logging.getLogger(__name__)
MODEL_PATH = config.ML_MODELS_DIR / 'placement_rf.joblib'
FEATURES = ('cgpa', 'backlogs', 'attendance_pct')
RF_CONFIG = dict(n_estimators=100, max_depth=8, min_samples_leaf=5, random_state=42)
RULES_ONLY = 'Meets all drive criteria (model unavailable, rules-only evaluation)'


def threshold():
    try:
        value = float(os.getenv('MAWOS_PLACEMENT_MODEL_THRESHOLD', '0.5'))
    except ValueError:
        raise config.ConfigurationError('MAWOS_PLACEMENT_MODEL_THRESHOLD must be between 0 and 1') from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise config.ConfigurationError('MAWOS_PLACEMENT_MODEL_THRESHOLD must be between 0 and 1')
    return value


def load_model():
    try:
        model = joblib.load(MODEL_PATH)
        if not isinstance(model, RandomForestClassifier):
            raise ValueError('Wrong model type')
        if any(model.get_params()[key] != value for key, value in RF_CONFIG.items()):
            raise ValueError('Incompatible configuration')
        if model.n_features_in_ != 3 or list(model.classes_) != [0, 1]:
            raise ValueError('Incompatible features/classes')
        if hasattr(model, 'feature_names_in_') and tuple(model.feature_names_in_) != FEATURES:
            raise ValueError('Incompatible feature order')
        return model, 'rf-' + hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()[:12]
    except Exception:
        # No exception text: it may contain filesystem paths or unsafe pickle content.
        logger.warning('Placement model unavailable; using rules-only evaluation')
        return None, None


def score(model, version, student, attendance):
    cutoff = threshold()
    if model is not None:
        try:
            probability = float(model.predict_proba([[student.cgpa, student.backlogs, attendance]])[0][1])
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError('Invalid probability')
            eligible = probability >= cutoff
            comparison = 'meets' if eligible else 'below'
            return dict(eligible=eligible, ml_probability=probability, model_version=version,
                        reasons=f'Model confidence {probability:.2f} {comparison} threshold {cutoff:g}')
        except Exception:
            logger.warning('Placement scoring unavailable; using rules-only evaluation')
    return dict(eligible=True, ml_probability=None, model_version=None, reasons=RULES_ONLY)
