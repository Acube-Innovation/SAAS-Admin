"""
Signals other apps connect to (change requests close when their linked version goes live).

    from apps.messaging_templates.signals import version_went_live

    @receiver(version_went_live)
    def close_requests(sender, version, actor, **kwargs): ...
"""

from django.dispatch import Signal

# Sent inside make_live's transaction, after the version became live. Receivers get `version`
# (a WhatsAppTemplateVersion) and `actor`.
version_went_live = Signal()
