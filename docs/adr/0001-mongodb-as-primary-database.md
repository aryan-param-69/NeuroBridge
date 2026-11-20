# ADR 0001 — MongoDB as the single database

**Status:** accepted (supersedes the PostgreSQL recommendation in `docs/01-architecture-and-technology.md` §4.3)
**Date:** 2026-11-19
**Decider:** project owner

## Context
The original design recommended PostgreSQL because the clinical data is strongly relational (foreign keys,
transactions). The project owner chose MongoDB instead. Practical factors: MongoDB is already installed and
running on the development machine, PostgreSQL is not, and one database is simpler for a single-developer
project. Phases 0 and 1 were first built on SQLAlchemy (SQLite for development) and were migrated.

## Decision
All application data lives in MongoDB (database `neurobridge`; tests use `neurobridge_test`).
Large numerical data (EDF files, epoch-level feature matrices) still goes to files/object storage, with
MongoDB holding metadata and pointers.

## How the relational design maps to documents
| Was (SQL) | Is (MongoDB) |
|---|---|
| `users` + `roles` + `user_roles` | `users` with an embedded `roles: [..]` array |
| `patients` + `patient_name_tokens` + `patient_access` | `patients` with embedded `care_team[]` and `search{name_tokens[], identity_hash, mrn_hash}` (multikey index) |
| `patient_identifiers` | `patient_identifiers` (separate collection, Fernet-encrypted fields) |
| `id_counters` | `counters` with atomic `$inc` |
| `audit_log` (hash chain) | `audit_log`, unique index on `seq` serialises concurrent appends |
| field history, medical history, events | one collection each, indexed by `patient_id` |

## What MongoDB gives up, and how it is handled
| Loss | Mitigation |
|---|---|
| Foreign keys / referential integrity | Access checks in one place (`load_patient`); care team embedded in the patient so it cannot dangle; tests for cross-patient ID misuse |
| Multi-document transactions (not available on a standalone `mongod`) | Writes are ordered so failures leave harmless orphans only (identifiers are written before the patient and removed if the patient insert fails). A replica set would restore transactions if stronger guarantees are needed |
| Uniqueness / constraints | Unique indexes (`email`, `display_id`, `audit_log.seq`, `refresh_tokens.token_hash`) |
| NoSQL operator injection (`{"$ne": ...}`) | All inputs are typed by Pydantic (strings, UUIDs, enums); search text is hashed or regex-escaped; covered by tests |
| BSON stores dates and times at millisecond precision | Audit timestamps are truncated to ms before hashing |

## Consequences
- Phase 0/1 storage layer rewritten; the security behaviour and the API are unchanged (the frontend needed no change apart from a type).
- ML experiments and the model registry (offline `ml/` workspace) use the same server, in a separate database `neurobridge_ml`.
- Production must run MongoDB with authentication enabled (the app refuses to start in production otherwise), TLS, backups and, ideally, a replica set.
- The tamper-resistance of the audit log (as opposed to tamper-evidence) requires a MongoDB role that has `insert` but not `update`/`remove` on `audit_log`; this belongs in deployment configuration.
