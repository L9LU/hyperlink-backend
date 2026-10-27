# utils/phone.py
# Phone, email and name helpers shared by registration and the doctor's
# patient search.
#
# - normalize_phone: store every number in ONE format (E.164, e.g. +2348012345678)
# - phone_variants:  every format an older account might have been saved in, so a
#                    search still finds patients who registered before numbers
#                    were normalized
# - mask_phone / mask_email / display_name: just enough to confirm "yes, that's
#                    them" without exposing the full contact details

import re


def _digits(value) -> str:
    return re.sub(r'\D', '', str(value or ''))


def normalize_phone(phone) -> str:
    """
    Turn a phone number into E.164 ('+2348012345678'). Returns '' if empty.

    Numbers that already start with '+' keep their country code. Numbers without
    one are assumed Nigerian, since that's the pilot market:
        '08012345678'    -> '+2348012345678'
        '8012345678'     -> '+2348012345678'
        '2348012345678'  -> '+2348012345678'
        '+234 801 234 5678' -> '+2348012345678'
    """
    raw = str(phone or '').strip()
    digits = _digits(raw)
    if not digits:
        return ''
    if raw.startswith('+'):
        return '+' + digits
    if digits.startswith('00'):
        return '+' + digits[2:]
    if digits.startswith('0') and len(digits) == 11:
        return '+234' + digits[1:]
    if len(digits) == 10 and digits[0] in '789':
        return '+234' + digits
    return '+' + digits


def phone_variants(phone) -> list:
    """
    Every plausible way this number could be stored, for a Firestore 'in' query.
    Returns [] if the input is too short to be a real phone number.
    """
    e164 = normalize_phone(phone)
    digits = _digits(e164)
    if len(digits) < 8:
        return []

    variants = {e164, digits}
    if e164.startswith('+234') and len(digits) == 13:
        national = digits[3:]  # 8012345678
        variants |= {'0' + national, national, '234' + national, '+234' + national}

    raw = str(phone or '').strip()
    if raw:
        variants.add(raw)

    return sorted(variants)[:30]  # Firestore allows at most 30 values in an 'in' query


def mask_phone(phone) -> str:
    """'+2348012345678' -> '+234•••••••678' (country code and last 3 digits only)."""
    e164 = normalize_phone(phone)
    if len(e164) < 8:
        return '••••'
    return e164[:4] + '•' * (len(e164) - 7) + e164[-3:]


def mask_email(email) -> str:
    """'ada@example.com' -> 'a••@example.com'."""
    email = str(email or '').strip()
    if '@' not in email:
        return '••••'
    local, domain = email.rsplit('@', 1)
    if not local:
        return '••••@' + domain
    return local[0] + '•' * max(len(local) - 1, 2) + '@' + domain


def display_name(name) -> str:
    """'Temiloluwa Owoade' -> 'Temiloluwa O.'  (first name and last initial only)."""
    parts = str(name or '').strip().split()
    if not parts:
        return 'Patient'
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."
