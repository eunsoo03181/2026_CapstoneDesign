"""
관리자 전용 라우터 — 모든 엔드포인트는 admin role 필수.

기능:
  - 통계 (전체/사용자별)
  - 사용자 관리 (역할/닉네임/계정 활성)
  - 사용자별 세션 (삭제된 것 포함)
  - 임퍼소네이션 (사용자 입장에서 보기)
"""

from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import get_db, User, InterviewSession, CreditTransaction
from auth.deps import require_admin
from app.scoring.credit_ops import adjust_credit, list_transactions


router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============================================================
# 통계
# ============================================================

@router.get("/stats")
def overall_stats(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """전체 시스템 통계."""
    total_users      = db.query(func.count(User.id)).scalar() or 0
    active_users     = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0   # noqa: E712
    admin_count      = db.query(func.count(User.id)).filter(User.role == "admin").scalar() or 0
    moderator_count  = db.query(func.count(User.id)).filter(User.role == "moderator").scalar() or 0
    suspended_count  = db.query(func.count(User.id)).filter(User.is_active == False).scalar() or 0  # noqa: E712

    total_sessions   = db.query(func.count(InterviewSession.id)).scalar() or 0
    active_sessions  = (
        db.query(func.count(InterviewSession.id))
        .filter(InterviewSession.is_deleted == False).scalar() or 0    # noqa: E712
    )
    deleted_sessions = (
        db.query(func.count(InterviewSession.id))
        .filter(InterviewSession.is_deleted == True).scalar() or 0     # noqa: E712
    )
    shared_sessions  = (
        db.query(func.count(InterviewSession.id))
        .filter(InterviewSession.is_shared == True).scalar() or 0      # noqa: E712
    )

    avg_score_q = (
        db.query(func.avg(InterviewSession.final_score_100))
        .filter(InterviewSession.is_deleted == False)                  # noqa: E712
        .filter(InterviewSession.final_score_100.isnot(None))
    )
    avg_score = avg_score_q.scalar()

    # 모델별 사용 횟수 (삭제 제외)
    model_rows = (
        db.query(
            InterviewSession.model_used.label("model"),
            func.count(InterviewSession.id).label("cnt"),
        )
        .filter(InterviewSession.is_deleted == False)                  # noqa: E712
        .group_by(InterviewSession.model_used)
        .order_by(func.count(InterviewSession.id).desc())
        .all()
    )
    # 라벨 정리 — null 은 '미상' 으로 통합 표시
    model_counts = []
    counted_total = sum(int(r.cnt) for r in model_rows) or 0
    for r in model_rows:
        model_counts.append({
            "model": r.model or "미상",
            "count": int(r.cnt),
            "percent": round((int(r.cnt) / counted_total * 100), 1) if counted_total else 0.0,
        })

    return {
        "users": {
            "total":     total_users,
            "active":    active_users,
            "suspended": suspended_count,
            "admin":     admin_count,
            "moderator": moderator_count,
        },
        "sessions": {
            "total":   total_sessions,
            "active":  active_sessions,
            "deleted": deleted_sessions,
            "shared":  shared_sessions,
            "avg_final_score_100": round(float(avg_score), 2) if avg_score is not None else None,
        },
        "models": {
            "total_counted": counted_total,
            "items":         model_counts,
        },
    }


# ============================================================
# 사용자 목록 / 상세 / 수정
# ============================================================

class UserAdminRow(BaseModel):
    id: str
    email: str
    name: str
    username: Optional[str]
    auth_provider: str
    role: str
    is_active: bool
    suspended_until: Optional[datetime]
    phone: Optional[str]
    last_login_at: Optional[datetime]
    created_at: datetime
    session_count: int = 0


@router.get("/users", response_model=List[UserAdminRow])
def list_users(
    sort: str = Query("created_at", description="id|email|name|username|role|is_active|last_login_at|created_at|session_count"),
    order: str = Query("desc", description="asc | desc"),
    q: Optional[str] = Query(None, description="이메일/이름/사용자명 부분 일치"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """전체 사용자 목록 + 세션 수 + 정렬 + 검색."""
    # 세션 수 서브쿼리
    session_count_sq = (
        db.query(
            InterviewSession.user_id.label("uid"),
            func.count(InterviewSession.id).label("cnt"),
        )
        .filter(InterviewSession.is_deleted == False)   # noqa: E712
        .group_by(InterviewSession.user_id)
        .subquery()
    )

    base = db.query(User, func.coalesce(session_count_sq.c.cnt, 0).label("cnt")) \
             .outerjoin(session_count_sq, User.id == session_count_sq.c.uid)

    if q:
        like = f"%{q}%"
        base = base.filter(
            (User.email.ilike(like)) |
            (User.name.ilike(like)) |
            (User.username.ilike(like))
        )

    # 정렬
    sort_map = {
        "id": User.id, "email": User.email, "name": User.name, "username": User.username,
        "role": User.role, "is_active": User.is_active,
        "last_login_at": User.last_login_at, "created_at": User.created_at,
        "session_count": session_count_sq.c.cnt,
    }
    col = sort_map.get(sort, User.created_at)
    base = base.order_by(col.desc() if order == "desc" else col.asc())

    rows = base.all()
    return [
        UserAdminRow(
            id=u.id, email=u.email, name=u.name or "",
            username=u.username, auth_provider=u.auth_provider,
            role=u.role, is_active=u.is_active,
            suspended_until=u.suspended_until,
            phone=u.phone, last_login_at=u.last_login_at,
            created_at=u.created_at, session_count=int(cnt or 0),
        )
        for (u, cnt) in rows
    ]


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    session_count = db.query(func.count(InterviewSession.id)) \
                     .filter(InterviewSession.user_id == user_id).scalar() or 0
    deleted_count = db.query(func.count(InterviewSession.id)) \
                     .filter(InterviewSession.user_id == user_id, InterviewSession.is_deleted == True).scalar() or 0  # noqa: E712
    return {
        "id": u.id, "email": u.email, "name": u.name or "",
        "username": u.username, "phone": u.phone, "picture": u.picture,
        "auth_provider": u.auth_provider, "role": u.role,
        "is_active": u.is_active,
        "suspended_until": u.suspended_until.isoformat() if u.suspended_until else None,
        "last_login_at": u.last_login_at, "created_at": u.created_at,
        "session_count_total":   int(session_count),
        "session_count_deleted": int(deleted_count),
    }


class UpdateRoleBody(BaseModel):
    role: str   # user | moderator | admin


@router.put("/users/{user_id}/role")
def update_role(
    user_id: str,
    body: UpdateRoleBody,
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    if body.role not in ("user", "moderator", "admin"):
        raise HTTPException(400, "올바른 역할이 아닙니다.")
    if user_id == me.id and body.role != "admin":
        raise HTTPException(400, "본인의 admin 권한은 직접 해제할 수 없습니다.")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    u.role = body.role
    db.commit()
    return {"ok": True, "role": u.role}


class UpdateNameBody(BaseModel):
    name: str


@router.put("/users/{user_id}/name")
def update_name(
    user_id: str,
    body: UpdateNameBody,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    u.name = (body.name or "").strip()[:100]
    db.commit()
    return {"ok": True, "name": u.name}


class UpdateActiveBody(BaseModel):
    is_active: bool
    # is_active=False 일 때 의미가 있음.
    #   - duration_days=null  → 영구 정지
    #   - duration_days=정수  → 그 일수만큼 정지 (만료 시 deps 에서 자동 해제)
    duration_days: Optional[int] = None


@router.put("/users/{user_id}/active")
def update_active(
    user_id: str,
    body: UpdateActiveBody,
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    if user_id == me.id and not body.is_active:
        raise HTTPException(400, "본인 계정은 정지할 수 없습니다.")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")

    if not body.is_active:
        # 정지 시도 — 관리자 계정은 정지 불가
        if u.role == "admin":
            raise HTTPException(400, "관리자 계정은 정지할 수 없습니다. 먼저 역할을 변경하세요.")
        u.is_active = False
        if body.duration_days and body.duration_days > 0:
            u.suspended_until = datetime.utcnow() + timedelta(days=int(body.duration_days))
        else:
            # 영구 정지
            u.suspended_until = None
    else:
        # 해제 — suspended_until 도 같이 비움
        u.is_active = True
        u.suspended_until = None

    db.commit()
    return {
        "ok": True,
        "is_active": u.is_active,
        "suspended_until": u.suspended_until.isoformat() if u.suspended_until else None,
    }


# ============================================================
# 사용자별 세션 (삭제 포함)
# ============================================================

@router.get("/users/{user_id}/sessions")
def user_sessions(
    user_id: str,
    include_deleted: bool = Query(True),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = db.query(InterviewSession).filter(InterviewSession.user_id == user_id)
    if not include_deleted:
        q = q.filter(InterviewSession.is_deleted == False)   # noqa: E712

    sort_map = {
        "created_at":    InterviewSession.created_at,
        "completed_at":  InterviewSession.completed_at,
        "final_score_100": InterviewSession.final_score_100,
        "is_shared":     InterviewSession.is_shared,
        "is_deleted":    InterviewSession.is_deleted,
        "title":         InterviewSession.title,
    }
    col = sort_map.get(sort, InterviewSession.created_at)
    q = q.order_by(col.desc() if order == "desc" else col.asc())

    out = []
    for s in q.all():
        out.append({
            "public_code": s.public_code,
            "title": s.title,
            "status": s.status,
            "final_score_100": s.final_score_100,
            "content_score_80": s.content_score_80,
            "nonverbal_score_20": s.nonverbal_score_20,
            "is_shared": s.is_shared,
            "is_deleted": s.is_deleted,
            "share_includes_audio":  s.share_includes_audio,
            "share_includes_video":  s.share_includes_video,
            "share_includes_resume": s.share_includes_resume,
            "list_on_board":         s.list_on_board,
            "model_used":   s.model_used,
            "started_at":   s.started_at,
            "completed_at": s.completed_at,
            "created_at":   s.created_at,
            "deleted_at":   s.deleted_at,
        })
    return out


# ============================================================
# 임퍼소네이션 — 사용자 입장에서 보기
# ============================================================

@router.post("/impersonate/exit")
def stop_impersonate(request: Request, db: Session = Depends(get_db)):
    """임퍼소네이션 종료 — 원래 관리자로 복귀.

    이 라우트는 **반드시** /impersonate/{user_id} 보다 먼저 선언되어야 함
    (그렇지 않으면 user_id='exit' 으로 매칭되어 require_admin 에서 403).
    또한 require_admin 을 걸지 않음 — 호출 시점엔 세션 user_id 가
    임퍼소네이션 대상으로 바뀌어 있어서 admin 체크가 통과되지 않기 때문.
    """
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return {"ok": True, "was_impersonating": False}
    # 백업해둔 관리자 id 로 복귀
    request.session["user_id"] = admin_id
    request.session.pop("admin_id", None)
    return {"ok": True, "was_impersonating": True}


@router.post("/impersonate/{user_id}")
def start_impersonate(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    """관리자가 다른 사용자처럼 시스템을 둘러볼 수 있게 세션을 전환."""
    if user_id == me.id:
        raise HTTPException(400, "자기 자신의 화면으로 입장할 수 없습니다.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")

    # 원래 관리자 ID 를 백업하고, session 의 user_id 를 대상으로 전환
    request.session["admin_id"] = me.id
    request.session["user_id"]  = user_id
    return {"ok": True, "viewing_as": {"id": target.id, "name": target.name, "email": target.email}}


# ============================================================
# Credit (재화) 관리
# ============================================================

class CreditAdjustBody(BaseModel):
    delta: int                                 # 양수=부여 / 음수=차감 (음수 잔액은 0 클램프)
    reason: Optional[str] = "admin_adjust"     # 사유 (감사 로그)


@router.get("/users/{user_id}/credits")
def get_user_credits(
    user_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """사용자의 현재 잔액 + 최근 N건 transactions."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")

    txs = list_transactions(db, target, limit=limit)
    return {
        "user_id": target.id,
        "name": target.name,
        "email": target.email,
        "role": target.role,
        "balance": int(target.credits or 0),
        "unlimited": target.role in ("admin", "moderator"),
        "transactions": [
            {
                "id": t.id,
                "delta": t.delta,
                "balance_after": t.balance_after,
                "reason": t.reason,
                "related_session_code": t.related_session_code,
                "created_by_user_id": t.created_by_user_id,
                "created_at": t.created_at,
            } for t in txs
        ],
    }


@router.post("/users/{user_id}/credits")
def adjust_user_credits(
    user_id: str,
    body: CreditAdjustBody,
    db: Session = Depends(get_db),
    me: User = Depends(require_admin),
):
    """관리자가 임의 delta 로 credit 조정. 음수 잔액 → 0 클램프."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    if not isinstance(body.delta, int) or body.delta == 0:
        raise HTTPException(400, "delta 는 0 이 아닌 정수여야 합니다.")

    previous = int(target.credits or 0)
    new_balance = adjust_credit(
        db, target,
        delta=body.delta,
        reason=(body.reason or "admin_adjust")[:120],
        by_user=me,
    )
    return {
        "ok": True,
        "user_id": target.id,
        "previous_balance": previous,
        "new_balance": new_balance,
        "delta_applied": new_balance - previous,    # 음수 잔액 클램프 반영
    }
