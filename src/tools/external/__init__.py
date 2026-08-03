"""Tools that open external links referenced in Telegram messages.

The Telegram connector only sees attachments of the messages themselves, not the
contents of external storage a client links to. Modules here let the server open
such links server-side and inline their content (images) into the tool result,
the same way `get_media_content` inlines message attachments.
"""

from src.tools.external.yandex_disk import get_yandex_disk_content_impl

__all__ = ["get_yandex_disk_content_impl"]
