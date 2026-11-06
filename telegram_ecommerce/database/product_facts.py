"""Extract useful merchant specifications without leaking source payloads or guessing fit."""
import re

SPEC_FIELDS = {'colour', 'color', 'material', 'dimensions', 'capacity', 'weight', 'size',
               'compatibility', 'laptop compatibility', 'page count', 'paper', 'care'}


def product_facts(product):
    custom = (product.details or {}).get('custom') or {}
    specs, highlights, notes = {}, [], []
    for key, value in custom.items():
        if key.casefold() in SPEC_FIELDS and isinstance(value, (str, int, float)):
            specs[key.replace('_', ' ').title()] = str(value)[:500]
    sections = custom.get('product_details') or []
    if not isinstance(sections, list): sections = []
    for section in sections:
        if not isinstance(section, dict): continue
        if section.get('layout') == 'highlights':
            for banner in section.get('banners') or []:
                label = (banner.get('title') or {}).get('text')
                if isinstance(label, str): highlights.append(label[:160])
        if section.get('text') not in {'Specifications', 'Product Details'}: continue
        for feed in (section.get('gpContent') or {}).get('feed') or []:
            for entry in feed.get('texts') or []:
                key, value = entry.get('specHeader'), entry.get('specText')
                if isinstance(key, str) and key.casefold() in SPEC_FIELDS and isinstance(value, str):
                    specs[key] = value[:500]
                value = entry.get('text')
                if isinstance(value, str) and section.get('text') == 'Product Details': notes.append(value[:500])
    # Source snapshots sometimes contradict themselves (e.g. 14-inch highlight,
    # 16-inch sleeve claim). Expose both and never infer guaranteed compatibility.
    size_claims = set()
    for value in highlights + notes:
        if 'laptop' in value.casefold():
            size_claims.update(re.findall(r'(\d+(?:\.\d+)?)\s*(?:inch|[’"″]+)\s*(?:\w+\s+){0,2}laptop', value, re.I))
    warning = ('The source lists conflicting laptop sizes. Confirm the internal compartment size before buying.'
               if len(size_claims) > 1 else
               'Overall dimensions are not internal laptop-compartment measurements. Confirm compatibility with your device.')
    return {'specifications': dict(list(specs.items())[:12]), 'highlights': highlights[:8],
            'features': notes[:12], 'fit_note': warning if any(any(term in value.casefold() for term in ('laptop', 'sleeve', 'macbook')) for value in [product.name, *highlights, *notes]) else ''}
