from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models import EntityType

# For API compatibility, we provide lowercase strings that map to the enum
ENTITY_TYPES = [e.value.lower() for e in EntityType]

PROJECT_ROLES = ("owner", "editor", "viewer")
ROLE_RANKS = {"viewer": 1, "editor": 2, "owner": 3}


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    owner_id: UUID
    role: str
    created_at: datetime
    updated_at: datetime


class ProjectMemberOut(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str
    role: str
    added_at: datetime


# --- Requests ---


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def _strip_title(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("title must not be blank")
        return self


class UpdateProjectRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)


class AddMemberRequest(BaseModel):
    user_id: UUID
    role: str = Field(default="editor", pattern="^(owner|editor|viewer)$")


# --- Tiptap Node Schema ---

class TiptapNode(BaseModel):
    type: str
    attrs: dict[str, Any] | None = None
    content: list["TiptapNode"] | None = None
    text: str | None = None


# --- Screenplay Schemas ---

class ScreenplayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    title: str
    locked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CreateScreenplayRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class UpdateScreenplayRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)


# --- Scene Schemas ---

class SceneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    screenplay_id: UUID
    order_key: float
    number: str | None
    number_suffix: str | None
    locked: bool
    int_ext: str | None
    location_entity_id: UUID | None
    time_of_day: str | None
    heading_modifier: str | None
    content: TiptapNode
    content_hash: str
    created_at: datetime
    updated_at: datetime


class SceneListResponse(BaseModel):
    items: list[SceneOut]


class CreateSceneRequest(BaseModel):
    content: TiptapNode


class UpdateSceneRequest(BaseModel):
    content: TiptapNode | None = None
    int_ext: str | None = Field(default=None, pattern="^(INT|EXT|INT_EXT)$")
    location_entity_id: UUID | None = None
    time_of_day: str | None = None
    heading_modifier: str | None = None


class ReorderSceneRequest(BaseModel):
    order_key: float


class ReorderSceneResponse(BaseModel):
    items: list[SceneOut]


# --- Entity Schemas ---

class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    entity_type: str  # Keep as str for API compatibility, converted from Enum
    canonical_name: str
    aliases: list[str]
    attributes: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EntityListResponse(BaseModel):
    items: list[EntityOut]


class CreateEntityRequest(BaseModel):
    entity_type: str = Field(pattern="^(" + "|".join(ENTITY_TYPES) + ")$")
    canonical_name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class UpdateEntityRequest(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=255)
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None


class MergeEntitiesRequest(BaseModel):
    source_id: UUID
    target_id: UUID


# --- Annotation Schemas ---

class AnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scene_id: UUID
    node_id: str
    start_offset: int
    end_offset: int
    entity_id: UUID
    source: str
    created_by: UUID | None
    created_at: datetime


class AnnotationListResponse(BaseModel):
    items: list[AnnotationOut]


class CreateAnnotationRequest(BaseModel):
    node_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    entity_id: UUID

    @model_validator(mode="after")
    def _check_offsets(self):
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset must be less than end_offset")
        return self


# --- Report Schemas ---

class CharacterReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    canonical_name: str
    aliases: list[str]
    scene_ids: list[UUID]
    dialogue_count: int


class LocationReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    canonical_name: str
    aliases: list[str]
    scene_ids: list[UUID]


class EntityReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: str  # Keep as str for API compatibility, converted from Enum
    canonical_name: str
    aliases: list[str]
    scene_ids: list[UUID]
    occurrence_count: int


class RuntimeReport(BaseModel):
    runtime_minutes: int


class SceneMetrics(BaseModel):
    scene_id: UUID
    start_page: int
    end_page: int
    page_length: float


class PaginationReport(BaseModel):
    page_count: int
    runtime_minutes: int
    scene_metrics: list[SceneMetrics]


# --- AI Suggestion Schemas ---

class AiSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scene_id: UUID
    node_id: str
    matched_text: str
    start_offset: int | None
    end_offset: int | None
    suggested_type: str  # Keep as str for API compatibility, converted from Enum
    suggested_name: str
    matched_entity_id: UUID | None
    confidence: float | None
    model: str
    prompt_version: str
    status: str
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime


class AiSuggestionListResponse(BaseModel):
    items: list[AiSuggestionOut]


# --- Validation Schemas ---

class ValidationIssue(BaseModel):
    id: UUID
    type: str
    message: str
    node_id: str


class ValidationListResponse(BaseModel):
    items: list[ValidationIssue]


# --- Search Schemas ---

class SearchResult(BaseModel):
    id: str
    kind: str
    match: str
    snippet: str
    scene_id: UUID | None
    node_id: str | None
    start_offset: int | None
    end_offset: int | None


class SearchResultListResponse(BaseModel):
    items: list[SearchResult]