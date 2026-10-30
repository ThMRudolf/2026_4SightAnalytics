"""Com2WA – reusable process-monitoring alerts delivered via WhatsApp."""
from .channels import Channel, ConsoleChannel, MetaCloudChannel
from .config import build_manager, load_config
from .manager import AlertManager
from .models import Alert, AlertKind, AlertState, DeliveryResult, Recipient, RenderedMessage, Severity
from .rules import EqualsRule, NonZeroRule, Rule, ThresholdRule, rule_from_dict

__version__ = "0.1.0"

__all__ = [
    "AlertManager", "build_manager", "load_config",
    "Rule", "ThresholdRule", "NonZeroRule", "EqualsRule", "rule_from_dict",
    "Alert", "AlertKind", "AlertState", "Recipient", "RenderedMessage", "DeliveryResult", "Severity",
    "Channel", "ConsoleChannel", "MetaCloudChannel",
]
