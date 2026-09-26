"""complete confirmed version immutability

`10f47a8774be` made a confirmed profile version immutable in the database: every
column except `is_current`, and every snapshot child row. Two later migrations
widened the snapshot without widening that guard:

    a3f19c7d4e52   added full_name, email, phone, location and summary to
                   profile_versions, and the profile_links table
    bd5e41681322   added owner_type and project_id to profile_links

Neither touched `easyapply_profile_version_immutable()` or gave profile_links a
trigger, so a direct UPDATE of a confirmed version's contact block or summary,
and any write to its links, passed the database. Only the service's own
pre-check refused them — which is exactly the "application discipline" the
original migration says is not enforcement.

This re-creates the version guard with the five columns added since, and puts
profile_links under the same snapshot-child guard the other five child tables
already use (it carries `profile_version_id` directly, so no new function is
needed).

Revision ID: c3d9e1f47a20
Revises: bd5e41681322
Create Date: 2026-09-24 23:30:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c3d9e1f47a20"
down_revision: Union[str, Sequence[str], None] = "bd5e41681322"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _version_guard(extra_columns: tuple[str, ...]) -> str:
    columns = (
        "id",
        "profile_id",
        "version_number",
        "status",
        "created_by_type",
        "created_by_user_id",
        "created_at",
        "confirmed_at",
        *extra_columns,
    )
    changed = "\n        OR ".join(
        f"NEW.{name:<18} IS DISTINCT FROM OLD.{name}" for name in columns
    )
    return f"""
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
           {changed}
    ) THEN
        RAISE EXCEPTION
            'profile_version % is confirmed; only is_current may change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


#: Every column `a3f19c7d4e52` added to profile_versions. `is_current` is the one
#: column a confirmed version may still change, and stays out of the list.
SNAPSHOT_CONTACT_COLUMNS = ("full_name", "email", "phone", "location", "summary")


#: A project-owned link must name a project of its OWN version. The snapshot-child
#: guard looks only at `profile_version_id`, and `project_id` is a plain FK to
#: projects.id: a DRAFT's link row could point at a CONFIRMED version's project
#: and change what that frozen version shows beside it.
PROJECT_LINK_SAME_VERSION = """
CREATE OR REPLACE FUNCTION easyapply_profile_link_project_same_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.project_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM projects p
        WHERE p.id = NEW.project_id AND p.profile_version_id = NEW.profile_version_id
    ) THEN
        RAISE EXCEPTION
            'profile_links row names project %, which belongs to another profile_version',
            NEW.project_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_version_guard(SNAPSHOT_CONTACT_COLUMNS))
    op.execute(
        """
        CREATE TRIGGER trg_profile_links_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON profile_links
        FOR EACH ROW EXECUTE FUNCTION easyapply_snapshot_child_immutable();
        """
    )
    op.execute(PROJECT_LINK_SAME_VERSION)
    op.execute(
        """
        CREATE TRIGGER trg_profile_links_project_same_version
        BEFORE INSERT OR UPDATE ON profile_links
        FOR EACH ROW EXECUTE FUNCTION easyapply_profile_link_project_same_version();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_profile_links_project_same_version ON profile_links;"
    )
    op.execute("DROP FUNCTION IF EXISTS easyapply_profile_link_project_same_version();")
    op.execute("DROP TRIGGER IF EXISTS trg_profile_links_immutable ON profile_links;")
    op.execute(_version_guard(()))
