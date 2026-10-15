from datetime import datetime

from pydantic import BaseModel


class SkippedLineOut(BaseModel):
    row: int
    text: str
    reason: str


class TemplateProfile(BaseModel):
    """One golden template, usually one sheet of the imported standard
    workbook (services/standard_workbook.py)."""

    name: str
    platform: str  # "9800" | "aireos"
    kind: str = "local"  # "flex" (FlexConnect / vWLC) | "local"
    template: str = ""
    source: str | None = None  # workbook file it came from
    imported_at: datetime | None = None
    rules: int = 0
    optional: int = 0
    skipped_count: int = 0
    skipped: list[SkippedLineOut] = []


class ImportRecord(BaseModel):
    filename: str
    sha256: str
    imported_at: datetime
    imported_by: str | None = None
    sheets: list[str] = []
    rules: int = 0


class R2OItemModel(BaseModel):
    item_id: str
    row: int
    description: str
    commands: list[str]
    expected: str = ""
    criteria: str = ""
    stage: str | None = None  # PRE | POST
    level: str | None = None  # M | G
    comments: str | None = None
    enabled: bool = True


class R2OChecklist(BaseModel):
    """One sheet of the imported Return-to-Operations checklist."""

    name: str
    platform: str  # "9800" | "aireos"
    kind: str = "local"  # "flex" | "local"
    source: str | None = None
    imported_at: datetime | None = None
    items: list[R2OItemModel] = []


class WirelessStandard(BaseModel):
    """What "configured to standard" means for this organization's
    wireless controllers. Stored as JSON (models/wireless_audit.py), so a
    new field only needs a default here. Every list left empty skips its
    check rather than failing it."""

    # --- access point naming / addressing --------------------------------------------
    # Regular expression an AP name must match in full, e.g.
    # ^[A-Z]{3}-B\d{2}-F\d{2}-AP\d{3}$  (site-building-floor-number).
    ap_name_pattern: str = ""
    ap_name_case: str = "any"  # "upper" | "lower" | "any"
    # Appended to AP names for the DNS lookup when the name has no dot.
    dns_domain: str = ""
    check_dns: bool = True
    # Reverse (PTR) record must give back the AP's name.
    check_reverse_dns: bool = True
    ap_subnets: list[str] = []  # CIDRs AP management addresses must sit in
    approved_ap_models: list[str] = []  # prefixes, e.g. "C9130AXI", "AIR-AP2802"
    country_codes: list[str] = []  # e.g. ["US"]
    min_ap_uplink_mbps: int = 1000
    require_full_duplex: bool = True
    require_primary_controller: bool = True  # 9800: AP has a primary controller primed
    require_non_default_tags: bool = True  # 9800: no AP left on default-*-tag

    # --- AP groups (AireOS) / tags (9800) / WNCD -----------------------------------------
    # Regular expression AireOS AP group names and 9800 site tag names must
    # match, e.g. ^[A-Z0-9]{8}-APGROUP\d{2}(-(Production|Warehouse))?(-Outside)?$
    ap_group_name_pattern: str = ""
    flag_default_ap_group: bool = True  # APs left in default-group / default-site-tag
    # 802.11ac Wave 2 and 802.11ax APs should not share an AP group / site
    # tag (802.11ax then has to be disabled on every WLAN).
    flag_mixed_ap_generations: bool = True
    legacy_ap_models: list[str] = ["AIR-AP18", "AIR-AP28", "AIR-AP38", "AIR-AP48", "AIR-CAP", "AIR-AP15", "AIR-AP17", "AIR-AP27", "AIR-AP37"]
    max_aps_per_flex_site_tag: int = 300
    wncd_imbalance_ratio: float = 2.0  # busiest WNCD vs the average, by APs
    max_wncd_cpu_percent: int = 80

    # --- radios --------------------------------------------------------------------------
    flag_disabled_radios: bool = True
    allowed_24ghz_channels: list[int] = [1, 6, 11]

    # --- controller ---------------------------------------------------------------------
    approved_9800_versions: list[str] = []  # prefixes, e.g. "17.9.5", "17.12"
    approved_aireos_versions: list[str] = []  # e.g. "8.10.196"
    ntp_servers: list[str] = []
    syslog_servers: list[str] = []
    dns_servers: list[str] = []
    require_ha: bool = False  # SSO pair with a hot standby
    max_cpu_percent: int = 80
    max_snapshot_age_hours: int = 24
    security_checks: bool = True  # telnet, HTTP, default SNMP communities...

    # --- WLANs ------------------------------------------------------------------------------
    required_ssids: list[str] = []
    flag_unexpected_ssids: bool = False  # SSIDs not in required_ssids
    open_ssids_allowed: list[str] = []  # guest SSIDs allowed without L2 security

    # --- golden configuration templates (see services/config_template.py) ----------------
    # Imported from the standard workbook, one per sheet. A controller is
    # checked against the profile assigned to it in profile_assignments
    # (device name -> profile name, "none" to skip), else the only profile
    # for its platform, else the flex / local one by its kind (a C9800-CL,
    # or a 9800 whose site tags are all "no local-site", is flex).
    template_profiles: list[TemplateProfile] = []
    profile_assignments: dict[str, str] = {}
    import_history: list[ImportRecord] = []
    # Return-to-Operations checklists (services/r2o.py), chosen per
    # controller the same way as the template profiles; r2o_assignments
    # overrides by device name ("none" to skip).
    r2o_checklists: list[R2OChecklist] = []
    r2o_assignments: dict[str, str] = {}
    r2o_import_history: list[ImportRecord] = []
    # Hand-written templates, used for a platform with no profile.
    template_9800: str = ""
    template_aireos: str = ""
    # {{name}} placeholders in the templates take these values.
    template_variables: dict[str, str] = {}

    # --- clients ------------------------------------------------------------------------------
    client_rssi_warn: int = -67
    client_rssi_fail: int = -75
    client_snr_warn: int = 25
    client_snr_fail: int = 15
    max_clients_per_ap: int = 50
    flag_legacy_clients: bool = True  # 802.11a/b/g-only clients

    # --- logs -----------------------------------------------------------------------------------
    log_ignore_patterns: list[str] = []  # regexes; matching log lines are skipped


class WirelessStandardOut(BaseModel):
    standard: WirelessStandard
    updated_at: datetime | None
    is_default: bool


class WorkbookImportOut(BaseModel):
    filename: str
    sha256: str
    profiles: list[TemplateProfile]
    replaced: list[str]
    standard: "WirelessStandardOut"


class R2OImportOut(BaseModel):
    filename: str
    sha256: str
    checklists: list[R2OChecklist]
    replaced: list[str]


class R2OResultOut(BaseModel):
    controller_id: str
    controller_name: str
    checklist: str
    item_id: str
    row: int
    description: str
    commands: list[str]
    stage: str | None
    level: str | None
    status: str  # pass | fail | warn | manual | no_data
    details: list[str]
    output: str
    fix: list[str]


class CheckOut(BaseModel):
    check_id: str
    title: str
    category: str
    status: str  # pass | warn | fail | info | skipped
    evaluated: int
    issues: int
    note: str | None = None


class FindingOut(BaseModel):
    check_id: str
    category: str
    severity: str  # fail | warn | info
    controller_id: str
    controller_name: str
    subject: str | None
    message: str
    expected: str | None = None
    actual: str | None = None


class ControllerAuditOut(BaseModel):
    device_id: str
    name: str
    host: str
    platform: str  # "9800" | "aireos"
    site: str | None
    model: str | None
    software: str | None
    collected_at: datetime | None
    snapshot_id: str | None
    ap_count: int
    client_count: int
    score: int | None
    counts: dict[str, int]
    checks: list[CheckOut]
    missing_commands: list[str]
    template_profile: str | None = None
    r2o_checklist: str | None = None
    r2o_counts: dict[str, int] = {}


class GroupAuditOut(BaseModel):
    controller_id: str
    controller_name: str
    kind: str  # "AP group" | "Site tag" | "Policy tag" | "RF tag"
    name: str
    description: str | None = None
    ap_count: int = 0
    models: str | None = None
    wlans: str | None = None
    profiles: str | None = None  # RF / AP / flex profiles it points at
    wncd: int | None = None
    issues: int = 0


class WncdOut(BaseModel):
    controller_id: str
    controller_name: str
    instance: int
    site_tags: list[str]
    aps: int
    cpu_5min: int | None


class ApAuditOut(BaseModel):
    controller_id: str
    controller_name: str
    name: str
    model: str | None
    mac: str | None
    ip: str | None
    serial: str | None
    software: str | None
    state: str | None
    country: str | None
    clients: int | None
    ap_group: str | None = None
    site_tag: str | None = None
    policy_tag: str | None = None
    rf_tag: str | None = None
    primary_controller: str | None = None
    uplink: str | None = None
    radios: str | None = None
    fqdn: str | None = None
    dns_ips: list[str] = []
    ptr: str | None = None
    name_ok: bool | None = None
    subnet_ok: bool | None = None
    dns_ok: bool | None = None
    ptr_ok: bool | None = None
    issues: int = 0


class ClientAuditOut(BaseModel):
    controller_id: str
    controller_name: str
    mac: str
    manufacturer: str | None
    ip: str | None
    username: str | None
    ap_name: str | None
    ssid: str | None
    state: str | None
    protocol: str | None
    rssi: int | None
    snr: int | None
    health: str  # good | fair | poor | unknown
    issues: list[str]


class ClientHealthOut(BaseModel):
    controller_id: str
    controller_name: str
    total: int
    with_rf_detail: int
    good: int
    fair: int
    poor: int
    avg_rssi: float | None
    avg_snr: float | None
    band_24: int
    band_5: int
    band_6: int
    not_run: int
    no_ip: int
    legacy: int


class LogGroupOut(BaseModel):
    controller_id: str
    controller_name: str
    source: str  # "syslog" | "traplog"
    key: str
    severity: int | None
    count: int
    example: str
    notable: bool


class WirelessAuditOut(BaseModel):
    generated_at: datetime
    dns_checked: bool
    summary: dict[str, int]
    controllers: list[ControllerAuditOut]
    findings: list[FindingOut]
    access_points: list[ApAuditOut]
    clients: list[ClientAuditOut]
    client_health: list[ClientHealthOut]
    logs: list[LogGroupOut]
    groups: list[GroupAuditOut] = []
    wncd: list[WncdOut] = []
    r2o: list[R2OResultOut] = []


class AuditCommandCoverageOut(BaseModel):
    device_type: str
    label: str
    device_count: int
    audit_commands: list[str]
    missing: list[str]
    is_custom_profile: bool


WorkbookImportOut.model_rebuild()
