"""Initial platform definitions. Publishing integrations are intentionally out of scope."""

from lorchestrateur.platforms.contracts import ContentFieldRule, SchemaPlatform
from lorchestrateur.platforms.registry import PlatformRegistry


BLOG = SchemaPlatform(
    key="blog",
    display_name="Blog",
    adaptation_guidance="Preserve the master argument while expanding evidence and structure.",
    field_rules={
        "title": ContentFieldRule(max_length=120),
        "body": ContentFieldRule(min_length=1),
    },
)

X = SchemaPlatform(
    key="x",
    display_name="X",
    adaptation_guidance="Express one clear idea concisely and retain the intended voice.",
    # V1 targets standard posts. Entitlement-specific longer posts are a separate capability.
    field_rules={"text": ContentFieldRule(max_length=280)},
)

INSTAGRAM = SchemaPlatform(
    key="instagram",
    display_name="Instagram",
    adaptation_guidance="Use a strong opening and a readable caption suited to visual context.",
    field_rules={"caption": ContentFieldRule()},
)

FACEBOOK = SchemaPlatform(
    key="facebook",
    display_name="Facebook",
    adaptation_guidance="Adapt the idea for conversational reading without changing its claims.",
    field_rules={"text": ContentFieldRule()},
)


def create_default_registry() -> PlatformRegistry:
    return PlatformRegistry((BLOG, X, INSTAGRAM, FACEBOOK))
