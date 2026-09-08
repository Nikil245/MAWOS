from sqlalchemy import Column, Integer, String, Text

from scripts.verify_postgresql import (
    _model_server_default,
    _normalized_server_default,
    _server_default_difference,
)


def test_normalizes_postgresql_string_integer_boolean_and_json_literals():
    assert _normalized_server_default("'DRAFT'::character varying") == "DRAFT"
    assert _normalized_server_default("''::text") == ""
    assert _normalized_server_default("1") == "1"
    assert _normalized_server_default("TRUE::boolean") == "true"
    assert _normalized_server_default("'[]'::text") == "[]"
    assert _normalized_server_default("'{}'::jsonb") == "{}"


def test_scholarship_model_defaults_match_migrated_literals():
    from backend.app.models import Scholarship, ScholarshipApplication, ScholarshipAssessment

    assert _model_server_default(Scholarship.__table__.c.status) == "DRAFT"
    assert _model_server_default(ScholarshipApplication.__table__.c.application_status) == "SUBMITTED"
    assert _model_server_default(ScholarshipAssessment.__table__.c.reason_codes) == "[]"
    assert _model_server_default(Scholarship.__table__.c.criteria) == "{}"


def test_nullable_column_with_intentional_default_is_compared():
    column = Column("note", String, nullable=True, server_default="queued")
    assert not _server_default_difference(column, "'queued'::character varying")
    assert _server_default_difference(column, "'missing'::character varying")


def test_meaningful_server_default_mismatch_is_reported():
    status = Column("status", Text, nullable=False, server_default="DRAFT")
    integer = Column("criteria_version", Integer, nullable=False, server_default="1")
    assert _server_default_difference(status, "'PUBLISHED'::text")
    assert _server_default_difference(integer, "2")
