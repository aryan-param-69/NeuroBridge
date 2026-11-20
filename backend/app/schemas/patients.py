import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Sex = Literal["male", "female", "other", "unknown"]
Etiology = Literal["traumatic", "anoxic", "vascular", "infectious", "metabolic", "other", "unknown"]
Status = Literal["active", "discharged", "archived"]
Category = Literal["diagnosis", "comorbidity", "medication", "surgery", "seizure"]
EventType = Literal["medication_change", "surgery", "infection", "transfer", "seizure", "other"]


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return v or None


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    date_of_birth: date
    sex: Sex
    mrn: str | None = Field(default=None, max_length=64)
    guardian_contact: str | None = Field(default=None, max_length=300)
    etiology: Etiology
    etiology_other: str | None = Field(default=None, max_length=200)
    date_of_injury: date | None = None
    admission_date: date | None = None
    clinical_status_label: str | None = Field(default=None, max_length=200)
    craniectomy: bool = False
    consent_clinical: bool = False
    consent_research: bool = False
    tags: list[str] = Field(default_factory=list, max_length=20)
    confirm_duplicate: bool = False

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Name is too short")
        return v

    @field_validator("mrn", "guardian_contact", "etiology_other", "clinical_status_label")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return _clean(v)

    @model_validator(mode="after")
    def _dates(self):
        today = date.today()
        if self.date_of_birth > today:
            raise ValueError("Date of birth cannot be in the future")
        if self.date_of_birth.year < 1900:
            raise ValueError("Date of birth is implausible")
        if self.date_of_injury:
            if self.date_of_injury > today:
                raise ValueError("Date of injury cannot be in the future")
            if self.date_of_injury < self.date_of_birth:
                raise ValueError("Date of injury cannot be before date of birth")
        if self.admission_date and self.admission_date > today:
            raise ValueError("Admission date cannot be in the future")
        if self.etiology == "other" and not self.etiology_other:
            raise ValueError("Describe the etiology when 'other' is selected")
        return self


class PatientUpdate(BaseModel):
    """Only fields that are sent are changed (use model_fields_set)."""

    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    date_of_birth: date | None = None
    sex: Sex | None = None
    mrn: str | None = Field(default=None, max_length=64)
    guardian_contact: str | None = Field(default=None, max_length=300)
    etiology: Etiology | None = None
    etiology_other: str | None = Field(default=None, max_length=200)
    date_of_injury: date | None = None
    admission_date: date | None = None
    clinical_status_label: str | None = Field(default=None, max_length=200)
    craniectomy: bool | None = None
    consent_clinical: bool | None = None
    consent_research: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    status: Status | None = None

    @field_validator("mrn", "guardian_contact", "etiology_other", "clinical_status_label")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return _clean(v)


class MedicalHistoryIn(BaseModel):
    category: Category
    description: str = Field(min_length=1, max_length=2000)
    code_system: Literal["ICD-10", "SNOMED"] | None = None
    code: str | None = Field(default=None, max_length=40)
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("End date cannot be before start date")
        return self


class MedicalHistoryPatch(BaseModel):
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    code_system: Literal["ICD-10", "SNOMED"] | None = None
    code: str | None = Field(default=None, max_length=40)
    start_date: date | None = None
    end_date: date | None = None
    is_active: bool | None = None


class MedicalHistoryOut(MedicalHistoryIn):
    id: uuid.UUID
    created_at: datetime


class EventIn(BaseModel):
    event_type: EventType
    occurred_at: datetime
    description: str = Field(min_length=1, max_length=2000)


class EventOut(EventIn):
    id: uuid.UUID
    entered_by: uuid.UUID
    created_at: datetime


class HistoryOut(BaseModel):
    id: str
    field: str
    old_value: object | None
    new_value: object | None
    changed_by: uuid.UUID
    changed_at: datetime


class AccessMember(BaseModel):
    user_id: uuid.UUID
    access_level: Literal["owner", "member"] = "member"


class AccessUpdate(BaseModel):
    members: list[AccessMember] = Field(min_length=1)


class AccessOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    roles: list[str]
    access_level: str
