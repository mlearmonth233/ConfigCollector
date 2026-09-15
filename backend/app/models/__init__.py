from app.models.command_profile import CommandProfile
from app.models.credential import Credential
from app.models.custom_device_type import CustomDeviceType
from app.models.device import Device
from app.models.dns_check import DnsCheckJob, DnsCheckJobItem
from app.models.firmware import FirmwareImage, FirmwareUpgradeJob, FirmwareUpgradeJobItem
from app.models.hostname_rule import HostnameRule
from app.models.job import CollectionJob, CollectionJobItem
from app.models.organization import Organization
from app.models.schedule import Schedule
from app.models.snapshot import ConfigSnapshot
from app.models.snmp import SnmpJob, SnmpJobItem, SnmpProfile
from app.models.snmp_monitor import SnmpAlert, SnmpMonitorConfig, SnmpMonitorState
from app.models.user import User

# Every mapper must be imported before any of them is used - Organization's
# relationships name FirmwareImage/DnsCheckJob/etc. as strings that are
# only resolvable once those classes exist. Importing this package is the
# one-stop way to guarantee that (the API/task modules do it transitively;
# a standalone script should `import app.models`).
__all__ = [
    "Organization",
    "User",
    "Credential",
    "Device",
    "CollectionJob",
    "CollectionJobItem",
    "ConfigSnapshot",
    "CommandProfile",
    "Schedule",
    "FirmwareImage",
    "FirmwareUpgradeJob",
    "FirmwareUpgradeJobItem",
    "DnsCheckJob",
    "DnsCheckJobItem",
    "CustomDeviceType",
    "SnmpProfile",
    "SnmpJob",
    "SnmpJobItem",
    "HostnameRule",
    "SnmpMonitorConfig",
    "SnmpMonitorState",
    "SnmpAlert",
]
