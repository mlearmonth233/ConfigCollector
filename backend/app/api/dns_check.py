from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.dns_check import DnsCheckRequest, DnsCheckResultOut
from app.services.dns_check import MAX_TARGETS, run_dns_checks

router = APIRouter(prefix="/api/dns-check", tags=["dns-check"])


@router.post("", response_model=list[DnsCheckResultOut])
async def check_dns(
    payload: DnsCheckRequest,
    user: User = Depends(get_current_user),
) -> list[DnsCheckResultOut]:
    """Bulk ping/DNS/reverse-DNS check for a pasted list of hostnames or
    IPs - a stateless diagnostic, not tied to this org's device inventory,
    so nothing here is persisted. Requires login anyway (like every other
    endpoint) so this can't be used as an open network-probing relay by
    anyone who can reach this server."""
    # De-duplicate while preserving order - a pasted list commonly repeats
    # entries, and there's no reason to check (or show) the same target twice.
    seen: dict[str, None] = {}
    for raw in payload.targets:
        target = raw.strip()
        if target:
            seen.setdefault(target, None)
    targets = list(seen.keys())

    if not targets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide at least one hostname or IP")
    if len(targets) > MAX_TARGETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many targets at once (max {MAX_TARGETS}) - split into smaller batches",
        )

    results = await run_dns_checks(targets)
    return [DnsCheckResultOut.model_validate(r) for r in results]
