"""Gelenktabellen der unterstützten Geräte (aus LeRobot 0.7.0 übernommen und dort geprüft)."""

from .spec import DEVICE_TYPES, DeviceSpec, Joint, JointMode, get_device_spec

__all__ = ["DEVICE_TYPES", "DeviceSpec", "Joint", "JointMode", "get_device_spec"]
