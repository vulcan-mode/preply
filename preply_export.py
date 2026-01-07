import os
import json
import hashlib
import requests
from icalendar import Calendar, Event as ICalEvent, vDatetime
from dateutil import parser
from datetime import timezone, date, timedelta
import pytz

URL = "https://preply.com/graphql/v2/TutorCalendarEvents"
SESSIONID = os.environ.get("PREPLY_SESSIONID")
if not SESSIONID:
    raise RuntimeError("PREPLY_SESSIONID not set")

HEADERS = {
    "Content-Type": "application/json",
    "Cookie": f"sessionid={SESSIONID}",
}

LOCAL_TZ_NAME = 'America/Lima'  # For X-WR-TIMEZONE
LOCAL_TZ = pytz.timezone(LOCAL_TZ_NAME)

# Load base payload
with open("payload.json") as f:
    base_payload = json.load(f)

cal = Calendar()
cal.add('prodid', '-//Preply Export//Grok//EN')
cal.add('version', '2.0')
cal.add('X-WR-TIMEZONE', LOCAL_TZ_NAME)  # Tells Google to display in Lima time
cal.add('X-WR-CALNAME', 'Preply Lessons')

events_set = set()  # Dedupe by UID

today = date.today()
future = today + timedelta(days=60)
start_date = today

while start_date <= future:
    end_date = min(start_date + timedelta(days=31), future)
    print(f"Fetching range: {start_date} to {end_date}")

    payload = json.loads(json.dumps(base_payload))
    payload["variables"]["dateStart"] = start_date.isoformat()
    payload["variables"]["dateEnd"] = end_date.isoformat()

    r = requests.post(URL, headers=HEADERS, json=payload)
    r.raise_for_status()
    resp = r.json()

    tutor = resp.get("data", {}).get("currentUser", {}).get("tutor")
    if not tutor:
        start_date = end_date + timedelta(days=1)
        continue

    nodes = tutor.get("calendar", {}).get("nodes", [])
    for n in nodes:
        t = n.get("__typename")
        if not t:
            continue

        if t == "LessonTimeslot":
            lesson = n.get("lesson")
            if not lesson or lesson.get("status") not in ("BOOKED", "SCHEDULED"):
                continue
            student = lesson["client"]["user"]["fullName"]
            name = f"{student} Preply"
            uid_source = f"lesson:{lesson.get('id', 'unknown')}:{n.get('dateStart', '')}_{n.get('dateEnd', '')}"

        elif t == "ReservedRecurrentLessonTimeslot":
            config = n.get("recurrentLessonConfig")
            if not config:
                continue
            student = config["client"]["user"]["fullName"]
            name = f"r {student} Preply"
            uid_source = f"reserved:{n.get('id', '')}:{n.get('dateStart', '')}_{n.get('dateEnd', '')}"

        elif t == "TimeoffTimeslot":
            title = n.get("title", "Time off")
            name = f"{title} Preply"
            uid_source = f"timeoff:{n.get('id', '')}:{n.get('dateStart', '')}_{n.get('dateEnd', '')}"

        else:
            continue

        # Parse times (always in UTC from Preply)
        start_utc = parser.isoparse(n["dateStart"])
        end_utc = parser.isoparse(n["dateEnd"])

        # Debug: show local equivalent
        start_local = start_utc.astimezone(LOCAL_TZ)
        print(f"  → {t} | UTC: {start_utc} → Local: {start_local.strftime('%Y-%m-%d %H:%M %Z')}")

        uid = hashlib.sha1(uid_source.encode()).hexdigest() + "@preply"
        if uid in events_set:
            continue
        events_set.add(uid)

        e = ICalEvent()
        e.add('uid', uid)
        e.add('summary', name)
        e.add('dtstart', vDatetime(start_utc))
        e.add('dtend', vDatetime(end_utc))
        e.add('transp', 'OPAQUE')  # Equivalent to BUSY
        cal.add_component(e)

    start_date = end_date + timedelta(days=1)

# Optional: preview first few lines
serialized = cal.to_ical().decode('utf-8').splitlines()
print("\nSample ICS (first 30 lines):")
print('\n'.join(serialized[:30]))

with open("preply.ics", "w", encoding='utf-8') as f:
    f.write(cal.to_ical().decode('utf-8'))

print(f"\nExported {len(cal.subcomponents)} upcoming Preply events to preply.ics")
