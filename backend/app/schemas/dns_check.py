from pydantic import BaseModel, Field


class DnsCheckRequest(BaseModel):
    targets: list[str] = Field(min_length=1)


class DnsCheckResultOut(BaseModel):
    target: str
    ping_ok: bool
    forward_ok: bool
    forward_ips: list[str]
    reverse_ok: bool
    reverse_hostname: str | None

    model_config = {"from_attributes": True}
