"""Single, versioned slope-scoring rule for the terrain criterion."""

from __future__ import annotations

import numpy as np


# Classes (%): 0–3, 3–8, 8–15, 15–25, 25–45 and >45.
SLOPE_MULTIPLIER = np.array(
    [0.8875, 1.00, 0.8025, 0.54, 0.2325, 0.0225], dtype=float
)
RELIEF_METHOD_VERSION = "DECLIVIDADE_RELEVO_V1_COPDEM30"
