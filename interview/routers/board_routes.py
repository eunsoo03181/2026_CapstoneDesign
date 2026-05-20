"""
공유 게시판 (/board) API.

게시판에 노출되는 면접 세션 목록 조회.
- list_on_board=True 이고 is_shared=True 이며 not deleted 인 세션만 노출
- 로그인 사용자만 조회 (스크래핑 방지)
- 검색·정렬·페이지네이션 지원

상세는 기존 /api/sessions/{code}/public 그대로 사용.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from db import get_db, User, InterviewSession, Question
from auth.deps import get_current_user


router = APIRouter(prefix="/api/board", tags=["board"])


class BoardItem(BaseModel):
    public_code: str
    title: Optional[str] = None
    author_name: str          # 작성자 이름 (없으면 "익명")
    final_score_100: Optional[float]
    content_score_80: Optional[float]
    nonverbal_score_20: Optional[float]
    has_nonverbal: bool
    n_questions: int
    model_used: Optional[str] = None
    is_mine: bool             # 현재 사용자가 작성자인지
    completed_at: Optional[datetime]
    created_at: datetime


class BoardListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[BoardItem]


# 정렬 가능 컬럼 화이트리스트
SORT_MAP = {
    "created_at":      InterviewSession.created_at,
    "completed_at":    InterviewSession.completed_at,
    "final_score_100": InterviewSession.final_score_100,
    "title":           InterviewSession.title,
}


@router.get("/sessions", response_model=BoardListResponse)
def list_board_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    q: Optional[str] = Query(None, description="제목/작성자 부분 일치"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """게시판 목록 — 페이지네이션 + 검색 + 정렬."""

    base = (
        db.query(InterviewSession, User)
        .join(User, InterviewSession.user_id == User.id)
        .filter(InterviewSession.list_on_board == True)            # noqa: E712
        .filter(InterviewSession.is_shared == True)                 # noqa: E712
        .filter(InterviewSession.is_deleted == False)               # noqa: E712
    )

    if q:
        like = f"%{q}%"
        base = base.filter(or_(
            InterviewSession.title.ilike(like),
            User.name.ilike(like),
        ))

    total = base.count()

    col = SORT_MAP.get(sort, InterviewSession.created_at)
    base = base.order_by(col.desc() if order == "desc" else col.asc())

    rows = base.offset((page - 1) * page_size).limit(page_size).all()

    items: List[BoardItem] = []
    for s, u in rows:
        n_questions = (
            db.query(func.count(Question.id))
              .filter(Question.session_id == s.id).scalar() or 0
        )
        items.append(BoardItem(
            public_code=s.public_code,
            title=s.title,
            author_name=(u.name or "익명").strip() or "익명",
            final_score_100=s.final_score_100,
            content_score_80=s.content_score_80,
            nonverbal_score_20=s.nonverbal_score_20,
            has_nonverbal=(s.nonverbal_metrics is not None),
            n_questions=int(n_questions),
            model_used=s.model_used,
            is_mine=(s.user_id == current_user.id),
            completed_at=s.completed_at,
            created_at=s.created_at,
        ))

    return BoardListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )
