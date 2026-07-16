"""La table api_key existe, est insérable, et son unicité de key_hash tient."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import ApiKey
from app.db.session import get_session


def test_api_key_insert_and_unique_hash():
    h = f"hash-{uuid.uuid4().hex}"
    made = []
    with get_session() as db:
        k = ApiKey(scope="platform", prefix="sk_plat_ab12", key_hash=h,
                   label="test", created_by="admin@test")
        db.add(k)
        db.commit()
        made.append(k.id)
    try:
        with get_session() as db:
            db.add(ApiKey(scope="platform", prefix="sk_plat_zz99", key_hash=h,
                          label="dup", created_by="admin@test"))
            with pytest.raises(IntegrityError):
                db.commit()
    finally:
        with get_session() as db:
            db.query(ApiKey).filter(ApiKey.id.in_(made)).delete(synchronize_session=False)
            db.commit()
