from pydantic import BaseModel


class DnsCheckRequest(BaseModel):
    # Free-form bulk text - one hostname/IP per line, comma-separated, or a
    # mix of both. See services/dns_checker.py's parse_bulk_hosts.
    input: str


class DnsCheckResultOut(BaseModel):
    input: str
    input_type: str
    resolved_ip: str | None
    reverse_hostname: str | None
    dns_ok: bool
    ping_ok: bool
    error: str | None

    model_config = {"from_attributes": True}
