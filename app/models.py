from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import Enum
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, relationship, DeclarativeBase, MappedColumn


class Base(DeclarativeBase):
    pass


class TimestampedMixin:
    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


PROJECT_ROLE = ("owner", "editor", "viewer")


def uuid_pk() -> Mapped:
    return MappedColumn(UUID(as_uuid=True), primary_key=True, default=uuid4)


class User(TimestampedMixin, Base):
    __tablename__ = "users"

    id: Mapped[object] = uuid_pk()
    email: Mapped[str] = MappedColumn(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = MappedColumn(Text, nullable=False)
    display_name: Mapped[str] = MappedColumn(String(255), nullable=False)
    is_active: Mapped[bool] = MappedColumn(Boolean, nullable=False, default=True)


class Session(TimestampedMixin, Base):
    __tablename__ = "sessions"

    id: Mapped[object] = uuid_pk()
    user_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    csrf_secret: Mapped[str] = MappedColumn(Text, nullable=False)
    user_agent: Mapped[str] = MappedColumn(String(512), nullable=True)
    ip_address: Mapped[str] = MappedColumn(String(45), nullable=True)
    last_seen_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(lazy="joined")


class Project(TimestampedMixin, Base):
    __tablename__ = "projects"

    id: Mapped[object] = uuid_pk()
    title: Mapped[str] = MappedColumn(String(255), nullable=False)
    description: Mapped[str] = MappedColumn(Text, nullable=True)
    owner_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = MappedColumn(String(16), nullable=False, default="editor")
    added_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IntExt(str, PyEnum):
    INT = "INT"
    EXT = "EXT"
    INT_EXT = "INT_EXT"


class Screenplay(TimestampedMixin, Base):
    __tablename__ = "screenplays"

    id: Mapped[object] = uuid_pk()
    project_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = MappedColumn(String(255), nullable=False)
    locked_at: Mapped[datetime | None] = MappedColumn(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(lazy="joined")
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="screenplay", order_by="Scene.order_key", cascade="all, delete-orphan"
    )


class Scene(TimestampedMixin, Base):
    __tablename__ = "scenes"

    id: Mapped[object] = uuid_pk()
    screenplay_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("screenplays.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_key: Mapped[float] = MappedColumn(Float, nullable=False)
    number: Mapped[str | None] = MappedColumn(Text, nullable=True)
    number_suffix: Mapped[str | None] = MappedColumn(Text, nullable=True)
    locked: Mapped[bool] = MappedColumn(Boolean, nullable=False, default=False)
    int_ext: Mapped[IntExt | None] = MappedColumn(Enum(IntExt, name="int_ext", create_constraint=True), nullable=True)
    location_entity_id: Mapped[object | None] = MappedColumn(
        UUID(as_uuid=True), nullable=True
    )
    time_of_day: Mapped[str | None] = MappedColumn(Text, nullable=True)
    heading_modifier: Mapped[str | None] = MappedColumn(Text, nullable=True)
    content: Mapped[dict] = MappedColumn(JSON, nullable=False)
    content_hash: Mapped[str] = MappedColumn(Text, nullable=False)
    node_order: Mapped[list[str]] = MappedColumn(ARRAY(String), nullable=False, default=list)
    last_ai_hash: Mapped[str | None] = MappedColumn(Text, nullable=True)

    screenplay: Mapped[Screenplay] = relationship(back_populates="scenes")


class EntityType(str, PyEnum):
    character = "character"
    location = "location"
    prop = "prop"
    vehicle = "vehicle"
    set_dressing = "set_dressing"
    wardrobe = "wardrobe"
    sound = "sound"
    vfx = "vfx"
    sfx = "sfx"
    makeup = "makeup"
    hair = "hair"
    stunt = "stunt"
    animal = "animal"
    extra = "extra"
    equipment = "equipment"
    camera_setup = "camera_setup"
    other = "other"


class Entity(TimestampedMixin, Base):
    __tablename__ = "entities"

    id: Mapped[object] = uuid_pk()
    project_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[EntityType] = MappedColumn(Enum(EntityType, name="entity_type", create_constraint=True), nullable=False)
    canonical_name: Mapped[str] = MappedColumn(String(255), nullable=False)
    aliases: Mapped[list[str]] = MappedColumn(ARRAY(String), nullable=False, default=[])
    attributes: Mapped[dict] = MappedColumn(JSON, nullable=False, default={})

    __table_args__ = (
        Index("entities_name_trgm", "canonical_name", postgresql_using="gin", postgresql_ops={"canonical_name": "gin_trgm_ops"}),
        sa.UniqueConstraint("project_id", "entity_type", "canonical_name", name="uq_entity_project_type_name"),
    )


class Annotation(TimestampedMixin, Base):
    __tablename__ = "annotations"

    id: Mapped[object] = uuid_pk()
    scene_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("scenes.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = MappedColumn(Text, nullable=False)
    start_offset: Mapped[int] = MappedColumn(nullable=False)
    end_offset: Mapped[int] = MappedColumn(nullable=False)
    entity_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = MappedColumn(Text, nullable=False)
    created_by: Mapped[object | None] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SceneEntity(Base):
    __tablename__ = "scene_entities"

    scene_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("scenes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    occurrence_count: Mapped[int] = MappedColumn(nullable=False, default=0)


class SceneMetrics(Base):
    __tablename__ = "scene_metrics"

    screenplay_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("screenplays.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scene_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("scenes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    start_page: Mapped[int] = MappedColumn(nullable=False)
    end_page: Mapped[int] = MappedColumn(nullable=False)
    page_length: Mapped[float] = MappedColumn(nullable=False)


class AiSuggestion(Base):
    __tablename__ = "ai_suggestions"

    id: Mapped[object] = uuid_pk()
    scene_id: Mapped[object] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("scenes.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[str] = MappedColumn(Text, nullable=False)
    matched_text: Mapped[str] = MappedColumn(Text, nullable=False)
    start_offset: Mapped[int | None] = MappedColumn(nullable=True)
    end_offset: Mapped[int | None] = MappedColumn(nullable=True)
    suggested_type: Mapped[EntityType] = MappedColumn(
        Enum(EntityType, name="entity_type", create_constraint=True), nullable=False
    )
    suggested_name: Mapped[str] = MappedColumn(Text, nullable=False)
    matched_entity_id: Mapped[object | None] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence: Mapped[float | None] = MappedColumn(sa.Numeric(precision=4, scale=3), nullable=True)
    model: Mapped[str] = MappedColumn(Text, nullable=False)
    prompt_version: Mapped[str] = MappedColumn(Text, nullable=False)
    status: Mapped[str] = MappedColumn(Text, nullable=False, default="pending")
    reviewed_by: Mapped[object | None] = MappedColumn(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = MappedColumn(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ai_suggestions_scene",
            "scene_id",
            postgresql_where=sa.text("status = 'pending'"),
        ),
    )