"""An org's device-naming rules (see services/hostname_detection.py) - how
"Add device" and bulk add guess role, zone and device type from a name."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.custom_device_types import load_catalog
from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.hostname_rule import HostnameRule
from app.models.user import User
from app.schemas.device import DeviceDetectionOut
from app.schemas.hostname_rule import (
    HostnameRuleIn,
    HostnameRuleOut,
    HostnameRulesOut,
    HostnameRulesReplace,
    HostnameTestIn,
)
from app.services.hostname_detection import BUILTIN_RULES, Rule, detect, rules_from_rows, validate_rule

router = APIRouter(prefix="/api/hostname-rules", tags=["hostname-rules"])


async def load_rules(db: AsyncSession, org_id) -> tuple[Rule, ...] | None:
    """The org's saved rules in order, or None when it has none (callers
    then get the built-ins from detect())."""
    rows = list(await db.scalars(select(HostnameRule).where(HostnameRule.org_id == org_id)))
    return rules_from_rows(rows) if rows else None


def _rule_from_in(item: HostnameRuleIn) -> Rule:
    return Rule(
        pattern=item.pattern,
        match_mode=item.match_mode,
        device_role=item.device_role,
        role_label=item.role_label,
        network_zone=item.network_zone,
        device_type=item.device_type,
    )


def _to_out(rules: tuple[Rule, ...]) -> list[HostnameRuleOut]:
    return [
        HostnameRuleOut(
            pattern=r.pattern,
            match_mode=r.match_mode,
            device_role=r.device_role,
            role_label=r.role_label,
            network_zone=r.network_zone,
            device_type=r.device_type,
            sort_order=i,
        )
        for i, r in enumerate(rules)
    ]


@router.get("", response_model=HostnameRulesOut)
async def get_rules(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> HostnameRulesOut:
    rules = await load_rules(db, user.org_id)
    return HostnameRulesOut(rules=_to_out(rules or BUILTIN_RULES), using_builtin=rules is None)


async def _validate_all(db: AsyncSession, org_id, items: list[HostnameRuleIn]) -> list[Rule]:
    catalog = await load_catalog(db, org_id)
    rules = []
    for i, item in enumerate(items, start=1):
        rule = _rule_from_in(item)
        try:
            validate_rule(rule)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Rule {i}: {exc}") from exc
        if rule.device_type and rule.device_type not in catalog:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Rule {i}: unknown device type '{rule.device_type}'",
            )
        rules.append(rule)
    return rules


@router.put("", response_model=HostnameRulesOut)
async def replace_rules(
    payload: HostnameRulesReplace, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> HostnameRulesOut:
    rules = await _validate_all(db, admin.org_id, payload.rules)
    await db.execute(delete(HostnameRule).where(HostnameRule.org_id == admin.org_id))
    db.add_all(
        HostnameRule(
            org_id=admin.org_id,
            sort_order=i,
            pattern=r.pattern,
            match_mode=r.match_mode.value,
            device_role=r.device_role,
            role_label=r.role_label,
            network_zone=r.network_zone,
            device_type=r.device_type,
        )
        for i, r in enumerate(rules)
    )
    await db.commit()
    return HostnameRulesOut(rules=_to_out(tuple(rules) or BUILTIN_RULES), using_builtin=not rules)


@router.post("/test", response_model=DeviceDetectionOut)
async def test_rules(payload: HostnameTestIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> DeviceDetectionOut:
    """What the rules make of one hostname - the saved rules, or (when
    `rules` is given) an unsaved draft from the editor."""
    if payload.rules is not None:
        rules: tuple[Rule, ...] | None = tuple(_rule_from_in(r) for r in payload.rules) or None
    else:
        rules = await load_rules(db, user.org_id)
    result = detect(payload.name, rules)
    return DeviceDetectionOut(
        device_role=result.device_role,
        device_role_label=result.device_role_label,
        network_zone=result.network_zone,
        suggested_device_type=result.suggested_device_type,
    )
