"""Bake all dashboard data files for AWHC iframe widgets.

Run by .github/workflows/refresh-data.yml hourly.
Reads CORTEZA_TOKEN from environment. Writes JSON files into the working
directory (repo root). The workflow then commits any changes.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

TOKEN = os.environ.get('CORTEZA_TOKEN')
if not TOKEN:
    print('ERROR: CORTEZA_TOKEN env var not set', file=sys.stderr)
    sys.exit(1)

HOST = "https://main.mareto.helpseeker.org"
NS = "482995025732763649"
EVENT_MODULE = "482995025764614145"
EVENT_TYPE_MOD = "482995025763631105"
PROGRAM_MOD = "482995025761730561"
GEOGRAPHY_MOD = "482995025762779137"
ORG_MOD = "482995025764089857"
PERSON_MOD = "482995025764286465"

def curl(url):
    r = subprocess.run(['curl', '-sS', '-H', f'Authorization: Bearer {TOKEN}', url],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception as e:
        print(f'curl parse fail for {url}: {e} :: {r.stdout[:200]}', file=sys.stderr)
        sys.exit(1)

_label_cache = {}
def fetch_label(record_id, module_id, name_field):
    if not record_id: return None
    key = (module_id, record_id)
    if key in _label_cache: return _label_cache[key]
    d = curl(f"{HOST}/api/compose/namespace/{NS}/module/{module_id}/record/{record_id}")
    rec = d.get('response') or {}
    for v in rec.get('values', []):
        if v.get('name') == name_field:
            _label_cache[key] = v.get('value')
            return v.get('value')
    _label_cache[key] = record_id
    return record_id

def get_val(rec, field):
    for v in rec.get('values', []):
        if v.get('name') == field:
            return v.get('value')
    return None

def get_multi_labels(rec, field, module_id, name_field):
    vals = [v.get('value') for v in rec.get('values', []) if v.get('name') == field]
    return [fetch_label(v, module_id, name_field) for v in vals if v]

def parse_date(s):
    if not s: return None
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except:
        return None

NOW = datetime.now(timezone.utc)
YEAR_START = datetime(NOW.year, 1, 1, tzinfo=timezone.utc)
# Quarter math
QUARTER = (NOW.month - 1) // 3
QUARTER_START = datetime(NOW.year, QUARTER*3 + 1, 1, tzinfo=timezone.utc)
QUARTER_END_MONTH = QUARTER*3 + 3
QUARTER_END = datetime(NOW.year + (1 if QUARTER_END_MONTH > 12 else 0),
                       (QUARTER_END_MONTH % 12) + 1 if QUARTER_END_MONTH != 12 else 12,
                       1, tzinfo=timezone.utc) - timedelta(seconds=1)
LAST30_START = NOW - timedelta(days=30)

# ============================================================
# 1. lobby-data.json — Lobby Activity widget
# ============================================================
print(f"=== Baking lobby-data.json ===")
data = curl(f"{HOST}/api/compose/namespace/{NS}/module/{EVENT_MODULE}/record/?filter=event_lobby_reportable+%3D+true&limit=500")
records = (data.get('response') or {}).get('set') or []
print(f"Lobby-reportable events: {len(records)}")

ytd, q, last30 = 0, 0, 0
for r in records:
    d = parse_date(get_val(r, 'event_start_date'))
    if not d: continue
    if d >= YEAR_START and d <= NOW: ytd += 1
    if d >= QUARTER_START and d <= QUARTER_END: q += 1
    if d >= LAST30_START and d <= NOW: last30 += 1

def recent_key(r):
    d = parse_date(get_val(r, 'event_start_date'))
    return d or datetime.min.replace(tzinfo=timezone.utc)
recent_sorted = sorted(records, key=recent_key, reverse=True)[:25]

def build_item(r, detailed=False):
    name = get_val(r, 'event_name') or 'Untitled event'
    d = get_val(r, 'event_start_date') or ''
    program_label = fetch_label(get_val(r, 'event_program'), PROGRAM_MOD, 'program_name') if get_val(r, 'event_program') else ''
    type_label = fetch_label(get_val(r, 'event_type'), EVENT_TYPE_MOD, 'event_type_name') if get_val(r, 'event_type') else ''
    meta_parts = [p for p in [type_label, program_label] if p]
    meta = ' · '.join(meta_parts) if meta_parts else ''
    item = {'date': d, 'name': name, 'meta': meta}
    if detailed:
        item['province'] = fetch_label(get_val(r, 'event_province_state'), GEOGRAPHY_MOD, 'geography_type_name') if get_val(r, 'event_province_state') else ''
        item['city'] = get_val(r, 'event_city') or ''
        item['coordinator'] = fetch_label(get_val(r, 'event_responsible'), PERSON_MOD, 'person_first_name') if get_val(r, 'event_responsible') else ''
        item['organizations'] = get_multi_labels(r, 'event_organization', ORG_MOD, 'organization_name')
        people_vals = [v.get('value') for v in r.get('values', []) if v.get('name') == 'event_person']
        item['attendee_count'] = len(people_vals)
    return item

with open('lobby-data.json', 'w') as f:
    json.dump({
        'generated_at': NOW.isoformat(),
        'ytd': ytd, 'quarter': q, 'last30d': last30,
        'recent': [build_item(r) for r in recent_sorted[:5]],
        'detailed': [build_item(r, detailed=True) for r in recent_sorted],
        'total': len(records),
    }, f, indent=2)
print(f"  Wrote lobby-data.json (ytd={ytd}, q={q}, 30d={last30})")

# ============================================================
# 2. events-by-province.json — Canada map widget
# ============================================================
print(f"\n=== Baking events-by-province.json ===")
PROVINCES = [
    "British Columbia", "Alberta", "Saskatchewan", "Manitoba", "Ontario",
    "Quebec", "New Brunswick", "Nova Scotia", "Prince Edward Island",
    "Newfoundland and Labrador", "Yukon", "Northwest Territories", "Nunavut"
]
data = curl(f"{HOST}/api/compose/namespace/{NS}/module/{EVENT_MODULE}/record/?limit=500")
all_records = (data.get('response') or {}).get('set') or []
print(f"Total event records: {len(all_records)}")

counts = {p: 0 for p in PROVINCES}
unknown = 0
for r in all_records:
    prov_recid = get_val(r, 'event_province_state')
    if not prov_recid:
        unknown += 1
        continue
    name = fetch_label(prov_recid, GEOGRAPHY_MOD, 'geography_type_name')
    if name in counts:
        counts[name] += 1
    elif name:
        counts[name] = counts.get(name, 0) + 1
    else:
        unknown += 1

with open('events-by-province.json', 'w') as f:
    json.dump({
        'generated_at': NOW.isoformat(),
        'total': len(all_records),
        'with_province': len(all_records) - unknown,
        'counts': counts,
    }, f, indent=2)
print(f"  Wrote events-by-province.json (total={len(all_records)})")
for p, c in sorted(counts.items(), key=lambda x: -x[1]):
    if c > 0:
        print(f"    {p}: {c}")
print("\nAll bakes complete.")
