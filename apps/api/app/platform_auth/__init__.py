"""Verified, workspace/member-scoped browser account sessions."""

from app.platform_auth.capabilities import CAPABILITIES, PlatformCapability, capability_for
from app.platform_auth.service import PlatformAccount, PlatformAuthService

__all__ = ["CAPABILITIES", "PlatformCapability", "capability_for",
           "PlatformAccount", "PlatformAuthService"]
