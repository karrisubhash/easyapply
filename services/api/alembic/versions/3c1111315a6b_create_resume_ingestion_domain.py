"""create resume ingestion domain

Revision ID: 3c1111315a6b
Revises: 10f47a8774be
Create Date: 2026-09-18 00:30:29.912124

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3c1111315a6b'
down_revision: Union[str, Sequence[str], None] = '10f47a8774be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The uploaded artifact is immutable. The row records what was uploaded; if a
# different resume arrives it is a different document. Enforced rather than
# asserted, because "we never update this table" is a claim about every future
# code path, and those have not been written yet.

DOCUMENT_GUARD = """
CREATE OR REPLACE FUNCTION easyapply_resume_document_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'resume_document % is immutable', OLD.id
        USING ERRCODE = 'restrict_violation',
              CONSTRAINT = 'ck_resume_documents_immutable';
END;
$$;
"""


# The processing state machine, enforced by the database.
#
# It is enforced here as well as in Python because the whole point of the design
# is that a worker process can be introduced later and write this table
# directly. A rule that only exists in one service is not a rule.
#
# Two details matter. The trigger fires on INSERT as well as UPDATE, because a
# BEFORE UPDATE trigger alone would happily accept a row created directly in
# 'completed'. And every RAISE carries USING CONSTRAINT: app/api/errors.py maps
# (SQLSTATE 23001, constraint_name IS NULL) to "this profile version is
# confirmed", so a bare restrict_violation raised here would be reported to the
# client as a completely unrelated profile error.

TRANSITION_GUARD = """
CREATE OR REPLACE FUNCTION easyapply_resume_run_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    allowed text[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'uploaded' THEN
            RAISE EXCEPTION
                'a processing run must start in uploaded, not %', NEW.status
                USING ERRCODE = 'restrict_violation',
                      CONSTRAINT = 'ck_resume_processing_runs_initial_status';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status = OLD.status THEN
        -- Progress within a state (counts, timings) is fine; leaving a
        -- terminal state is not, and neither is rewriting its verdict.
        IF OLD.status IN ('completed', 'failed')
           AND (NEW.failed_stage IS DISTINCT FROM OLD.failed_stage
                OR NEW.error_code IS DISTINCT FROM OLD.error_code
                OR NEW.attempt IS DISTINCT FROM OLD.attempt
                OR NEW.resume_document_id IS DISTINCT FROM OLD.resume_document_id) THEN
            RAISE EXCEPTION
                'run % is % and its outcome cannot be rewritten', OLD.id, OLD.status
                USING ERRCODE = 'restrict_violation',
                      CONSTRAINT = 'ck_resume_processing_runs_terminal_frozen';
        END IF;
        RETURN NEW;
    END IF;

    allowed := CASE OLD.status
        WHEN 'uploaded'          THEN ARRAY['validating', 'failed']
        WHEN 'validating'        THEN ARRAY['extracting', 'failed']
        WHEN 'extracting'        THEN ARRAY['structuring', 'ocr_required', 'failed']
        WHEN 'ocr_required'      THEN ARRAY['ocr_processing', 'failed']
        WHEN 'ocr_processing'    THEN ARRAY['structuring', 'failed']
        WHEN 'structuring'       THEN ARRAY['validating_result', 'failed']
        WHEN 'validating_result' THEN ARRAY['materialising', 'failed']
        WHEN 'materialising'     THEN ARRAY['completed', 'failed']
        ELSE ARRAY[]::text[]
    END;

    IF NOT (NEW.status = ANY (allowed)) THEN
        RAISE EXCEPTION
            'illegal processing transition % -> %', OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation',
                  CONSTRAINT = 'ck_resume_processing_runs_transition';
    END IF;

    RETURN NEW;
END;
$$;
"""


def _create_transition_guard() -> None:
    op.execute(DOCUMENT_GUARD)
    op.execute(
        """
        CREATE TRIGGER trg_resume_documents_immutable
        BEFORE UPDATE ON resume_documents
        FOR EACH ROW EXECUTE FUNCTION easyapply_resume_document_immutable();
        """
    )

    op.execute(TRANSITION_GUARD)
    op.execute(
        """
        CREATE TRIGGER trg_resume_processing_runs_transition
        BEFORE INSERT OR UPDATE ON resume_processing_runs
        FOR EACH ROW EXECUTE FUNCTION easyapply_resume_run_transition();
        """
    )


def _drop_transition_guard() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_resume_processing_runs_transition"
        " ON resume_processing_runs;"
    )
    op.execute("DROP FUNCTION IF EXISTS easyapply_resume_run_transition();")
    op.execute("DROP TRIGGER IF EXISTS trg_resume_documents_immutable ON resume_documents;")
    op.execute("DROP FUNCTION IF EXISTS easyapply_resume_document_immutable();")


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('resume_documents',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=128), nullable=False),
    sa.Column('byte_size', sa.BigInteger(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('byte_size > 0', name='ck_resume_documents_byte_size_positive'),
    sa.CheckConstraint('char_length(content_sha256) = 64', name='ck_resume_documents_content_sha256_length'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_resume_documents_user_id', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'content_sha256', name='uq_resume_documents_user_id_content_sha256')
    )
    op.create_table('resume_processing_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resume_document_id', sa.Integer(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=24), server_default='uploaded', nullable=False),
    sa.Column('failed_stage', sa.String(length=24), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('extracted_char_count', sa.Integer(), nullable=True),
    sa.Column('ocr_page_count', sa.Integer(), nullable=True),
    sa.Column('profile_version_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name='ck_resume_processing_runs_error_code_matches_status'),
    sa.CheckConstraint("(status = 'failed') = (failed_stage IS NOT NULL)", name='ck_resume_processing_runs_failed_stage_matches_status'),
    sa.CheckConstraint("(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)", name='ck_resume_processing_runs_finished_at_matches_status'),
    sa.CheckConstraint("failed_stage IS NULL OR failed_stage IN ('validate', 'extract', 'ocr', 'structure', 'validate_result', 'materialise')", name='ck_resume_processing_runs_failed_stage'),
    sa.CheckConstraint("profile_version_id IS NULL OR status = 'completed'", name='ck_resume_processing_runs_version_only_when_completed'),
    sa.CheckConstraint("status IN ('uploaded', 'validating', 'extracting', 'ocr_required', 'ocr_processing', 'structuring', 'validating_result', 'materialising', 'completed', 'failed')", name='ck_resume_processing_runs_status'),
    sa.CheckConstraint('attempt > 0', name='ck_resume_processing_runs_attempt_positive'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_resume_processing_runs_profile_version_id', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['resume_document_id'], ['resume_documents.id'], name='fk_resume_processing_runs_document_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resume_document_id', 'attempt', name='uq_resume_processing_runs_document_id_attempt')
    )
    op.create_index(op.f('ix_resume_processing_runs_profile_version_id'), 'resume_processing_runs', ['profile_version_id'], unique=False)
    op.create_table('resume_document_pages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resume_processing_run_id', sa.Integer(), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('extraction_method', sa.String(length=16), nullable=False),
    sa.Column('extraction_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("extraction_method IN ('text', 'ocr')", name='ck_resume_document_pages_extraction_method'),
    sa.CheckConstraint('char_count >= 0', name='ck_resume_document_pages_char_count'),
    sa.CheckConstraint('page_number > 0', name='ck_resume_document_pages_page_number_positive'),
    sa.ForeignKeyConstraint(['resume_processing_run_id'], ['resume_processing_runs.id'], name='fk_resume_document_pages_run_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resume_processing_run_id', 'page_number', name='uq_resume_document_pages_run_id_page_number')
    )
    op.create_table('resume_processing_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resume_processing_run_id', sa.Integer(), nullable=False),
    sa.Column('stage', sa.String(length=24), nullable=False),
    sa.Column('outcome', sa.String(length=16), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("outcome IN ('started', 'succeeded', 'failed', 'skipped')", name='ck_resume_processing_events_outcome'),
    sa.CheckConstraint("stage IN ('validate', 'extract', 'ocr', 'structure', 'validate_result', 'materialise')", name='ck_resume_processing_events_stage'),
    sa.CheckConstraint('duration_ms IS NULL OR duration_ms >= 0', name='ck_resume_processing_events_duration_not_negative'),
    sa.ForeignKeyConstraint(['resume_processing_run_id'], ['resume_processing_runs.id'], name='fk_resume_processing_events_run_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resume_processing_events_resume_processing_run_id'), 'resume_processing_events', ['resume_processing_run_id'], unique=False)
    op.create_table('resume_structured_results',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('resume_processing_run_id', sa.Integer(), nullable=False),
    sa.Column('structurer', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('unmapped', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['resume_processing_run_id'], ['resume_processing_runs.id'], name='fk_resume_structured_results_run_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resume_processing_run_id', name='uq_resume_structured_results_run_id')
    )
    # ### end Alembic commands ###

    _create_transition_guard()


def downgrade() -> None:
    """Downgrade schema."""
    _drop_transition_guard()

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('resume_structured_results')
    op.drop_index(op.f('ix_resume_processing_events_resume_processing_run_id'), table_name='resume_processing_events')
    op.drop_table('resume_processing_events')
    op.drop_table('resume_document_pages')
    op.drop_index(op.f('ix_resume_processing_runs_profile_version_id'), table_name='resume_processing_runs')
    op.drop_table('resume_processing_runs')
    op.drop_table('resume_documents')
    # ### end Alembic commands ###
