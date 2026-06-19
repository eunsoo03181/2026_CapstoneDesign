"""
Credit (재화) 차감·조정·내역 관리.

규칙
- 면접 1회 생성 = 1 credit 소모.
- admin / moderator 는 차감 없이 통과 (단, CreditTransaction 에 delta=0 흔적 남김).
- 일반 사용자가 credit 0 이면 InsufficientCreditError 발생 → 면접 생성 차단.
- 관리자는 임의 delta(+/-) 로 사용자 잔액 조정 가능. 차감으로 음수 잔액은 0 으로 클램프.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from db.models import User, CreditTransaction
from db.utils import gen_uuid


class InsufficientCreditError(Exception):
    """credit 부족으로 작업 불가."""
    def __init__(self, user_id: str, current_balance: int = 0):
        super().__init__(f"insufficient credit (user={user_id}, balance={current_balance})")
        self.user_id = user_id
        self.current_balance = current_balance


def _is_unlimited(user: User) -> bool:
    """admin/moderator 는 credit 무한 — 차감 안 함."""
    return getattr(user, "role", "user") in ("admin", "moderator")


def consume_credit(
    db: Session,
    user: User,
    *,
    related_session_code: Optional[str] = None,
    reason: str = "interview_create",
) -> int:
    """
    면접 생성 시점에 호출.
    - admin/mod: 차감 없이 통과, delta=0 transaction 만 기록.
    - 일반 사용자: 잔액 1 이상이면 -1 차감 + transaction. 0 이면 InsufficientCreditError.

    반환: 차감 후 잔액.
    """
    if _is_unlimited(user):
        _add_tx(db, user, delta=0,
                balance_after=int(user.credits or 0),
                reason=f"free_pass({user.role})",
                related_session_code=related_session_code,
                created_by_user_id=user.id)
        db.commit()
        return int(user.credits or 0)

    current = int(user.credits or 0)
    if current <= 0:
        raise InsufficientCreditError(user.id, current_balance=current)

    user.credits = current - 1
    new_balance = user.credits
    _add_tx(db, user, delta=-1,
            balance_after=new_balance,
            reason=reason,
            related_session_code=related_session_code,
            created_by_user_id=user.id)
    db.commit()
    return new_balance


def refund_credit(
    db: Session,
    user: User,
    *,
    related_session_code: Optional[str] = None,
    reason: str = "interview_failed_refund",
) -> int:
    """면접 생성 직후 실패한 경우 1 credit 되돌림. admin/mod 는 noop.

    반환: 환불 후 잔액.
    """
    if _is_unlimited(user):
        return int(user.credits or 0)
    user.credits = int(user.credits or 0) + 1
    _add_tx(db, user, delta=+1, balance_after=user.credits,
            reason=reason, related_session_code=related_session_code,
            created_by_user_id=user.id)
    db.commit()
    return user.credits


def adjust_credit(
    db: Session,
    target_user: User,
    *,
    delta: int,
    reason: str,
    by_user: Optional[User] = None,
) -> int:
    """관리자 임의 조정. delta>0 부여, delta<0 차감 (음수 잔액 → 0 클램프).

    by_user: 조정을 수행한 관리자. transaction.created_by_user_id 에 기록.
    """
    current = int(target_user.credits or 0)
    new_balance = max(0, current + int(delta))
    actual_delta = new_balance - current
    target_user.credits = new_balance
    _add_tx(db, target_user, delta=actual_delta,
            balance_after=new_balance,
            reason=(reason or "admin_adjust"),
            related_session_code=None,
            created_by_user_id=(by_user.id if by_user else None))
    db.commit()
    return new_balance


def list_transactions(
    db: Session,
    user: User,
    *,
    limit: int = 50,
) -> List[CreditTransaction]:
    """사용자별 최근 N건 내역. 최신순."""
    return (
        db.query(CreditTransaction)
          .filter(CreditTransaction.user_id == user.id)
          .order_by(CreditTransaction.created_at.desc())
          .limit(int(limit))
          .all()
    )


# ------------------------------------------------------------------
def _add_tx(
    db: Session,
    user: User,
    *,
    delta: int,
    balance_after: int,
    reason: str,
    related_session_code: Optional[str],
    created_by_user_id: Optional[str],
) -> None:
    row = CreditTransaction(
        id=gen_uuid(),
        user_id=user.id,
        delta=int(delta),
        balance_after=int(balance_after),
        reason=str(reason)[:120],
        related_session_code=related_session_code,
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
    )
    db.add(row)
