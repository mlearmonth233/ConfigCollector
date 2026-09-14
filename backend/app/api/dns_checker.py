from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.dns_checker import DnsCheckRequest, DnsCheckResultOut
from app.services.dns_checker import MAX_BULK_ENTRIES, check_bulk, parse_bulk_hosts

router = APIRouter(prefix="/api/dns-checker", tags=["dns-checker"])


@router.post("", response_model=list[DnsCheckResultOut])
async def check_dns_bulk(
    payload: DnsCheckRequest,
    user: User = Depends(get_current_user),
) -> list[DnsCheckResultOut]:
    """Stateless diagnostic tool - nothing here is persisted or scoped to an
    org, unlike the rest of the API. Any signed-in user can run it against
    any hostname/IP, same trust level as running `ping`/`nslookup` from
    their own machine."""
    entries = parse_bulk_hosts(payload.input)
    if not entries:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No hostnames or IP addresses provided")
    if len(entries) > MAX_BULK_ENTRIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many entries ({len(entries)}) - check at most {MAX_BULK_ENTRIES} at a time",
        )
    return await check_bulk(entries)
