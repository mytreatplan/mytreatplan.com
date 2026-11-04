"""`{% region_suggestion_bar %}`: the region suggestion bar, or nothing (see
regions/suggestion.py). It reads the request from the context only."""
from django import template
from django.urls import reverse

from regions.suggestion import suggestion_for

register = template.Library()


@register.inclusion_tag('regions/_suggestion_bar.html', takes_context=True)
def region_suggestion_bar(context):
    request = context.get('request')
    suggestion = suggestion_for(request) if request is not None else None
    if suggestion is None:
        return {'suggestion': None}
    return {'suggestion': suggestion, 'stay_url': reverse('region_stay'),
            'next_path': request.get_full_path()}
