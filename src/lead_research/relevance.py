"""Is a crawled company site actually real estate? Conservative homepage classifier.

`classify_site(company, homepage_text)` returns ``(label, score, signals)`` where label is
``'real_estate'``, ``'off_topic'`` or ``'unknown'``. Only ``'off_topic'`` changes behaviour
(the crawler stops after the homepage), so it is returned only when off-topic signals clearly
dominate AND no strong real-estate signal is present. Mortgage lenders, law firms, title and
insurance agencies stay ``'unknown'``: many of them are real-estate adjacent decision makers.

Domains that came from public license records (TREC, NY DOS, ...) are licensed real estate by
definition and must never be classified; see ``LICENSED_SOURCES`` / ``is_licensed_source``.
"""
from __future__ import annotations

import re
from typing import Any

# Sources whose domains are licensed real-estate firms (never filtered).
LICENSED_SOURCES = frozenset({
    'records', 'tx-trec', 'ny-dos', 'co-dora', 'sec-form-d', 'fl-dbpr-file', 'ct-dls', 'de-dpr',
    'history',  # previously delivered/imported leads were already accepted
})


def is_licensed_source(source: str | None) -> bool:
    if not source:
        return False
    source = str(source).strip().lower()
    return source in LICENSED_SOURCES or source.startswith(('records:', 'license:'))


# (pattern, weight). Strong positives (weight >= 3) veto an off_topic verdict on their own.
_POSITIVE = [
    (r'\breal[\s-]?estate\b', 3), (r'\brealty\b', 3), (r'\brealtors?\b', 3), (r'\bbrokerage\b', 3),
    (r'\bhomes? for sale\b', 3), (r'\bmls\b', 3), (r'\bidx\b', 2), (r'\bproperty management\b', 3),
    (r'\bproperty managers?\b', 2), (r'\bcommercial real estate\b', 3), (r'\bcre\b', 1),
    (r'\bmulti-?family\b', 3), (r'\breits?\b', 3), (r'\breal estate investment', 3),
    (r'\bdesignated broker\b', 3), (r'\bmanaging broker\b', 3), (r'\bbroker of record\b', 3),
    (r'\blicensed (?:real estate )?brokers?\b', 3), (r'\b(?:trec|dre|license) ?#', 2),
    (r'\blistings?\b', 2), (r'\bfor lease\b', 2), (r'\bleasing\b', 2), (r'\bapartments?\b', 2),
    (r'\bbuyers? (?:and|&) sellers?\b', 2), (r'\bhome ?buyers?\b', 1), (r'\bsell your home\b', 2),
    (r'\breal estate development\b', 3), (r'\bdevelopers?\b', 1), (r'\bdevelopment\b', 1),
    (r'\bacquisitions?\b', 1), (r'\bportfolio\b', 1), (r'\bland\b', 1), (r'\bacres?\b', 1),
    (r'\binvestment properties\b', 3), (r'\bindustrial (?:properties|space|real estate)\b', 3),
    (r'\boffice space\b', 2), (r'\bretail space\b', 2), (r'\btenants?\b', 1), (r'\blandlords?\b', 2),
    (r'\bcondos?\b', 2), (r'\bkeller williams\b|\bcoldwell banker\b|\bre/?max\b|\bcentury 21\b|\bsotheby', 3),
    (r'\bexp realty\b|\bcompass\b|\bberkshire hathaway homeservices\b', 2),
]
# Off-topic trades and businesses. Ambiguous adjacents (mortgage, title, law, insurance) are
# deliberately absent or weak so they land in 'unknown'.
_NEGATIVE = [
    (r'\bsign(?:s|age)? (?:company|shop|maker)\b', 3), (r'\bsignage\b', 3), (r'\bcustom signs?\b', 3),
    (r'\bbanners?\b', 1), (r'\bvehicle wraps?\b', 3),
    (r'\bhome warrant(?:y|ies)\b', 3), (r'\bwarranty plans?\b', 2),
    (r'\broofing\b', 3), (r'\broofers?\b', 3), (r'\bplumbing\b', 3), (r'\bplumbers?\b', 3),
    (r'\bhvac\b', 3), (r'\bair conditioning\b', 2), (r'\bfurnace\b', 2), (r'\belectricians?\b', 3),
    (r'\bpest control\b', 3), (r'\blandscaping\b', 2), (r'\blawn care\b', 3), (r'\bpool service\b', 3),
    (r'\bcleaning services?\b', 3), (r'\bjanitorial\b', 3), (r'\bmaid service\b', 3),
    (r'\bmovers\b', 3), (r'\bmoving (?:company|services)\b', 3), (r'\bstorage units?\b', 2),
    (r'\bmarketing agency\b', 3), (r'\bdigital marketing\b', 2), (r'\bseo services\b', 3), (r'\bweb design\b', 2),
    (r'\bsaas\b', 2), (r'\bsoftware (?:company|platform)\b', 2), (r'\bfree trial\b', 2), (r'\bpricing plans?\b', 1),
    (r'\bphotography\b', 2), (r'\bphotographers?\b', 2), (r'\bvideography\b', 2),
    (r'\brestaurant\b', 3), (r'\bmenu\b', 1), (r'\bcatering\b', 3), (r'\bdental\b', 3), (r'\bdentist\b', 3),
    (r'\bchiropract', 3), (r'\bauto (?:repair|body)\b', 3), (r'\bcar dealership\b', 3),
    (r'\bstaffing agency\b', 3), (r'\bsalon\b', 3), (r'\bspa\b', 1), (r'\bfitness\b', 2),
    (r'\bhome inspections?\b', 1), (r'\bappraisals?\b', 0),
]
# Vendors that *serve* real-estate agents ("real estate signs", "realtor photography") mention
# real estate constantly; those phrases are removed before positive scoring and count against.
_VENDOR = re.compile(r'\b(?:real[\s-]?estate|realtors?|realty|for sale|open house|agent)\s+'
                     r'(?:signs?|sign posts?|riders?|photography|photographers?|videos?|drone|marketing|'
                     r'warrant(?:y|ies)|software|crm|websites?|web design|leads?|lead generation|coaching|'
                     r'schools?|courses?|classes|exam prep|continuing education|staging)\b', re.I)
_POS = [(re.compile(p, re.I), w) for p, w in _POSITIVE]
_NEG = [(re.compile(p, re.I), w) for p, w in _NEGATIVE]
_MAX_TEXT = 60_000


def _score(patterns, text):
    hits = []
    total = 0
    for rx, weight in patterns:
        if weight <= 0:
            continue
        found = len(rx.findall(text))
        if found:
            hits.append((rx.pattern, weight, found))
            total += weight * min(found, 3)
    return total, hits


def classify_site(company: dict[str, Any] | None, homepage_text: str | None) -> tuple[str, int, dict[str, Any]]:
    """Classify a site from its extracted company dict and homepage HTML/text.

    Returns (label, score, signals). score = positive - negative (weighted, capped per term).
    """
    company = company or {}
    parts = [str(company.get(k) or '') for k in ('name', 'title', 'description', 'domain')]
    text = ' '.join(parts) + ' ' + re.sub(r'<[^>]+>', ' ', (homepage_text or '')[:_MAX_TEXT])
    text = re.sub(r'\s+', ' ', text)
    domain = str(company.get('domain') or '').lower()
    vendor = _VENDOR.findall(text)
    pos, pos_hits = _score(_POS, _VENDOR.sub(' ', text))
    neg, neg_hits = _score(_NEG, text)
    if vendor:
        neg += 3 * min(len(vendor), 3)
        neg_hits.append(('vendor_to_agents', 3, len(vendor)))
    if re.search(r'realty|realestate|homes|properties|property|apartments|brokers?', domain):
        pos += 3
        pos_hits.append(('domain', 3, 1))
    strong = any(w >= 3 for _, w, _ in pos_hits)
    signals = {'positive': [h[0] for h in pos_hits][:10], 'negative': [h[0] for h in neg_hits][:10],
               'positive_score': pos, 'negative_score': neg}
    score = pos - neg
    if strong and pos >= neg:
        return 'real_estate', score, signals
    if pos >= 6 and pos > neg:
        return 'real_estate', score, signals
    if not strong and neg >= 6 and neg >= 2 * max(pos, 1):
        return 'off_topic', score, signals
    return 'unknown', score, signals
