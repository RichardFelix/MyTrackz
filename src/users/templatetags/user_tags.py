from django import template

register = template.Library()


@register.filter
def get_attr(obj, attr):
    """Get attribute from object dynamically."""
    return getattr(obj, attr, None)
