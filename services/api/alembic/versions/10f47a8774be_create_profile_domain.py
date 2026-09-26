"""create profile domain

Revision ID: 10f47a8774be
Revises: 774fa9abfd6a
Create Date: 2026-09-17 22:36:35.627582

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '10f47a8774be'
down_revision: Union[str, Sequence[str], None] = '774fa9abfd6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Immutability is a business rule, so the database enforces it. Application
# discipline is not enforcement: it holds until the one code path that forgets.
#
# The rule has two halves. A confirmed profile_version may change only its
# is_current flag — nothing else, and it can never be deleted. And a confirmed
# version's snapshot rows may not be inserted, changed or removed at all.
#
# A consequence worth knowing: build a version as a draft, populate it, then
# confirm it. Confirming first and copying afterwards is refused.

SNAPSHOT_CHILD_TABLES = (
    "experiences",
    "education",
    "skills",
    "projects",
    "certifications",
)

PROFILE_VERSION_GUARD = """
CREATE OR REPLACE FUNCTION easyapply_profile_version_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'confirmed' THEN
            RAISE EXCEPTION
                'profile_version % is confirmed and cannot be deleted', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status = 'confirmed' AND (
           NEW.id                 IS DISTINCT FROM OLD.id
        OR NEW.profile_id         IS DISTINCT FROM OLD.profile_id
        OR NEW.version_number     IS DISTINCT FROM OLD.version_number
        OR NEW.status             IS DISTINCT FROM OLD.status
        OR NEW.created_by_type    IS DISTINCT FROM OLD.created_by_type
        OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
        OR NEW.created_at         IS DISTINCT FROM OLD.created_at
        OR NEW.confirmed_at       IS DISTINCT FROM OLD.confirmed_at
    ) THEN
        RAISE EXCEPTION
            'profile_version % is confirmed; only is_current may change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""

SNAPSHOT_CHILD_GUARD = """
CREATE OR REPLACE FUNCTION easyapply_snapshot_child_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_version_id integer := NULL;
    new_version_id integer := NULL;
    offending_version_id integer;
BEGIN
    -- OLD and NEW must be read behind TG_OP branches: touching an unassigned
    -- record raises, so a CASE inside the query is not enough.
    IF TG_OP <> 'INSERT' THEN
        old_version_id := OLD.profile_version_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_version_id := NEW.profile_version_id;
    END IF;

    -- An UPDATE that moves a row between versions must satisfy both ends.
    SELECT pv.id INTO offending_version_id
    FROM profile_versions pv
    WHERE pv.status = 'confirmed'
      AND pv.id IN (old_version_id, new_version_id)
    LIMIT 1;

    IF offending_version_id IS NOT NULL THEN
        RAISE EXCEPTION
            '% belongs to confirmed profile_version %, which is immutable',
            TG_TABLE_NAME, offending_version_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
"""

# project_technologies reaches its version through projects.
PROJECT_TECHNOLOGY_GUARD = """
CREATE OR REPLACE FUNCTION easyapply_project_technology_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    old_project_id integer := NULL;
    new_project_id integer := NULL;
    offending_version_id integer;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_project_id := OLD.project_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_project_id := NEW.project_id;
    END IF;

    SELECT pv.id INTO offending_version_id
    FROM projects p
    JOIN profile_versions pv ON pv.id = p.profile_version_id
    WHERE pv.status = 'confirmed'
      AND p.id IN (old_project_id, new_project_id)
    LIMIT 1;

    IF offending_version_id IS NOT NULL THEN
        RAISE EXCEPTION
            'project_technologies belongs to confirmed profile_version %, which is immutable',
            offending_version_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
"""


def _create_immutability_guards() -> None:
    op.execute(PROFILE_VERSION_GUARD)
    op.execute(SNAPSHOT_CHILD_GUARD)
    op.execute(PROJECT_TECHNOLOGY_GUARD)

    op.execute(
        """
        CREATE TRIGGER trg_profile_versions_immutable
        BEFORE UPDATE OR DELETE ON profile_versions
        FOR EACH ROW EXECUTE FUNCTION easyapply_profile_version_immutable();
        """
    )

    for table in SNAPSHOT_CHILD_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION easyapply_snapshot_child_immutable();
            """
        )

    op.execute(
        """
        CREATE TRIGGER trg_project_technologies_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON project_technologies
        FOR EACH ROW EXECUTE FUNCTION easyapply_project_technology_immutable();
        """
    )


def _drop_immutability_guards() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_project_technologies_immutable ON project_technologies;")
    for table in SNAPSHOT_CHILD_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table};")
    op.execute("DROP TRIGGER IF EXISTS trg_profile_versions_immutable ON profile_versions;")

    op.execute("DROP FUNCTION IF EXISTS easyapply_project_technology_immutable();")
    op.execute("DROP FUNCTION IF EXISTS easyapply_snapshot_child_immutable();")
    op.execute("DROP FUNCTION IF EXISTS easyapply_profile_version_immutable();")


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('profiles',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_profiles_user_id', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', name='uq_profiles_user_id')
    )
    op.create_table('profile_versions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_by_type', sa.String(length=16), nullable=False),
    sa.Column('created_by_user_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'confirmed') = (confirmed_at IS NOT NULL)", name='ck_profile_versions_confirmed_at_matches_status'),
    sa.CheckConstraint("NOT is_current OR status = 'confirmed'", name='ck_profile_versions_current_is_confirmed'),
    sa.CheckConstraint("created_by_type <> 'user' OR created_by_user_id IS NOT NULL", name='ck_profile_versions_created_by_user_present'),
    sa.CheckConstraint("created_by_type IN ('user', 'ai', 'system')", name='ck_profile_versions_created_by_type'),
    sa.CheckConstraint("status IN ('draft', 'confirmed')", name='ck_profile_versions_status'),
    sa.CheckConstraint('version_number > 0', name='ck_profile_versions_version_number_positive'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], name='fk_profile_versions_created_by_user_id', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], name='fk_profile_versions_profile_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('profile_id', 'version_number', name='uq_profile_versions_profile_id_version_number')
    )
    op.create_index(op.f('ix_profile_versions_created_by_user_id'), 'profile_versions', ['created_by_user_id'], unique=False)
    op.create_index('uq_profile_versions_current', 'profile_versions', ['profile_id'], unique=True, postgresql_where=sa.text('is_current'))
    op.create_table('certifications',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('profile_version_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('issuing_organization', sa.String(length=255), nullable=True),
    sa.Column('issue_date', sa.Date(), nullable=True),
    sa.Column('expiration_date', sa.Date(), nullable=True),
    sa.Column('credential_id', sa.String(length=255), nullable=True),
    sa.Column('credential_url', sa.Text(), nullable=True),
    sa.CheckConstraint('expiration_date IS NULL OR issue_date IS NULL OR expiration_date >= issue_date', name='ck_certifications_expiration_after_issue'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_certifications_profile_version_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_certifications_profile_version_id'), 'certifications', ['profile_version_id'], unique=False)
    op.create_table('education',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('profile_version_id', sa.Integer(), nullable=False),
    sa.Column('institution', sa.String(length=255), nullable=False),
    sa.Column('degree', sa.String(length=255), nullable=True),
    sa.Column('field_of_study', sa.String(length=255), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('start_date_precision', sa.String(length=8), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('end_date_precision', sa.String(length=8), nullable=True),
    sa.Column('grade', sa.String(length=64), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.CheckConstraint("end_date_precision IS NULL OR end_date_precision IN ('year', 'month', 'day')", name='ck_education_end_date_precision'),
    sa.CheckConstraint("start_date_precision IS NULL OR start_date_precision IN ('year', 'month', 'day')", name='ck_education_start_date_precision'),
    sa.CheckConstraint('(end_date IS NULL) = (end_date_precision IS NULL)', name='ck_education_end_date_precision_paired'),
    sa.CheckConstraint('(start_date IS NULL) = (start_date_precision IS NULL)', name='ck_education_start_date_precision_paired'),
    sa.CheckConstraint('end_date IS NULL OR start_date IS NULL OR end_date >= start_date', name='ck_education_end_date_after_start_date'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_education_profile_version_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_education_profile_version_id'), 'education', ['profile_version_id'], unique=False)
    op.create_table('experiences',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('profile_version_id', sa.Integer(), nullable=False),
    sa.Column('company', sa.String(length=255), nullable=False),
    sa.Column('job_title', sa.String(length=255), nullable=False),
    sa.Column('employment_type', sa.String(length=32), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('start_date_precision', sa.String(length=8), server_default='day', nullable=False),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('end_date_precision', sa.String(length=8), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.CheckConstraint("employment_type IS NULL OR employment_type IN ('full_time', 'part_time', 'contract', 'internship', 'freelance', 'temporary')", name='ck_experiences_employment_type'),
    sa.CheckConstraint("end_date_precision IS NULL OR end_date_precision IN ('year', 'month', 'day')", name='ck_experiences_end_date_precision'),
    sa.CheckConstraint("start_date_precision IS NULL OR start_date_precision IN ('year', 'month', 'day')", name='ck_experiences_start_date_precision'),
    sa.CheckConstraint('(end_date IS NULL) = (end_date_precision IS NULL)', name='ck_experiences_end_date_precision_paired'),
    sa.CheckConstraint('end_date IS NULL OR end_date >= start_date', name='ck_experiences_end_date_after_start_date'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_experiences_profile_version_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_experiences_profile_version_id'), 'experiences', ['profile_version_id'], unique=False)
    op.create_table('projects',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('profile_version_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('role', sa.String(length=255), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('start_date_precision', sa.String(length=8), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('end_date_precision', sa.String(length=8), nullable=True),
    sa.Column('project_url', sa.Text(), nullable=True),
    sa.CheckConstraint("end_date_precision IS NULL OR end_date_precision IN ('year', 'month', 'day')", name='ck_projects_end_date_precision'),
    sa.CheckConstraint("start_date_precision IS NULL OR start_date_precision IN ('year', 'month', 'day')", name='ck_projects_start_date_precision'),
    sa.CheckConstraint('(end_date IS NULL) = (end_date_precision IS NULL)', name='ck_projects_end_date_precision_paired'),
    sa.CheckConstraint('(start_date IS NULL) = (start_date_precision IS NULL)', name='ck_projects_start_date_precision_paired'),
    sa.CheckConstraint('end_date IS NULL OR start_date IS NULL OR end_date >= start_date', name='ck_projects_end_date_after_start_date'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_projects_profile_version_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_profile_version_id'), 'projects', ['profile_version_id'], unique=False)
    op.create_table('skills',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('profile_version_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('proficiency', sa.String(length=16), nullable=True),
    sa.Column('years_of_experience', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.CheckConstraint("proficiency IS NULL OR proficiency IN ('beginner', 'intermediate', 'advanced', 'expert')", name='ck_skills_proficiency'),
    sa.CheckConstraint('years_of_experience IS NULL OR years_of_experience >= 0', name='ck_skills_years_of_experience_not_negative'),
    sa.ForeignKeyConstraint(['profile_version_id'], ['profile_versions.id'], name='fk_skills_profile_version_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_skills_profile_version_id_name_lower', 'skills', ['profile_version_id', sa.literal_column('lower(name)')], unique=True)
    op.create_table('project_technologies',
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_project_technologies_project_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('project_id', 'name')
    )
    # ### end Alembic commands ###

    _create_immutability_guards()


def downgrade() -> None:
    """Downgrade schema."""
    _drop_immutability_guards()

    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('project_technologies')
    op.drop_index('uq_skills_profile_version_id_name_lower', table_name='skills')
    op.drop_table('skills')
    op.drop_index(op.f('ix_projects_profile_version_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_experiences_profile_version_id'), table_name='experiences')
    op.drop_table('experiences')
    op.drop_index(op.f('ix_education_profile_version_id'), table_name='education')
    op.drop_table('education')
    op.drop_index(op.f('ix_certifications_profile_version_id'), table_name='certifications')
    op.drop_table('certifications')
    op.drop_index('uq_profile_versions_current', table_name='profile_versions', postgresql_where=sa.text('is_current'))
    op.drop_index(op.f('ix_profile_versions_created_by_user_id'), table_name='profile_versions')
    op.drop_table('profile_versions')
    op.drop_table('profiles')
    # ### end Alembic commands ###
