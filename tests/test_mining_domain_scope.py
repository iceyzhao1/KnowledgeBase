from fastapi import HTTPException

from knowledge_mining.mining.api.domain_scope import ensure_same_domain, require_domain


def test_require_domain_trims_valid_value():
    assert require_domain("  odn  ") == "odn"


def test_require_domain_rejects_blank():
    try:
        require_domain("  ")
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("blank domain must fail")


def test_cross_domain_resource_is_hidden():
    try:
        ensure_same_domain("civil_engineering", "odn", "run")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("cross-domain resource must be hidden")
