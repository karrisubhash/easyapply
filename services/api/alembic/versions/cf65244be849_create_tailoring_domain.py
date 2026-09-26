"""create tailoring domain

Four tables that turn a confirmed profile version into a document aimed at one
job, and record honestly how much of that document can be stood behind:

    tailoring_runs             one generation attempt. Append-only: a retry is a
                               new row with the next `attempt`, never a reset of
                               an existing one, so "how many times was this
                               generated" is a COUNT.

    tailored_resumes           the generated document as validated JSONB, plus
                               the grounding VERDICT. Written even when grounding
                               failed — the user needs to see what was produced
                               and why it was refused.

    tailoring_issues           one grounding finding each. `error` withholds the
                               artifact; `warning` does not.

    tailored_resume_artifacts  the rendered file, by storage key. Never bytes:
                               document binaries do not belong in PostgreSQL,
                               and `app/services/storage.py` is the only module
                               that knows where an object lives.

Two constraints in here carry the feature's honesty guarantee, and both are in
the database rather than only in Python because the pipeline will not be the
only thing that ever writes these tables:

  * `ck_tailored_resumes_passed_has_no_errors` — a document with an unsupported
    claim in it cannot be recorded as grounded. The verdict and the evidence for
    it cannot disagree.
  * `uq_tailored_resume_artifacts_tailored_resume_id`, combined with the
    pipeline only reaching the render stage on a pass, is what makes "an
    artifact exists" and "this document is trustworthy" the same fact.

The transition trigger below is the mirror of
`easyapply_resume_run_transition`, and exists for the same reason that one does:
the state graph is enforced twice, in `app/models/tailoring.py` and here, so a
future worker writing the table directly cannot bypass it.

Revision ID: cf65244be849
Revises: 7b4c1e9d2f80
Create Date: 2026-09-21 18:40:30.510846

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cf65244be849'
down_revision: Union[str, Sequence[str], None] = '7b4c1e9d2f80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# A run's state graph, in the database. The application enforces it too
# (app/services/tailoring/pipeline.py::advance), and the duplication is
# deliberate: the Python check gives a clear error at the point of the mistake,
# and this one is what remains true when something else writes the table.
#
# The CONSTRAINT names below matter. app/api/errors.py keys its mapping on
# (SQLSTATE, constraint_name) and reports a bare restrict_violation with no
# constraint name as "this profile version is confirmed" — so every RAISE here
# names a constraint, or an illegal tailoring transition would surface to the
# client as a completely unrelated profile error.

TRANSITION_GUARD = """
CREATE OR REPLACE FUNCTION easyapply_tailoring_run_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    allowed text[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'queued' THEN
            RAISE EXCEPTION
                'a tailoring run must start in queued, not %', NEW.status
                USING ERRCODE = 'restrict_violation',
                      CONSTRAINT = 'ck_tailoring_runs_initial_status';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status = OLD.status THEN
        -- Progress within a state is fine; rewriting a finished run's verdict
        -- is not. A completed run is the record of what actually happened.
        IF OLD.status IN ('completed', 'failed')
           AND (NEW.failed_stage IS DISTINCT FROM OLD.failed_stage
                OR NEW.error_code IS DISTINCT FROM OLD.error_code
                OR NEW.attempt IS DISTINCT FROM OLD.attempt
                OR NEW.job_id IS DISTINCT FROM OLD.job_id
                OR NEW.profile_version_id IS DISTINCT FROM OLD.profile_version_id
                OR NEW.job_requirements_id IS DISTINCT FROM OLD.job_requirements_id) THEN
            RAISE EXCEPTION
                'tailoring run % is % and its outcome cannot be rewritten', OLD.id, OLD.status
                USING ERRCODE = 'restrict_violation',
                      CONSTRAINT = 'ck_tailoring_runs_terminal_frozen';
        END IF;
        RETURN NEW;
    END IF;

    allowed := CASE OLD.status
        WHEN 'queued'     THEN ARRAY['generating', 'failed']
        WHEN 'generating' THEN ARRAY['validating', 'failed']
        -- Two ways out of validating: a pass renders, a fail completes without
        -- an artifact. Grounding failure is a verdict, not a processing error.
        WHEN 'validating' THEN ARRAY['rendering', 'completed', 'failed']
        WHEN 'rendering'  THEN ARRAY['completed', 'failed']
        ELSE ARRAY[]::text[]
    END;

    IF NOT (NEW.status = ANY (allowed)) THEN
        RAISE EXCEPTION
            'illegal tailoring transition % -> %', OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation',
                  CONSTRAINT = 'ck_tailoring_runs_transition';
    END IF;

    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('tailoring_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('profile_version_id', sa.Integer(), nullable=False),
    sa.Column('job_requirements_id', sa.Integer(), nullable=False),
    sa.Column('match_result_id', sa.Integer(), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='queued', nullable=False),
    sa.Column('generator', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=True),
    sa.Column('prompt_version', sa.String(length=16), nullable=True),
    sa.Column('failed_stage', sa.String(length=16), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name='ck_tailoring_runs_error_code_matches_status'),
    sa.CheckConstraint("(status = 'failed') = (failed_stage IS NOT NULL)", name='ck_tailoring_runs_failed_stage_matches_status'),
    sa.CheckConstraint("(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)", name='ck_tailoring_runs_finished_at_matches_status'),
    sa.CheckConstraint("failed_stage IS NULL OR failed_stage IN ('generate', 'validate', 'render')", name='ck_tailoring_runs_failed_stage'),
    sa.CheckConstraint("generator IN ('deterministic', 'ai')", name='ck_tailoring_runs_generator'),
    sa.CheckConstraint("status IN ('queued', 'generating', 'validating', 'rendering', 'completed', 'failed')", name='ck_tailoring_runs_status'),
    sa.CheckConstraint('attempt > 0', name='ck_tailoring_runs_attempt_positive'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name='fk_tailoring_runs_job_id', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_requirements_id'], ['job_requirements.id'], name='fk_tailoring_runs_job_requirements_id', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['match_result_id'], ['match_results.id'], name='fk_tailoring_runs_match_result_id', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_tailoring_runs_profile_version_id', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_tailoring_runs_user_id', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'profile_version_id', 'attempt', name='uq_tailoring_runs_job_version_attempt')
    )
    op.create_index('ix_tailoring_runs_active', 'tailoring_runs', ['created_at'], unique=False, postgresql_where=sa.text("status NOT IN ('completed', 'failed')"))
    op.create_index(op.f('ix_tailoring_runs_job_requirements_id'), 'tailoring_runs', ['job_requirements_id'], unique=False)
    op.create_index(op.f('ix_tailoring_runs_profile_version_id'), 'tailoring_runs', ['profile_version_id'], unique=False)
    op.create_index('ix_tailoring_runs_user_id_created_at', 'tailoring_runs', ['user_id', 'created_at'], unique=False)
    op.create_index('ix_tailoring_runs_user_id_job_id', 'tailoring_runs', ['user_id', 'job_id'], unique=False)
    op.create_table('tailored_resumes',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tailoring_run_id', sa.Integer(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('schema_version', sa.String(length=16), nullable=False),
    sa.Column('grounding_status', sa.String(length=16), nullable=False),
    sa.Column('error_count', sa.Integer(), nullable=False),
    sa.Column('warning_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(grounding_status = 'passed') = (error_count = 0)", name='ck_tailored_resumes_passed_has_no_errors'),
    sa.CheckConstraint("grounding_status IN ('passed', 'failed')", name='ck_tailored_resumes_grounding_status'),
    sa.CheckConstraint('error_count >= 0 AND warning_count >= 0', name='ck_tailored_resumes_counts_not_negative'),
    sa.ForeignKeyConstraint(['tailoring_run_id'], ['tailoring_runs.id'], name='fk_tailored_resumes_tailoring_run_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tailoring_run_id', name='uq_tailored_resumes_run_id')
    )
    op.create_table('tailored_resume_artifacts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tailored_resume_id', sa.Integer(), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('media_type', sa.String(length=128), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('byte_size > 0', name='ck_tailored_resume_artifacts_byte_size_positive'),
    sa.CheckConstraint('char_length(content_sha256) = 64', name='ck_tailored_resume_artifacts_sha256_length'),
    sa.CheckConstraint('char_length(storage_key) > 0', name='ck_tailored_resume_artifacts_storage_key_present'),
    sa.ForeignKeyConstraint(['tailored_resume_id'], ['tailored_resumes.id'], name='fk_tailored_resume_artifacts_tailored_resume_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tailored_resume_id', name='uq_tailored_resume_artifacts_tailored_resume_id')
    )
    op.create_table('tailoring_issues',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tailored_resume_id', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('section', sa.String(length=32), nullable=False),
    sa.Column('field_path', sa.String(length=128), nullable=True),
    sa.Column('claim', sa.String(length=600), nullable=True),
    sa.Column('reason', sa.String(length=400), nullable=False),
    sa.CheckConstraint("severity IN ('error', 'warning')", name='ck_tailoring_issues_severity'),
    sa.CheckConstraint('char_length(reason) > 0', name='ck_tailoring_issues_reason_present'),
    sa.CheckConstraint('char_length(section) > 0', name='ck_tailoring_issues_section_present'),
    sa.ForeignKeyConstraint(['tailored_resume_id'], ['tailored_resumes.id'], name='fk_tailoring_issues_tailored_resume_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tailoring_issues_severity', 'tailoring_issues', ['tailored_resume_id', 'severity'], unique=False)
    op.create_index(op.f('ix_tailoring_issues_tailored_resume_id'), 'tailoring_issues', ['tailored_resume_id'], unique=False)
    # ### end Alembic commands ###

    op.execute(TRANSITION_GUARD)
    op.execute(
        """
        CREATE TRIGGER trg_tailoring_runs_transition
        BEFORE INSERT OR UPDATE ON tailoring_runs
        FOR EACH ROW EXECUTE FUNCTION easyapply_tailoring_run_transition();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS trg_tailoring_runs_transition ON tailoring_runs")
    op.execute("DROP FUNCTION IF EXISTS easyapply_tailoring_run_transition()")

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_tailoring_issues_tailored_resume_id'), table_name='tailoring_issues')
    op.drop_index('ix_tailoring_issues_severity', table_name='tailoring_issues')
    op.drop_table('tailoring_issues')
    op.drop_table('tailored_resume_artifacts')
    op.drop_table('tailored_resumes')
    op.drop_index('ix_tailoring_runs_user_id_job_id', table_name='tailoring_runs')
    op.drop_index('ix_tailoring_runs_user_id_created_at', table_name='tailoring_runs')
    op.drop_index(op.f('ix_tailoring_runs_profile_version_id'), table_name='tailoring_runs')
    op.drop_index(op.f('ix_tailoring_runs_job_requirements_id'), table_name='tailoring_runs')
    op.drop_index('ix_tailoring_runs_active', table_name='tailoring_runs', postgresql_where=sa.text("status NOT IN ('completed', 'failed')"))
    op.drop_table('tailoring_runs')
    # ### end Alembic commands ###
