"""AIPC Plugin and Super-App Extension Subsystem."""

from aipc.plugins.errors import (
    AIPCError,
    PluginContractError,
    PluginError,
    PluginRegistrationError,
)
from aipc.plugins.models import (
    PluginVerificationReport,
    ProviderCapabilityQualification,
    ProviderPluginManifest,
    ProviderPluginPackage,
)
from aipc.plugins.registry import PluginRegistry
from aipc.plugins.verifier import verify_provider_plugin

__all__ = [
    "AIPCError",
    "PluginContractError",
    "PluginError",
    "PluginRegistrationError",
    "PluginRegistry",
    "PluginVerificationReport",
    "ProviderCapabilityQualification",
    "ProviderPluginManifest",
    "ProviderPluginPackage",
    "verify_provider_plugin",
]
