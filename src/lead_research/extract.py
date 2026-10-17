"""Extract companies, people, titles, work emails and LinkedIn links from HTML.

Everything here is a pure function over HTML text (stdlib only), except
`crawl_domains`, which drives a duck-typed fetcher (`fetcher.get(url)` returning
an object with `error`, `text`, `final_url`). Every person record carries the
page it came from (`evidence_url`) plus a short visible-text `excerpt`, so the
downstream quality engine can show where each name/title/email was published.

Person record keys: full_name, title, email, linkedin_url, evidence_url,
excerpt, method. `method` is one of METHODS (strongest first); 'mailto' means an
email was published inside the person's card (mailto link, Cloudflare-protected
link, or plain/obfuscated text).
"""
from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Iterator

METHODS = ('jsonld', 'microdata', 'mailto', 'linkedin_link', 'name_title_block')
_METHOD_RANK = {m: i for i, m in enumerate(METHODS)}
MAX_HTML_CHARS = 3_000_000
_MAX_DEPTH = 400
_CARD_WINDOW = 8
EXCERPT_MAX = 240

# --------------------------------------------------------------------------- vocabularies

ROLE_LOCAL_PARTS = frozenset({
    'info', 'hello', 'hi', 'contact', 'contactus', 'office', 'admin', 'administrator', 'sales',
    'team', 'support', 'help', 'marketing', 'leasing', 'rentals', 'rental', 'broker', 'brokers',
    'manager', 'management', 'inquiries', 'inquiry', 'enquiries', 'frontdesk', 'reception',
    'careers', 'career', 'jobs', 'job', 'hr', 'recruiting', 'billing', 'accounting', 'accounts',
    'ap', 'ar', 'invoices', 'finance', 'media', 'press', 'pr', 'news', 'newsletter', 'noreply',
    'no-reply', 'donotreply', 'do-not-reply', 'webmaster', 'web', 'website', 'postmaster',
    'hostmaster', 'abuse', 'privacy', 'legal', 'compliance', 'service', 'services',
    'customerservice', 'listings', 'listing', 'properties', 'property', 'agents', 'agent',
    'realestate', 'homes', 'mail', 'email', 'general', 'operations', 'ops', 'maintenance',
    'repairs', 'residents', 'tenants', 'owners', 'events', 'social', 'feedback', 'questions',
    'appointments', 'booking', 'bookings', 'reservations', 'all', 'staff', 'mortgage',
    'closing', 'closings', 'transactions', 'escrow', 'title', 'commercial', 'residential',
})
PERSONAL_PROVIDERS = frozenset({
    'gmail.com', 'googlemail.com', 'yahoo.com', 'ymail.com', 'rocketmail.com', 'hotmail.com',
    'outlook.com', 'live.com', 'msn.com', 'aol.com', 'icloud.com', 'me.com', 'mac.com',
    'comcast.net', 'att.net', 'sbcglobal.net', 'verizon.net', 'bellsouth.net', 'cox.net',
    'charter.net', 'earthlink.net', 'optonline.net', 'protonmail.com', 'proton.me', 'pm.me',
    'gmx.com', 'gmx.net', 'mail.com', 'zoho.com', 'yandex.com', 'aim.com', 'frontier.com',
    'windstream.net', 'roadrunner.com', 'twc.com', 'juno.com', 'netzero.net', 'hey.com',
})
_JUNK_EMAIL_DOMAINS = frozenset({
    'example.com', 'example.org', 'example.net', 'domain.com', 'yourdomain.com', 'email.com',
    'sentry.io', 'wixpress.com', 'sentry-next.wixpress.com', 'test.com', 'yoursite.com',
    'company.com', 'mysite.com', 'website.com',
})
_JUNK_TLDS = frozenset({'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'css', 'js', 'ico', 'bmp', 'tiff', 'avif'})

_PARTICLES = frozenset({'de', 'da', 'del', 'della', 'der', 'di', 'du', 'la', 'le', 'van', 'von',
                        'den', 'dos', 'das', 'ten', 'ter', 'bin', 'al', 'el', 'y', 'st.'})
_HONORIFICS = frozenset({'dr', 'dr.', 'mr', 'mr.', 'mrs', 'mrs.', 'ms', 'ms.', 'miss', 'mx', 'mx.',
                         'prof', 'prof.'})
_SUFFIXES = frozenset({'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv'})
_CREDENTIAL_RE = re.compile(
    r"^(?:(?:[A-Z]\.){1,4}|[A-Z]{2,8}\.?|Realtor|Esq\.?|Ph\.?D\.?|Jr\.?|Sr\.?|Broker|Associate)$")
# Words that make a short line "not a person name". Kept to clear non-surnames.
_NON_NAME_WORDS = frozenset("""
our us we you your my me the a an and or of for to in on at by with from as is are be this that
team teams contact about read more view profile profiles commercial residential properties property
real estate realty realtor realtors home homes house houses sale sales sell selling buy buying buyer
buyers seller sellers rent rental rentals lease leasing management manager managers services service
meet agent agents broker brokers brokerage office offices company group partners partner llc inc
corp co ltd learn call email phone search listing listings new featured privacy policy terms
rights reserved welcome client clients testimonials testimonial market report reports news blog
careers career join find get started schedule request free why who what where when how investment
investments advisory apartments apartment today menu close open back next previous page login sign
register submit send message years experience award awards winning top best luxury downtown
county city springs heights village valley district neighborhood neighborhoods community
communities land lots acres office industrial retail multifamily development developments
capital ventures holdings associates association solutions consulting construction homeowners
chief executive officer president founder founders cofounder co-founder owner owners principal
principals director directors vice operating financial marketing operations technology officer
associate administrator administrative assistant coordinator specialist consultant advisor
leadership staff people executives bios directory overview mission vision values history story
locations location map directions hours open click here more less show hide all see download
subscribe follow share like facebook twitter instagram linkedin youtube pinterest tiktok
florida texas california arizona colorado nevada oregon utah ohio michigan illinois massachusetts
connecticut maryland tennessee kentucky alabama louisiana oklahoma kansas nebraska iowa minnesota
wisconsin missouri arkansas mississippi idaho montana wyoming alaska hawaii maine vermont
hampshire jersey york mexico dakota island boston chicago miami houston seattle denver phoenix
atlanta orlando tampa nashville scottsdale manhattan francisco diego angeles vegas san los las
fort saint beach usa america american united states north south east northern southern
eastern western central greater metro area region regional national global international
mortgage loan loans finance financing insurance title escrow closing appraisal inspection
portfolio gallery video videos photos tour tours virtual open-house calculator guide guides
resources faq faqs support help account dashboard cart checkout shop store
""".split())
_NAV_PREFIXES = ('our ', 'meet ', 'find ', 'view ', 'contact ', 'see ', 'all ', 'search ', 'become ',
                 'join ', 'learn ', 'call ', 'email ', 'browse ', 'about ', 'work with', 'hire ')

ROLE_RE = re.compile(
    r"(?<![A-Za-z])(?:ceo|cfo|coo|cto|cmo|cio|cro|chief|co-?founder|founder|owner|president|"
    r"principal|partner|managing broker|broker\s*/\s*owner|broker-owner|designated broker|"
    r"managing director|managing member|chairman|chairwoman|chairperson|chair|vice president|"
    r"svp|evp|avp|vp|director|realtor|agent|broker|associate|manager|head of|officer|"
    r"salesperson|team lead(?:er)?|executive|advisor|consultant|specialist|coordinator|"
    r"administrator|assistant|controller|treasurer|secretary|counsel|attorney|analyst|"
    r"property manager|leasing)(?![A-Za-z])", re.I)
DECISION_RE = re.compile(
    r"(?<![A-Za-z])(?:ceo|cfo|coo|cto|cmo|chief|co-?founder|founder|owner|president|principal|"
    r"partner|managing broker|broker\s*/\s*owner|broker-owner|designated broker|managing director|"
    r"managing member|chairman|chairwoman|vice president|vp|director|head of)(?![A-Za-z])", re.I)

EMAIL_RE = re.compile(
    r"(?<![\w.%+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}"
    r"[A-Za-z0-9])?\.)+[A-Za-z]{2,24})(?![\w-])")
_AT = r"\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|<\s*at\s*>)\s*"
_DOT = r"\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|<\s*dot\s*>)\s*"
_OBFUSCATED_RE = re.compile(
    rf"([A-Za-z0-9._%+-]+){_AT}([A-Za-z0-9-]+(?:(?:{_DOT}|\.)[A-Za-z0-9-]+)+)", re.I)
_SPACED_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)\s+(?:AT|at)\s+([A-Za-z0-9-]+(?:\s+(?:DOT|dot)\s+[A-Za-z0-9-]+)+)\b")
_DOT_TOKEN_RE = re.compile(_DOT, re.I)
PHONE_RE = re.compile(
    r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\(([2-9]\d{2})\)\s?|([2-9]\d{2})[\s.-])([2-9]\d{2})[\s.-](\d{4})(?![\d-])")
_LINKEDIN_PERSON_RE = re.compile(r"linkedin\.com/(?:in|pub)/([^/?#\s\"'<>]+)", re.I)
_LINKEDIN_COMPANY_RE = re.compile(r"linkedin\.com/(?:company|school)/([^/?#\s\"'<>]+)", re.I)

_BLOCK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'div', 'dl', 'dt', 'figcaption',
    'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'li', 'main',
    'nav', 'ol', 'p', 'section', 'table', 'tbody', 'thead', 'td', 'th', 'tr', 'ul', 'button',
    'label', 'caption', 'summary', 'details', 'center', 'option', 'img',
})
_SKIP_TAGS = frozenset({'script', 'style', 'noscript', 'template', 'svg', 'head', 'select',
                        'iframe', 'object', 'textarea', 'math', 'canvas'})
_VOID_TAGS = frozenset({'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta',
                        'param', 'source', 'track', 'wbr'})
_BLOCKISH_CLASS_RE = re.compile(r"name|title|position|role|job|designation|email|phone", re.I)
_ZERO_WIDTH = dict.fromkeys(map(ord, '\u200b\u200c\u200d\u2060\ufeff'), None)


# --------------------------------------------------------------------------- tiny DOM

class _Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent')

    def __init__(self, tag: str, attrs: dict[str, str], parent: '_Node | None'):
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []
        self.parent = parent

    def iter(self) -> Iterator['_Node']:
        """Depth-first element iterator (iterative; safe on deep documents)."""
        stack: list[_Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, _Node))


def decode_cfemail(hex_string: str) -> str | None:
    """Decode Cloudflare email protection hex (first byte is the XOR key)."""
    try:
        data = bytes.fromhex((hex_string or '').strip())
    except ValueError:
        return None
    if len(data) < 2:
        return None
    key = data[0]
    try:
        decoded = bytes(b ^ key for b in data[1:]).decode('utf-8')
    except UnicodeDecodeError:
        return None
    decoded = urllib.parse.unquote(decoded).strip()
    return decoded.lower() if EMAIL_RE.fullmatch(decoded) else None


class _TreeBuilder(HTMLParser):
    """Lenient HTML -> _Node tree. Decodes Cloudflare-protected emails on the fly."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node('#root', {}, None)
        self.stack = [self.root]

    def _make(self, tag: str, attrs: list[tuple[str, str | None]]) -> _Node:
        attr_map = {k.lower(): (v or '') for k, v in attrs}
        if 'data-cfemail' in attr_map:
            decoded = decode_cfemail(attr_map['data-cfemail'])
            if decoded:
                attr_map['data-decoded-email'] = decoded
        href = attr_map.get('href', '')
        if '/cdn-cgi/l/email-protection' in href and '#' in href:
            decoded = decode_cfemail(href.rsplit('#', 1)[1])
            if decoded:
                attr_map['href'] = 'mailto:' + decoded
        node = _Node(tag, attr_map, self.stack[-1])
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = self._make(tag, attrs)
        if tag not in _VOID_TAGS and len(self.stack) < _MAX_DEPTH:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._make(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _parse(html_text: str) -> _Node:
    builder = _TreeBuilder()
    try:
        builder.feed((html_text or '')[:MAX_HTML_CHARS])
        builder.close()
    except Exception:  # html.parser is lenient; keep whatever was built
        pass
    return builder.root


def _clean_text(value: str) -> str:
    value = (value or '').translate(_ZERO_WIDTH).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', value).strip()


def _text_content(node: _Node) -> str:
    """Visible text of an element with spaces at block boundaries."""
    parts: list[str] = []
    stack: list[_Node | str] = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
            continue
        if item.tag in _SKIP_TAGS and item is not node:
            continue
        if item.attrs.get('data-decoded-email'):
            parts.append(' ' + item.attrs['data-decoded-email'] + ' ')
            continue
        if item.tag in _BLOCK_TAGS:
            parts.append(' ')
        stack.append(' ' if item.tag in _BLOCK_TAGS else '')
        stack.extend(reversed(item.children))
    return _clean_text(''.join(parts))


# --------------------------------------------------------------------------- visible lines

class _Line:
    __slots__ = ('text', 'emails', 'linkedins', 'anchors')

    def __init__(self, text: str, emails: list[str], linkedins: list[str],
                 anchors: list[tuple[str, str, str]]):
        self.text = text
        self.emails = emails
        self.linkedins = linkedins
        self.anchors = anchors  # (kind, value, anchor_text) for mailto/linkedin anchors


def _mailto_emails(href: str) -> list[str]:
    target = urllib.parse.unquote(href.split(':', 1)[1] if ':' in href else '')
    target = target.split('?', 1)[0]
    return [m.group(1).lower() for m in EMAIL_RE.finditer(target) if _plausible_email(m.group(1))]


def normalize_linkedin(url: str) -> str | None:
    """Canonical https://www.linkedin.com/in/<slug> (or /company/<slug>) URL."""
    match = _LINKEDIN_PERSON_RE.search(url or '')
    if match:
        return 'https://www.linkedin.com/in/' + urllib.parse.unquote(match.group(1)).strip().rstrip('/')
    match = _LINKEDIN_COMPANY_RE.search(url or '')
    if match:
        return 'https://www.linkedin.com/company/' + urllib.parse.unquote(match.group(1)).strip()
    return None


def _build_lines(root: _Node) -> list[_Line]:
    """Flatten the body into visible text lines carrying mailto/LinkedIn anchors."""
    lines: list[_Line] = []
    buf: list[str] = []
    emails: list[str] = []
    linkedins: list[str] = []
    anchors: list[tuple[str, str, str]] = []

    def flush() -> None:
        nonlocal buf, emails, linkedins, anchors
        text = _clean_text(''.join(buf))
        if text or emails or linkedins:
            found = list(emails)
            for email in find_emails(text):
                if email not in found:
                    found.append(email)
            lines.append(_Line(text, found, list(linkedins), list(anchors)))
        buf, emails, linkedins, anchors = [], [], [], []

    stack: list[tuple[_Node | str, bool]] = [(root, False)]
    while stack:
        item, closing = stack.pop()
        if isinstance(item, str):
            buf.append(item)
            continue
        blockish = item.tag in _BLOCK_TAGS or bool(_BLOCKISH_CLASS_RE.search(item.attrs.get('class', '')))
        if closing:
            if blockish:
                flush()
            continue
        if item.tag in _SKIP_TAGS:
            continue
        if blockish:
            flush()
        decoded = item.attrs.get('data-decoded-email')
        if decoded:
            buf.append(' ' + decoded + ' ')
            stack.append((item, True))
            continue
        if item.tag == 'a':
            href = item.attrs.get('href', '').strip()
            low = href.lower()
            if low.startswith('mailto:'):
                anchor_text = _text_content(item)
                for email in _mailto_emails(href):
                    if email not in emails:
                        emails.append(email)
                    anchors.append(('email', email, anchor_text))
            elif 'linkedin.com/' in low:
                link = normalize_linkedin(href)
                if link and '/in/' in link:
                    linkedins.append(link)
                    anchors.append(('linkedin', link, _text_content(item)))
        stack.append((item, True))
        stack.extend((child, False) for child in reversed(item.children))
    flush()
    return lines


# --------------------------------------------------------------------------- emails

def _plausible_email(email: str) -> bool:
    email = email.lower()
    local, _, domain = email.rpartition('@')
    if not local or not domain or '..' in email or local.startswith('.') or local.endswith('.'):
        return False
    if domain.rsplit('.', 1)[-1] in _JUNK_TLDS or domain in _JUNK_EMAIL_DOMAINS:
        return False
    if any(domain.endswith('.' + junk) for junk in ('wixpress.com', 'sentry.io')):
        return False
    return not re.fullmatch(r'[0-9a-f]{16,}', local)  # tracking hashes


def deobfuscate_emails(text: str) -> str:
    """Rewrite 'name [at] domain [dot] com', 'name(at)domain.com', 'name AT x DOT com' forms."""
    def fix(match: re.Match[str]) -> str:
        domain = _DOT_TOKEN_RE.sub('.', match.group(2))
        domain = re.sub(r'\s+(?:DOT|dot)\s+', '.', domain)
        return f'{match.group(1)}@{domain}'

    text = _OBFUSCATED_RE.sub(fix, text or '')
    return _SPACED_RE.sub(fix, text)


def find_emails(text: str) -> list[str]:
    """All plausible emails in text (after de-obfuscation), lowercased, in order."""
    out: list[str] = []
    for match in EMAIL_RE.finditer(deobfuscate_emails(text)):
        email = match.group(1).lower().rstrip('.')
        if _plausible_email(email) and email not in out:
            out.append(email)
    return out


def _bare_domain(domain: str) -> str:
    domain = (domain or '').strip().lower()
    if '://' in domain:
        domain = urllib.parse.urlsplit(domain).hostname or ''
    domain = domain.split('/', 1)[0].split(':', 1)[0].rstrip('.')
    return domain[4:] if domain.startswith('www.') else domain


def _is_role_local(local: str) -> bool:
    local = local.lower().split('+', 1)[0]
    if local in ROLE_LOCAL_PARTS:
        return True
    tokens = [t for t in re.split(r'[._-]+', local) if t]
    if tokens and re.sub(r'\d+$', '', tokens[0]) in ROLE_LOCAL_PARTS and len(tokens) <= 3:
        return True
    stripped = re.sub(r'\d+$', '', local)
    return stripped in ROLE_LOCAL_PARTS


def classify_email(email: str, domain: str) -> str:
    """'person' | 'role' | 'other_domain' | 'personal_provider' for an address vs. a company domain."""
    email = (email or '').strip().lower()
    local, _, email_domain = email.rpartition('@')
    if email_domain in PERSONAL_PROVIDERS:
        return 'personal_provider'
    company = _bare_domain(domain)
    email_domain = _bare_domain(email_domain)
    if not company or not (email_domain == company or email_domain.endswith('.' + company)
                           or company.endswith('.' + email_domain)):
        return 'other_domain'
    return 'role' if _is_role_local(local) else 'person'


# --------------------------------------------------------------------------- names

def _fold(value: str) -> str:
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', value.lower())


def _is_name_word(token: str) -> bool:
    pieces = re.split(r"[-'’]", token)
    if any(not p or not p.isalpha() for p in pieces):
        return False
    if not token[0].isupper() or not any(c.islower() for c in token):
        return False
    return sum(c.isupper() for c in token) <= 3


def _smart_title(token: str) -> str:
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), token.lower())


def clean_person_name(value: str) -> str | None:
    """Return a normalized display name if `value` looks like a person name, else None."""
    text = _clean_text(value).strip(' :|-–—•·,')
    text = re.sub(r'[®™©]', '', text).strip()
    if not text or len(text) > 60:
        return None
    if ',' in text:
        head, tail = text.split(',', 1)
        tail_tokens = [t for t in re.split(r'[,\s]+', tail.strip()) if t]
        if not tail_tokens or not all(_CREDENTIAL_RE.match(t) for t in tail_tokens):
            return None
        text = head.strip()
    if re.search(r'[\d@/\\:|&+=#<>!?()\[\]{}"“”;]', text):
        return None
    tokens = text.split()
    while tokens and tokens[0].lower() in _HONORIFICS:
        tokens.pop(0)
    while tokens and tokens[-1].lower().rstrip(',') in _SUFFIXES:
        tokens.pop()
    if not 2 <= len(tokens) <= 5:
        return None
    letters = ''.join(c for c in ''.join(tokens) if c.isalpha())
    if letters and letters.isupper():
        tokens = [t if len(t.rstrip('.')) == 1 else _smart_title(t) for t in tokens]
    main = 0
    for i, token in enumerate(tokens):
        low = token.lower()
        if re.fullmatch(r'[A-Z]\.?', token):
            if i == len(tokens) - 1:
                return None
            main += 1
            continue
        if low in _NON_NAME_WORDS or low.strip('.') in _NON_NAME_WORDS:
            if not (low in _PARTICLES and 0 < i < len(tokens) - 1):
                return None
        if low in _PARTICLES and 0 < i < len(tokens) - 1:
            continue
        if not _is_name_word(token):
            return None
        main += 1
    if not 2 <= main <= 4:
        return None
    return ' '.join(tokens)


def looks_like_title(value: str) -> bool:
    """A short line containing a role keyword that is not navigation or a sentence."""
    text = _clean_text(value)
    low = text.lower()
    if not 2 <= len(text) <= 100 or '@' in text or len(text.split()) > 12:
        return False
    if low.startswith(_NAV_PREFIXES) or text.endswith(('?', '!')) or 'http' in low:
        return False
    if re.search(r'\b(?:is|has|was|have|will|can|our|we|you|your)\b', low):
        return False
    return bool(ROLE_RE.search(text)) and clean_person_name(text) is None


def _name_key(full_name: str) -> str:
    parts = _name_parts(full_name)
    return f'{parts[0]} {parts[1]}' if parts else _fold(full_name)


def _name_parts(full_name: str) -> tuple[str, str] | None:
    """(first, last) folded to ascii alnum; None when fewer than two usable parts."""
    text = re.sub(r',.*$', '', _clean_text(full_name))
    tokens = [t for t in text.split() if t.lower() not in _HONORIFICS and t.lower() not in _SUFFIXES]
    tokens = [t for i, t in enumerate(tokens) if i == 0 or t.lower() not in _PARTICLES]
    folded = [_fold(t) for t in tokens]
    folded = [t for t in folded if t]
    if len(folded) < 2:
        return None
    return folded[0], folded[-1]


def _email_affinity(full_name: str, local: str) -> int:
    """2: local part fits a common pattern for this name; 1: contains first/last; 0: unrelated."""
    parts = _name_parts(full_name)
    if not parts:
        return 0
    local = _fold(local.split('+', 1)[0])
    first, last = parts
    if any(_fold(_render(p, first, last)) == local for p in PATTERNS):
        return 2
    if (len(first) >= 3 and first in local) or (len(last) >= 3 and last in local):
        return 1
    return 0


# --------------------------------------------------------------------------- people

def _person(full_name: str, title: str | None, email: str | None, linkedin: str | None,
            page_url: str, excerpt: str, method: str) -> dict[str, Any]:
    return {
        'full_name': full_name,
        'title': _clean_text(title or '') or None,
        'email': email.lower() if email else None,
        'linkedin_url': linkedin or None,
        'evidence_url': page_url,
        'excerpt': _clip(excerpt),
        'method': method,
    }


def _clip(text: str, limit: int = EXCERPT_MAX) -> str:
    text = _clean_text(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _first_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _first_str(value.get('name') or value.get('@value'))
    if isinstance(value, list):
        for item in value:
            found = _first_str(item)
            if found:
                return found
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _types(obj: dict[str, Any]) -> set[str]:
    out = set()
    for t in _as_list(obj.get('@type')):
        if isinstance(t, str):
            out.add(t.rsplit('/', 1)[-1].lower())
    return out


def _jsonld_blocks(root: _Node) -> list[Any]:
    blocks = []
    for node in root.iter():
        if node.tag != 'script' or 'ld+json' not in node.attrs.get('type', '').lower():
            continue
        raw = ''.join(c for c in node.children if isinstance(c, str)).strip()
        raw = re.sub(r'^\s*<!--|-->\s*$', '', raw)
        for candidate in (raw, re.sub(r',\s*([}\]])', r'\1', raw)):
            try:
                blocks.append(json.loads(candidate, strict=False))
                break
            except (ValueError, RecursionError):
                continue
    return blocks


def _walk_json(data: Any) -> Iterator[tuple[dict[str, Any], str | None]]:
    """Yield (dict, key_it_was_found_under) for every dict inside JSON-LD."""
    stack: list[tuple[Any, str | None]] = [(data, None)]
    seen = 0
    while stack and seen < 20_000:
        item, key = stack.pop()
        seen += 1
        if isinstance(item, list):
            stack.extend((x, key) for x in reversed(item))
        elif isinstance(item, dict):
            yield item, key
            for k, v in reversed(list(item.items())):
                if isinstance(v, (dict, list)):
                    stack.append((v, k))


def _clean_email_value(value: Any) -> str | None:
    text = _first_str(value)
    if not text:
        return None
    text = re.sub(r'^mailto:', '', text.strip(), flags=re.I)
    emails = find_emails(urllib.parse.unquote(text))
    return emails[0] if emails else None


def _linkedin_from(values: Iterable[Any]) -> str | None:
    for value in values:
        for item in _as_list(value):
            if isinstance(item, str) and 'linkedin.com/' in item.lower():
                link = normalize_linkedin(item)
                if link and '/in/' in link:
                    return link
    return None


def _visible_excerpt(lines: list[_Line], full_name: str, extra: str | None) -> str | None:
    key = full_name.lower()
    for i, line in enumerate(lines):
        pos = line.text.lower().find(key)
        if pos < 0:
            continue
        own = line.text[pos:]
        text = ' | '.join([own] + [ln.text for ln in lines[i + 1:i + 3] if ln.text])
        room = EXCERPT_MAX - len(extra or '') - 3
        if extra and extra.lower() not in text.lower()[:max(room, 0)]:
            text = f'{own[:max(room, 0)]} | {extra}'
        return text
    return None


def _structured_person(obj: dict[str, Any], key: str | None, page_url: str, lines: list[_Line],
                       method: str) -> dict[str, Any] | None:
    types = _types(obj) if '@type' in obj else {'person'}
    is_person = 'person' in types
    is_agent = 'realestateagent' in types and bool(obj.get('jobTitle'))
    if not (is_person or is_agent):
        return None
    raw_name = _first_str(obj.get('name'))
    if not raw_name and (obj.get('givenName') or obj.get('familyName')):
        raw_name = f"{_first_str(obj.get('givenName')) or ''} {_first_str(obj.get('familyName')) or ''}"
    name = clean_person_name(raw_name or '')
    if not name:
        return None
    title = _first_str(obj.get('jobTitle')) or _first_str(obj.get('roleName'))
    if not title and key == 'founder':
        title = 'Founder'
    email = _clean_email_value(obj.get('email'))
    linkedin = _linkedin_from([obj.get('sameAs'), obj.get('url')])
    if not (title or email or linkedin):
        return None
    evidence = email or title
    excerpt = _visible_excerpt(lines, name, evidence) or ' | '.join(
        x for x in (f'{method}: {name}', title, email) if x)
    return _person(name, title, email, linkedin, page_url, excerpt, method)


def _jsonld_people(blocks: list[Any], page_url: str, lines: list[_Line]) -> list[dict[str, Any]]:
    people = []
    for block in blocks:
        for obj, key in _walk_json(block):
            if '@type' not in obj and key not in ('employee', 'founder', 'member', 'author',
                                                  'employees', 'members', 'founders', 'agent'):
                continue
            record = _structured_person(obj, key, page_url, lines, 'jsonld')
            if record:
                people.append(record)
    return people


def _microdata_props(scope: _Node) -> dict[str, list[str]]:
    props: dict[str, list[str]] = {}
    stack = list(reversed([c for c in scope.children if isinstance(c, _Node)]))
    while stack:
        node = stack.pop()
        names = node.attrs.get('itemprop', '').split()
        if names:
            attrs = node.attrs
            if node.tag == 'meta':
                value = attrs.get('content', '')
            elif node.tag in ('a', 'link', 'area'):
                value = attrs.get('href', '') if 'name' not in names else _text_content(node)
            elif node.tag in ('img', 'audio', 'video', 'source'):
                value = attrs.get('src', '')
            else:
                value = attrs.get('content') or _text_content(node)
            for name in names:
                props.setdefault(name, []).append(value)
        if 'itemscope' in node.attrs and node is not scope:
            continue  # nested item: its props belong to it
        stack.extend(reversed([c for c in node.children if isinstance(c, _Node)]))
    return props


def _microdata_people(root: _Node, page_url: str) -> list[dict[str, Any]]:
    people = []
    for node in root.iter():
        if 'itemscope' not in node.attrs:
            continue
        itemtype = node.attrs.get('itemtype', '').lower()
        if not re.search(r'schema\.org/(?:person|realestateagent)\b', itemtype):
            continue
        props = _microdata_props(node)
        obj = {
            '@type': 'RealEstateAgent' if 'realestateagent' in itemtype else 'Person',
            'name': props.get('name'), 'givenName': props.get('givenName'),
            'familyName': props.get('familyName'), 'jobTitle': props.get('jobTitle'),
            'email': props.get('email'), 'sameAs': props.get('sameAs'), 'url': props.get('url'),
        }
        obj = {k: v for k, v in obj.items() if v}
        excerpt_lines = [_Line(_text_content(node), [], [], [])]
        record = _structured_person(obj, None, page_url, excerpt_lines, 'microdata')
        if record:
            people.append(record)
    return people


def _split_name_title(text: str) -> tuple[str, str] | None:
    """'Jane Doe - Managing Broker' / 'Jane Doe, CEO' / 'Jane Doe CEO' -> (name, title)."""
    pieces = re.split(r'\s+[-–—|•·]\s+|,\s+', text, maxsplit=1)
    if len(pieces) == 2:
        name = clean_person_name(pieces[0])
        if name and looks_like_title(pieces[1]):
            return name, _clean_text(pieces[1])
    tokens = text.split()
    for k in (2, 3, 4):
        if len(tokens) > k:
            name = clean_person_name(' '.join(tokens[:k]))
            rest = ' '.join(tokens[k:])
            if name and looks_like_title(rest) and len(tokens) - k <= 6:
                return name, rest
    return None


def _card_people(lines: list[_Line], page_url: str) -> list[dict[str, Any]]:
    """Name line + nearby title/email/LinkedIn lines, bounded by the next name line."""
    names: list[tuple[int, str, str | None]] = []
    for i, line in enumerate(lines):
        if not line.text:
            continue
        split = _split_name_title(line.text)
        if split:
            names.append((i, split[0], split[1]))
            continue
        name = clean_person_name(line.text)
        if name:
            names.append((i, name, None))
    all_names = [n for _, n, _ in names]
    people: list[dict[str, Any]] = []

    def owned_elsewhere(name: str, local: str) -> bool:
        """True if another name on the page fits this email/slug better."""
        mine = _email_affinity(name, local)
        return mine < 2 and any(_email_affinity(o, local) > mine for o in all_names if o != name)

    for k, (i, name, inline_title) in enumerate(names):
        prev_idx = names[k - 1][0] if k else -1
        end = min(names[k + 1][0] if k + 1 < len(names) else len(lines), i + _CARD_WINDOW)
        title, title_idx = inline_title, i if inline_title else None
        if not title:
            for j in range(i + 1, min(end, i + 4)):
                if looks_like_title(lines[j].text):
                    title, title_idx = _clean_text(lines[j].text), j
                    break
        if not title and i - 1 > prev_idx and looks_like_title(lines[i - 1].text):
            title, title_idx = _clean_text(lines[i - 1].text), i - 1
        email = email_idx = linkedin = None
        for j in range(i, end):
            for candidate in lines[j].emails:
                local = candidate.split('@', 1)[0]
                if _is_role_local(local) and _email_affinity(name, local) == 0:
                    continue
                if owned_elsewhere(name, local):
                    continue
                email, email_idx = candidate, j
                break
            if email:
                break
        for j in range(i, end):
            for link in lines[j].linkedins:
                if not owned_elsewhere(name, link.rsplit('/', 1)[-1]):
                    linkedin = link
                    break
            if linkedin:
                break
        if not (title or email or linkedin):
            continue
        method = 'mailto' if email else 'linkedin_link' if linkedin else 'name_title_block'
        idxs = sorted({i, *(x for x in (title_idx, email_idx) if x is not None)})
        excerpt = ' | '.join(lines[x].text for x in idxs if lines[x].text)
        if email and email not in excerpt.lower():
            excerpt += f' <{email}>'
        people.append(_person(name, title, email, linkedin, page_url, excerpt, method))

    # Anchors whose own text is a person name ("<a href=mailto:..>Jane Doe</a>").
    for line in lines:
        for kind, value, anchor_text in line.anchors:
            name = clean_person_name(anchor_text)
            if not name:
                continue
            excerpt = line.text if value in line.text.lower() else f'{line.text} <{value}>'
            if kind == 'email':
                people.append(_person(name, None, value, None, page_url, excerpt, 'mailto'))
            else:
                people.append(_person(name, None, None, value, page_url, excerpt, 'linkedin_link'))
    return people


def merge_people(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge records for the same person (first+last name match), combining fields.

    The strongest method wins. When a record contributes the email, its
    evidence_url/excerpt are used, because the email is the key published fact.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        key = _name_key(record.get('full_name') or '')
        if not key:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = dict(record)
            order.append(key)
            continue
        if record.get('email') and not current.get('email'):
            current['email'] = record['email']
            current['evidence_url'] = record.get('evidence_url')
            current['excerpt'] = record.get('excerpt')
        for field in ('title', 'linkedin_url'):
            if record.get(field) and not current.get(field):
                current[field] = record[field]
        if len(record.get('full_name') or '') > len(current.get('full_name') or ''):
            current['full_name'] = record['full_name']
        if _METHOD_RANK.get(record.get('method'), 99) < _METHOD_RANK.get(current.get('method'), 99):
            current['method'] = record['method']
    return [merged[k] for k in order]


def extract_people(html: str, page_url: str) -> list[dict[str, Any]]:
    """People published on one page, merged by name and deduplicated."""
    root = _parse(html)
    lines = _build_lines(root)
    records = (_jsonld_people(_jsonld_blocks(root), page_url, lines)
               + _microdata_people(root, page_url)
               + _card_people(lines, page_url))
    return merge_people(records)


# --------------------------------------------------------------------------- company

_ORG_TYPES = frozenset({
    'organization', 'corporation', 'localbusiness', 'realestateagent', 'professionalservice',
    'financialservice', 'insuranceagency', 'legalservice', 'homeandconstructionbusiness',
    'accountingservice', 'store', 'onlinebusiness', 'ngo',
})
_GENERIC_TITLE_PARTS = re.compile(
    r'^(?:home(?:\s*page)?|welcome|official (?:site|website)|index|homepage|main)$', re.I)


def _meta(root: _Node, *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for node in root.iter():
        if node.tag != 'meta':
            continue
        key = (node.attrs.get('property') or node.attrs.get('name') or '').lower()
        if key in wanted and node.attrs.get('content', '').strip():
            return _clean_text(node.attrs['content'])
    return None


def _clean_title(title: str, domain: str) -> str | None:
    parts = [p.strip() for p in re.split(r'\s+[|–—:•·-]\s+|\s*\|\s*', _clean_text(title)) if p.strip()]
    parts = [p for p in parts if not _GENERIC_TITLE_PARTS.match(p)]
    if not parts:
        return None
    label = _fold(domain.split('.')[0])
    for part in parts:
        folded = _fold(part)
        if folded and label and (folded in label or label in folded):
            return part
    return parts[0]


def _format_phone(match: re.Match[str]) -> str:
    area = match.group(1) or match.group(2)
    return f'({area}) {match.group(3)}-{match.group(4)}'


def find_phones(text: str) -> list[str]:
    """US/NANP phone numbers in text, formatted '(617) 555-0142'."""
    out: list[str] = []
    for match in PHONE_RE.finditer(text or ''):
        phone = _format_phone(match)
        if phone not in out:
            out.append(phone)
    return out


def _tel_phone(href: str) -> str | None:
    digits = re.sub(r'\D', '', urllib.parse.unquote(href.split(':', 1)[-1]))
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in '23456789' and digits[3] in '23456789':
        return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'
    return None


def extract_company(html: str, url: str) -> dict[str, Any]:
    """Company facts published on a page (name, emails, phones, LinkedIn, region)."""
    root = _parse(html)
    lines = _build_lines(root)
    domain = _bare_domain(url)
    name = region = country = locality = None
    linkedin_company = None
    emails: list[str] = []
    phones: list[str] = []
    website_name = None
    for block in _jsonld_blocks(root):
        for obj, _key in _walk_json(block):
            types = _types(obj)
            if 'website' in types and not website_name:
                website_name = _first_str(obj.get('name'))
            if not types & _ORG_TYPES:
                continue
            if 'realestateagent' in types and obj.get('jobTitle') and 'person' not in types:
                continue  # an individual agent, handled by extract_people
            if not name:
                name = _first_str(obj.get('name'))
            for addr in _as_list(obj.get('address')):
                if isinstance(addr, dict) and not (region or country):
                    region = _first_str(addr.get('addressRegion'))
                    country = _first_str(addr.get('addressCountry'))
                    locality = _first_str(addr.get('addressLocality'))
            email = _clean_email_value(obj.get('email'))
            if email and email not in emails:
                emails.append(email)
            for tel in _as_list(obj.get('telephone')):
                if isinstance(tel, str):
                    for phone in find_phones(tel) or ([_tel_phone(tel)] if _tel_phone(tel) else []):
                        if phone not in phones:
                            phones.append(phone)
            for same in _as_list(obj.get('sameAs')):
                if isinstance(same, str) and not linkedin_company:
                    link = normalize_linkedin(same)
                    if link and '/company/' in link:
                        linkedin_company = link
    title_node = next((n for n in root.iter() if n.tag == 'title'), None)
    title_text = _text_content(title_node) if title_node else ''
    name = (name or _meta(root, 'og:site_name') or website_name
            or (_clean_title(title_text, domain) if title_text else None))
    for line in lines:
        for email in line.emails:
            if email not in emails:
                emails.append(email)
        for phone in find_phones(line.text):
            if phone not in phones:
                phones.append(phone)
    for node in root.iter():
        if node.tag != 'a':
            continue
        href = node.attrs.get('href', '')
        if href.lower().startswith('tel:'):
            phone = _tel_phone(href)
            if phone and phone not in phones:
                phones.append(phone)
        elif not linkedin_company and 'linkedin.com/company/' in href.lower():
            linkedin_company = normalize_linkedin(href)
    description = _meta(root, 'description', 'og:description')
    return {
        'name': _clean_text(name) if name else None,
        'domain': domain,
        'generic_emails': [e for e in emails if classify_email(e, domain) == 'role'],
        'all_emails': emails,
        'phones': phones[:10],
        'linkedin_company_url': linkedin_company,
        'address_locality': locality,
        'address_region': region,
        'country': country,
        'description': description[:500] if description else None,
    }


# --------------------------------------------------------------------------- page discovery

_PAGE_KEYWORDS = {
    'leadership': 12, 'our-team': 12, 'meet-the-team': 12, 'meet-our-team': 12, 'executives': 11,
    'founders': 11, 'principals': 11, 'team': 10, 'our-agents': 10, 'our-people': 10,
    'people': 9, 'staff': 9, 'agents': 9, 'brokers': 9, 'bios': 9, 'management-team': 11,
    'directory': 7, 'meet': 6, 'about-us': 7, 'who-we-are': 7, 'about': 6, 'partners': 5,
    'management': 4, 'company': 4, 'contact-us': 4, 'contact': 4,
    'agent': 6, 'broker': 6, 'executive': 8, 'leaders': 10, 'professionals': 7, 'associates': 6,
}
_TEXT_PHRASES = {
    'leadership': 12, 'our team': 12, 'meet the team': 12, 'meet our team': 12, 'executive team': 12,
    'management team': 11, 'our people': 10, 'our agents': 10, 'our brokers': 10, 'team': 8,
    'agents': 8, 'staff': 8, 'people': 6, 'founders': 10, 'principals': 10, 'about us': 7,
    'who we are': 7, 'about': 6, 'company': 3, 'partners': 4, 'contact us': 4, 'contact': 4,
    'directory': 6, 'meet': 5, 'bios': 8, 'leaders': 10,
}
_EXCLUDED_PATH = re.compile(
    r'/(?:listings?|property|properties|homes?-for-sale|for-sale|for-rent|idx|search|mls|blog|'
    r'blogs|news|articles?|posts?|tag|tags|category|categories|author|page|wp-content|wp-admin|'
    r'wp-json|wp-includes|feed|rss|cart|checkout|login|log-in|signin|sign-in|signup|sign-up|'
    r'register|account|my-account|privacy|privacy-policy|terms|terms-of-use|terms-of-service|'
    r'sitemap|cdn-cgi|events?|calendar|press-releases?|sold|neighborhoods?|communities|'
    r'market-reports?|mortgage-calculator|calculator|testimonials|reviews|gallery|videos?|'
    r'apply|careers?|jobs?|portal|tenant-portal|owner-portal|pay-rent|payments?)(?:/|$)', re.I)
_ASSET_RE = re.compile(
    r'\.(?:pdf|jpe?g|png|gif|webp|svg|ico|css|js|json|xml|txt|zip|rar|gz|mp4|mov|avi|mp3|wav|'
    r'docx?|xlsx?|pptx?|csv|ics|vcf|woff2?|ttf|eot|webm|heic)$', re.I)
_DATE_PATH = re.compile(r'/(?:19|20)\d{2}/\d{1,2}(?:/|$)')
_TRACKING = re.compile(r'^(?:utm_.*|gclid|fbclid|msclkid|dclid|mc_cid|mc_eid|_ga|_gl|ref|referrer|'
                       r'source|hsa_.*|_hs.*|yclid|igshid)$', re.I)
_PAGINATION_QS = re.compile(r'(?:^|&)(?:page|paged|p|pg|offset|start|sort|order|filter|s|q|query)=', re.I)


def normalize_link(base_url: str, href: str) -> str | None:
    """Absolute http(s) URL with fragment and tracking parameters removed."""
    href = (href or '').strip()
    if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'sms:', 'data:')):
        return None
    try:
        absolute = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlsplit(absolute)
        if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
            return None
        port = parts.port
    except ValueError:
        return None
    query = urllib.parse.urlencode(
        [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
         if not _TRACKING.match(k)], doseq=True)
    netloc = parts.hostname.lower() + (f':{port}' if port and port not in (80, 443) else '')
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path or '/', query, ''))


def _site_key(url: str) -> str:
    return _bare_domain(urllib.parse.urlsplit(url).hostname or '')


def _page_key(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip('/') or '/'
    return f'{_bare_domain(parts.hostname or "")}{path.lower()}?{parts.query}'


def _keyword_score(path: str, text: str) -> float:
    segments = [s for s in path.lower().split('/') if s]
    path_score = 0
    for seg in segments:
        seg = re.sub(r'\.(?:html?|php|aspx?)$', '', seg)
        path_score = max(path_score, _PAGE_KEYWORDS.get(seg, 0),
                         *(_PAGE_KEYWORDS.get(t, 0) for t in re.split(r'[-_]+', seg)))
    low = _clean_text(text).lower()
    text_score = 0
    if low and len(low) <= 60:
        for phrase, weight in _TEXT_PHRASES.items():
            if re.search(rf'(?<![a-z]){re.escape(phrase)}(?![a-z])', low):
                text_score = max(text_score, weight)
    if not (path_score or text_score):
        return 0.0
    score = path_score + 0.6 * text_score
    score -= 2 * max(0, len(segments) - 2)
    return score


def discover_pages(base_url: str, html: str, limit: int = 8) -> list[str]:
    """Same-site links most likely to list people (team/about/leadership/contact...), best first."""
    root = _parse(html)
    site = _site_key(base_url)
    base_key = _page_key(base_url)
    scored: dict[str, tuple[float, int, str]] = {}
    for position, node in enumerate(n for n in root.iter() if n.tag == 'a'):
        url = normalize_link(base_url, node.attrs.get('href', ''))
        if not url or _site_key(url) != site:
            continue
        parts = urllib.parse.urlsplit(url)
        path = parts.path or '/'
        if (path == '/' or _ASSET_RE.search(path) or _EXCLUDED_PATH.search(path)
                or _DATE_PATH.search(path) or _PAGINATION_QS.search(parts.query)):
            continue
        key = _page_key(url)
        if key == base_key:
            continue
        text = ' '.join(x for x in (_text_content(node), node.attrs.get('title', ''),
                                    node.attrs.get('aria-label', '')) if x)
        score = _keyword_score(path, text)
        if parts.query:
            score -= 2
        if score <= 0:
            continue
        if key not in scored or score > scored[key][0]:
            scored[key] = (score, scored.get(key, (0, position))[1], url)
    ranked = sorted(scored.values(), key=lambda x: (-x[0], x[1]))
    out: list[str] = []
    per_parent: dict[str, int] = {}
    for _score, _pos, url in ranked:
        segments = [s for s in urllib.parse.urlsplit(url).path.lower().split('/') if s]
        parent = segments[0] if len(segments) > 1 else '/' + '/'.join(segments)
        if per_parent.get(parent, 0) >= 2:
            continue
        per_parent[parent] = per_parent.get(parent, 0) + 1
        out.append(url)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- email patterns

PATTERNS = ('{first}', '{last}', '{first}.{last}', '{first}{last}', '{f}{last}', '{f}.{last}',
            '{first}{l}', '{first}_{last}', '{last}{f}', '{last}.{first}', '{f}{l}')


def _render(pattern: str, first: str, last: str) -> str:
    return pattern.format(first=first, last=last, f=first[:1], l=last[:1])


def infer_pattern(people_with_emails: Iterable[dict[str, Any]], domain: str) -> dict[str, Any] | None:
    """Most-supported unambiguous local-part pattern among person emails at `domain`."""
    counts = {p: 0 for p in PATTERNS}
    total = 0
    seen: set[str] = set()
    for item in people_with_emails:
        email = (item.get('email') or '').strip().lower()
        if not email or email in seen or classify_email(email, domain) != 'person':
            continue
        parts = _name_parts(item.get('full_name') or '')
        if not parts:
            continue
        seen.add(email)
        total += 1
        local = email.split('@', 1)[0]
        for pattern in PATTERNS:
            if _render(pattern, *parts) == local:
                counts[pattern] += 1
    if not total:
        return None
    best = max(counts.values())
    winners = [p for p, c in counts.items() if c == best]
    if best < 1 or len(winners) > 1:
        return None
    return {'pattern': winners[0], 'support': best, 'total': total, 'confidence': round(best / total, 3)}


def apply_pattern(pattern: str, full_name: str, domain: str) -> str | None:
    """Render `pattern` for a name at `domain`; None for unknown patterns or unusable names."""
    parts = _name_parts(full_name)
    domain = _bare_domain(domain)
    if pattern not in PATTERNS or not parts or not domain:
        return None
    return f'{_render(pattern, *parts)}@{domain}'


# --------------------------------------------------------------------------- crawl orchestration

def normalize_domain(value: str) -> str | None:
    """'https://www.Example.com/about' -> 'www.example.com' (host as given, lowercased)."""
    text = (value or '').strip().lower()
    if not text:
        return None
    if '://' not in text:
        text = 'http://' + text
    try:
        host = urllib.parse.urlsplit(text).hostname
    except ValueError:
        return None
    host = (host or '').rstrip('.')
    return host if host and '.' in host else None


def _merge_company(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, list):
            target = base.setdefault(key, [])
            target.extend(v for v in value if v not in target)
        elif value and not base.get(key):
            base[key] = value
    return base


SUBPAGE_FAILURE_LIMIT = 2  # consecutive network/timeout failures after the homepage end a site


def _crawl_one(raw_domain: str, fetcher: Any, max_pages: int, classify: bool = True) -> dict[str, Any]:
    host = normalize_domain(raw_domain)
    site = _bare_domain(host or raw_domain or '')
    result: dict[str, Any] = {'domain': site, 'homepage_url': None, 'status': 'unreachable',
                              'company': {}, 'people': [], 'pattern': None, 'pages_fetched': 0,
                              'errors': []}
    errors: list[str] = result['errors']

    def note(code: str | None) -> None:
        if code and code not in errors:
            errors.append(code)

    if not host:
        note('unsafe_url')
        return result
    candidates = [f'https://{host}/']
    if not host.startswith('www.'):
        candidates.append(f'https://www.{host}/')
    candidates.append(f'http://{host}/')
    home = None
    for url in candidates:
        response = fetcher.get(url)
        if response.error is None:
            home = response
            break
        note(response.error)
        if response.error != 'network_error':
            break
    if home is None:
        result['status'] = 'blocked' if 'blocked_by_robots' in errors else 'unreachable'
        return result
    home_url = home.final_url or candidates[0]
    result['homepage_url'] = home_url
    site = _site_key(home_url) or site
    result['domain'] = site
    pages = [(home_url, home.text)]
    home_company = None
    if classify:
        from .relevance import classify_site
        home_company = extract_company(home.text, home_url)
        label, score, signals = classify_site({**home_company, 'domain': site}, home.text)
        result['relevance'] = {'label': label, 'score': score}
        if label == 'off_topic':
            home_company['domain'] = site
            home_company['generic_emails'] = [e for e in home_company.get('all_emails', [])
                                              if classify_email(e, site) == 'role']
            result.update(company=home_company, status='off_topic', pages_fetched=1,
                          relevance={'label': label, 'score': score, 'negative': signals['negative'][:5]})
            return result
    seen = {_page_key(home_url), _page_key(candidates[0])}
    queue = discover_pages(home_url, home.text, limit=max(1, max_pages * 2))
    attempts, max_attempts = 0, max(0, max_pages - 1) + 2
    failures = 0
    while queue and len(pages) < max_pages and attempts < max_attempts and failures < SUBPAGE_FAILURE_LIMIT:
        url = queue.pop(0)
        if _page_key(url) in seen:
            continue
        seen.add(_page_key(url))
        attempts += 1
        response = fetcher.get(url)
        if response.error is not None:
            note(response.error)
            # A struggling host (timeouts, resets) will not get better within this crawl.
            failures = failures + 1 if response.error in ('timeout', 'network_error') else 0
            continue
        failures = 0
        final = response.final_url or url
        seen.add(_page_key(final))
        pages.append((final, response.text))
        for extra in discover_pages(final, response.text, limit=4):
            if _page_key(extra) not in seen and extra not in queue:
                queue.append(extra)
    company: dict[str, Any] = {}
    people: list[dict[str, Any]] = []
    for index, (page_url, text) in enumerate(pages):
        parsed = home_company if index == 0 and home_company is not None else extract_company(text, page_url)
        _merge_company(company, dict(parsed))
        people.extend(extract_people(text, page_url))
    company['domain'] = site
    company['generic_emails'] = [e for e in company.get('all_emails', [])
                                 if classify_email(e, site) == 'role']
    result['company'] = company
    result['people'] = merge_people(people)
    result['pattern'] = infer_pattern(result['people'], site)
    result['pages_fetched'] = len(pages)
    result['status'] = 'ok' if result['people'] else 'no_people'
    return result


def crawl_domains(domains: Iterable[str], fetcher: Any, max_pages: int = 6, workers: int = 16,
                  progress: Callable[[int, int, str], None] | None = None,
                  classify: bool = True, trusted: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Crawl many company sites in parallel (politeness is the fetcher's job).

    Returns one result per unique input domain, in input order. A failing site
    yields status 'unreachable' with error codes; it never aborts the batch.
    With ``classify`` the homepage is checked for real-estate relevance and clearly
    off-topic sites stop there (status 'off_topic'). Domains in ``trusted`` (e.g. from
    public license records) are never classified.
    """
    trusted_set = {(normalize_domain(d) or d).lower().removeprefix('www.') for d in trusted if isinstance(d, str)}
    unique: list[str] = []
    for domain in domains:
        if isinstance(domain, str) and domain.strip() and domain.strip() not in unique:
            unique.append(domain.strip())
    results: list[dict[str, Any] | None] = [None] * len(unique)
    if not unique:
        return []

    def run(domain: str) -> dict[str, Any]:
        try:
            key = (normalize_domain(domain) or domain).lower().removeprefix('www.')
            return _crawl_one(domain, fetcher, max(1, max_pages), classify=classify and key not in trusted_set)
        except Exception:
            return {'domain': _bare_domain(normalize_domain(domain) or domain), 'homepage_url': None,
                    'status': 'unreachable', 'company': {}, 'people': [], 'pattern': None,
                    'pages_fetched': 0, 'errors': ['internal_error']}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(unique)))) as pool:
        futures = {pool.submit(run, d): i for i, d in enumerate(unique)}
        done = 0
        for future in as_completed(futures):
            index = futures[future]
            results[index] = future.result()
            done += 1
            if progress is not None:
                try:
                    progress(done, len(unique), results[index]['domain'])
                except Exception:
                    pass
    return [r for r in results if r is not None]
