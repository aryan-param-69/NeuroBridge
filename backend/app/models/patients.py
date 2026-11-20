"""Patient domain constants (storage is MongoDB, see app/db/mongo.py)."""

SEX = ("male", "female", "other", "unknown")
ETIOLOGY = ("traumatic", "anoxic", "vascular", "infectious", "metabolic", "other", "unknown")
STATUS = ("active", "discharged", "archived")
# Allowed status transitions (archived is terminal for clinicians).
STATUS_TRANSITIONS = {
    "active": {"discharged"},
    "discharged": {"active", "archived"},
    "archived": set(),
}
HISTORY_CATEGORIES = ("diagnosis", "comorbidity", "medication", "surgery", "seizure")
EVENT_TYPES = ("medication_change", "surgery", "infection", "transfer", "seizure", "other")
