"""Import send results from outreach tools and feed them back into lead history.

Supported exports (auto-detected from the CSV header, or forced with ``--tool``):

* Instantly   lead export: ``Email``/``email``, ``Status``/``Lead Status`` (Bounced, Unsubscribed,
  Replied, Interested, Meeting Booked, ...), optional ``Opens``/``Clicks``/``Replies`` counts
* Smartlead   campaign lead export: ``email``, ``status``, ``lead_category`` (Interested, Meeting Request,
  Not Interested, Do Not Contact, Wrong Person, ...), ``is_bounced``, ``is_unsubscribed``,
  ``open_count``, ``click_count``, ``reply_count``, ``sent_count``
* Lemlist     campaign export: ``email``, ``lastState``/``status``, ``emailsBounced``, ``emailsReplied``,
  ``emailsOpened``, ``emailsClicked``, ``emailsUnsubscribed``, ``emailsSent``, ``interested``
* Apollo      sequence export: ``Email``, ``Email Bounced``, ``Replied``, ``Email Unsubscribed``,
  ``Email Open``, ``Email Click``, ``Email Sent``, ``Demoed``, ``Contact Stage``
* HubSpot     contact export: ``Email``, ``Email hard bounced``/``Hard bounce reason``,
  ``Unsubscribed from all email``, ``Marketing emails replied``/``opened``/``clicked``/``delivered``,
  ``Lead Status``
* generic     any file with an email column and one of ``status``/``event``/``outcome``/``result``

Every row becomes at most one *primary* outcome (bounce wins over everything, then
complaint/unsubscribe/do-not-contact, then meeting > interested > replied > negatives > engaged
> delivered). Bounces suppress the email forever and, when the delivered email was a
pattern guess, mark that domain's pattern as distrusted so the next run stops guessing there.
Unsubscribes, complaints, do-not-contact, wrong person and customers suppress the whole person.

Re-importing the same file is harmless: each row is hashed and counted once.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from . import quality

TOOLS = ('auto', 'instantly', 'smartlead', 'lemlist', 'apollo', 'hubspot', 'generic')

# Header signatures (normalized with quality._key) used by auto-detection, most specific first
SIGNATURES = (
    ('hubspot', {'email_hard_bounced', 'hard_bounce_reason', 'unsubscribed_from_all_email', 'marketing_emails_opened',
                 'marketing_emails_delivered', 'marketing_emails_replied', 'record_id'}),
    ('smartlead', {'lead_category', 'is_bounced', 'is_unsubscribed', 'reply_count', 'open_count', 'click_count', 'sent_count'}),
    ('lemlist', {'emailsbounced', 'emailsreplied', 'emailsopened', 'emailsclicked', 'emailsunsubscribed', 'emailssent', 'laststate'}),
    ('apollo', {'email_bounced', 'email_unsubscribed', 'email_open', 'email_click', 'email_sent', 'demoed', 'contact_stage',
                'replied'}),
    ('instantly', {'lead_status', 'email_opened', 'email_clicked', 'email_replied', 'opens', 'clicks', 'replies',
                   'interest_status', 'esp_code'}),
)
EMAIL_COLUMNS = ('email', 'email_address', 'lead_email', 'contact_email', 'to', 'recipient', 'e_mail', 'work_email')
STATUS_COLUMNS = ('outcome', 'status', 'event', 'event_type', 'result', 'lead_status', 'laststate', 'last_state',
                  'lead_category', 'category', 'interest_status', 'contact_stage', 'disposition', 'type')

# Primary outcome priority (lower wins). 'engaged' = opened/clicked, 'delivered' = sent/delivered.
PRIORITY = ('bounced', 'complaint', 'unsubscribed', 'do_not_contact', 'wrong_person', 'customer', 'meeting',
            'interested', 'replied', 'not_interested', 'engaged', 'delivered')
PERSON_SUPPRESS = {'complaint', 'unsubscribed', 'do_not_contact', 'wrong_person', 'customer'}

# Free-text status/event words -> outcome (matched on the folded, underscore-joined value)
STATUS_WORDS = (
    (r'(hard_?)?bounc|invalid|undeliverable|rejected|blocked_by_recipient|mailbox_(not_found|full)|address_not_found', 'bounced'),
    (r'spam|complain|abuse|marked_as_spam', 'complaint'),
    (r'unsub|opt_?out|opted_out', 'unsubscribed'),
    (r'do_?not_?contact|dnc|blocklist|blacklist|do_not_email', 'do_not_contact'),
    (r'wrong_?(person|contact)|no_longer|left_company|not_the_right', 'wrong_person'),
    (r'customer|closed_won|(^|_)won($|_)', 'customer'),
    (r'meeting|booked|demo(ed)?|call_scheduled|appointment', 'meeting'),
    (r'interested|positive|(^|_)hot($|_)|(^|_)qualified', 'interested'),
    (r'repl|responded|answered|conversation', 'replied'),
    (r'not_interested|negative|unqualified|bad_timing|not_now|lost', 'not_interested'),
    (r'click|opened', 'engaged'),
    (r'sent|deliver|completed|contacted|attempted|finished|in_?progress|active|started', 'delivered'),
)
# Boolean / count columns -> outcome when truthy (> 0)
FLAG_COLUMNS = {
    'is_bounced': 'bounced', 'email_bounced': 'bounced', 'bounced': 'bounced', 'emailsbounced': 'bounced',
    'email_hard_bounced': 'bounced', 'hard_bounced': 'bounced', 'hard_bounce_reason': 'bounced', 'bounce': 'bounced',
    'is_unsubscribed': 'unsubscribed', 'email_unsubscribed': 'unsubscribed', 'unsubscribed': 'unsubscribed',
    'emailsunsubscribed': 'unsubscribed', 'unsubscribed_from_all_email': 'unsubscribed', 'opted_out': 'unsubscribed',
    'spam_complaint': 'complaint', 'marked_as_spam': 'complaint',
    'demoed': 'meeting', 'meeting_booked': 'meeting', 'interested': 'interested',
    'replied': 'replied', 'reply_count': 'replied', 'replies': 'replied', 'emailsreplied': 'replied', 'email_replied': 'replied',
    'marketing_emails_replied': 'replied',
    'open_count': 'engaged', 'opens': 'engaged', 'emailsopened': 'engaged', 'email_open': 'engaged', 'email_opened': 'engaged',
    'marketing_emails_opened': 'engaged', 'click_count': 'engaged', 'clicks': 'engaged', 'emailsclicked': 'engaged',
    'email_click': 'engaged', 'email_clicked': 'engaged', 'marketing_emails_clicked': 'engaged',
    'sent_count': 'delivered', 'emailssent': 'delivered', 'email_sent': 'delivered', 'marketing_emails_delivered': 'delivered',
    'delivered': 'delivered',
}
# Soft bounces are not permanent: never suppress on them
SOFT_BOUNCE = re.compile(r'soft_?bounc|temporar|greylist|mailbox_full|out_of_office|auto_?repl')


def detect_tool(headers):
    keys = {quality._key(h) for h in headers if h}
    best, hits = 'generic', 0
    for tool, signature in SIGNATURES:
        n = len(keys & signature)
        if n > hits and n >= 2:
            best, hits = tool, n
    return best


def _truthy(value):
    text = str(value if value is not None else '').strip().lower()
    if not text or text in ('0', 'false', 'no', 'n', 'none', 'null', '-', 'nan', '0.0'):
        return False
    if text in ('true', 'yes', 'y', 'x', '1'):
        return True
    try:
        return float(text.replace(',', '')) > 0
    except ValueError:
        return True  # a non-empty reason/date (e.g. "Hard bounce reason", "Demoed: 2026-10-01")


def _status_outcome(value):
    text = re.sub(r'[^a-z0-9]+', '_', quality.fold(str(value or ''))).strip('_')
    if not text:
        return None
    if SOFT_BOUNCE.search(text):
        return 'delivered'
    # "not interested" must win over "interested"
    if re.search(r'not_interested|uninterested|unqualified', text):
        return 'not_interested'
    for pattern, outcome in STATUS_WORDS:
        if re.search(pattern, text):
            return outcome
    return None


INSTANTLY_STATUS_CODES = {'-1': 'bounced', '-2': 'unsubscribed', '3': 'delivered', '1': 'delivered', '2': 'delivered'}


def row_outcome(row, tool='generic'):
    """(email, primary outcome or None, all outcomes found) for one export row."""
    keyed = {quality._key(k): v for k, v in row.items() if k}
    if tool == 'instantly':
        code = str(keyed.get('status') or '').strip()
        if code in INSTANTLY_STATUS_CODES:
            keyed['status'] = INSTANTLY_STATUS_CODES[code]
    email = ''
    for col in EMAIL_COLUMNS:
        email = quality.normalize_email_address(keyed.get(col) or '')
        if email:
            break
    found = set()
    for col in STATUS_COLUMNS:
        outcome = _status_outcome(keyed.get(col))
        if outcome:
            found.add(outcome)
    for col, outcome in FLAG_COLUMNS.items():
        if col in keyed and _truthy(keyed[col]):
            found.add(outcome)
    primary = next((o for o in PRIORITY if o in found), None)
    return email, primary, found


def _row_hash(tool, row):
    canonical = json.dumps({quality._key(k): str(v).strip() for k, v in sorted(row.items()) if k}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f'{tool}|{canonical}'.encode('utf-8')).hexdigest()


def import_results(history, rows, tool='auto', clock=time.time):
    """Apply outreach results to ``history`` (a daily.History). Returns a summary dict."""
    if tool not in TOOLS:
        raise ValueError('unknown_results_tool')
    rows = [r for r in rows if isinstance(r, dict)]
    headers = list(dict.fromkeys(k for r in rows for k in r.keys() if k))
    detected = detect_tool(headers) if tool == 'auto' else tool
    db = history.db
    summary = {'tool': detected, 'rows_read': len(rows), 'already_imported': 0, 'no_email': 0, 'unrecognized': 0,
               'matched_delivered': 0, 'outcomes': {}, 'newly_suppressed': 0, 'patterns_distrusted': []}
    now = clock()
    with db:
        for row in rows:
            digest = _row_hash(detected, row)
            if db.execute('SELECT 1 FROM result_rows WHERE hash=?', (digest,)).fetchone():
                summary['already_imported'] += 1
                continue
            email, outcome, _ = row_outcome(row, detected)
            db.execute('INSERT INTO result_rows VALUES(?,?,?,?,?)', (digest, detected, email or None, outcome, now))
            if not email:
                summary['no_email'] += 1
                continue
            if not outcome:
                summary['unrecognized'] += 1
                continue
            origin = db.execute("SELECT batch, email_source, source, full_name, company_website, email, linkedin_url FROM delivered "
                                "WHERE key=? LIMIT 1", ('em:' + email,)).fetchone()
            if origin:
                summary['matched_delivered'] += 1
                person = {'full_name': origin['full_name'], 'company_website': origin['company_website'],
                          'email': origin['email'] or email, 'linkedin_url': origin['linkedin_url']}
                keys = history.keys_for(person)
            else:
                keys = ['em:' + email]
            # The email key always carries the outcome (source_quality counts em: keys)
            keys = ['em:' + email] + [k for k in keys if k != 'em:' + email]
            for key in keys:
                db.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?,?,?,?,?)',
                           (key, outcome, origin['source'] if origin else None, origin['email_source'] if origin else None,
                            origin['batch'] if origin else None, now))
            block = ['em:' + email] if outcome == 'bounced' else keys if outcome in PERSON_SUPPRESS else []
            for key in block:
                summary['newly_suppressed'] += db.execute('INSERT OR IGNORE INTO suppressed VALUES(?,?,?)', (key, outcome, now)).rowcount
            if outcome == 'bounced' and origin and origin['email_source'] == 'pattern_verified':
                domain = email.split('@', 1)[1]
                if history.distrust_pattern(domain, f'pattern_verified email hard-bounced ({detected} import)', clock=clock):
                    summary['patterns_distrusted'].append(domain)
            summary['outcomes'][outcome] = summary['outcomes'].get(outcome, 0) + 1
    return summary
