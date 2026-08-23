"""Built-in governed platform modules. Publishing remains intentionally out of scope."""

from lorchestrateur.platforms.blog import BLOG
from lorchestrateur.platforms.facebook import FACEBOOK
from lorchestrateur.platforms.instagram import INSTAGRAM
from lorchestrateur.platforms.registry import PlatformRegistry
from lorchestrateur.platforms.x import X


def create_default_registry() -> PlatformRegistry:
    return PlatformRegistry((BLOG, X, INSTAGRAM, FACEBOOK))
