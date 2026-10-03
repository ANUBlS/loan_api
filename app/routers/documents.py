import uuid

from fastapi import APIRouter, Response

from ..deps import DB, CurrentUser
from ..services import documents as doc_service

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.get(
    "/{document_id}/download",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def download(document_id: uuid.UUID, user: CurrentUser, db: DB):
    doc = doc_service.get_for_user(db, user, document_id)
    db.refresh(doc, ["content"])
    return Response(
        content=doc.content,
        media_type=doc.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{doc.file_name}"',
            "Cache-Control": "private, no-store",
        },
    )
