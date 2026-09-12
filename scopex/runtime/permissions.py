from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActionClass(str, Enum):
    READ_ONLY = "READ_ONLY"
    ANALYSIS = "ANALYSIS"
    TEMP_WRITE = "TEMP_WRITE"
    CONFIG_WRITE = "CONFIG_WRITE"
    SERVICE_RESTART = "SERVICE_RESTART"
    DELETE = "DELETE"
    DEVICE_CONTROL = "DEVICE_CONTROL"


class PermissionDecision(str, Enum):
    AUTO = "AUTO"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


_DEFAULTS = {
    ActionClass.READ_ONLY: PermissionDecision.AUTO,
    ActionClass.ANALYSIS: PermissionDecision.AUTO,
    ActionClass.TEMP_WRITE: PermissionDecision.AUTO,
    ActionClass.CONFIG_WRITE: PermissionDecision.CONFIRM,
    ActionClass.SERVICE_RESTART: PermissionDecision.CONFIRM,
    ActionClass.DELETE: PermissionDecision.CONFIRM,
    ActionClass.DEVICE_CONTROL: PermissionDecision.CONFIRM,
}


@dataclass(slots=True)
class PermissionPolicy:
    decisions: dict[ActionClass, PermissionDecision] = field(
        default_factory=lambda: dict(_DEFAULTS)
    )

    def decision_for(self, action: ActionClass) -> PermissionDecision:
        if not isinstance(action, ActionClass):
            action = ActionClass(action)
        return self.decisions.get(action, PermissionDecision.DENY)

    def requires_confirmation(self, action: ActionClass) -> bool:
        return self.decision_for(action) is PermissionDecision.CONFIRM

    def allowed_without_confirmation(self, action: ActionClass) -> bool:
        return self.decision_for(action) is PermissionDecision.AUTO
