from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import models
from app.deps import AuthPrincipal, get_db, require_project_role
from app.schema import ValidationIssue, ValidationListResponse
from app.sentry import breadcrumb

# Computed-on-read structural validation (SPEC §6). Nothing is persisted.
validation_router = APIRouter(prefix="/scenes/{scene_id}/validation", tags=["validation"])


@validation_router.get("", response_model=ValidationListResponse)
async def scene_validation(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Scene)
        .options(selectinload(models.Scene.screenplay))
        .where(models.Scene.id == scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")
    await require_project_role("viewer")(scene.screenplay.project_id, principal, db)

    issues = _validate_content(scene.content)
    breadcrumb("db", "scene validation", scene_id=str(scene_id), count=len(issues))
    return ValidationListResponse(items=issues)


def _validate_content(content: dict | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(content, dict):
        return issues

    nodes = content.get("content")
    if not isinstance(nodes, list):
        return issues

    prev_type: str | None = None
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        if not isinstance(node_type, str):
            continue

        if index == 0 and node_type != "sceneHeading":
            issues.append(
                ValidationIssue(
                    id=uuid4(),
                    type="warning",
                    message="Scene without Scene Heading.",
                    node_id="",
                )
            )

        if node_type == "dialogue" and prev_type != "character":
            issues.append(
                _issue(
                    "warning",
                    "Dialogue has no associated Character.",
                    _node_text_id(node),
                )
            )
        elif node_type == "parenthetical" and prev_type != "dialogue":
            issues.append(
                _issue(
                    "warning",
                    "Parenthetical outside Dialogue.",
                    _node_text_id(node),
                )
            )
        elif node_type == "character" and prev_type == "character":
            char_id = _character_id(node)
            issues.append(
                _issue(
                    "warning",
                    "Multiple consecutive Character nodes.",
                    char_id,
                )
            )

        prev_type = node_type
    return issues


def _issue(issue_type: str, message: str, node_id: str) -> ValidationIssue:
    return ValidationIssue(id=uuid4(), type=issue_type, message=message, node_id=node_id)


def _node_text_id(node: dict) -> str:
    """A text block's stable `id`, or '' if absent."""
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    node_id = attrs.get("id")
    return node_id if isinstance(node_id, str) else ""


def _character_id(node: dict) -> str:
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    char_id = attrs.get("characterId")
    return char_id if isinstance(char_id, str) else ""