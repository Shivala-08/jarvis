"""Calendar Setup & Sync Test — walks you through OAuth setup, then tests the full flow.

Usage:
    uv run python scripts/calendar_setup.py          # Setup check + instructions
    uv run python scripts/calendar_setup.py --test    # Full sync test
    uv run python scripts/calendar_setup.py --auth    # Force re-auth
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_DIR = Path("config")
CLIENT_SECRET = CONFIG_DIR / "google_client_secret.json"
TOKEN_FILE = CONFIG_DIR / "google_token.json"


# ---------------------------------------------------------------------------
# Setup check
# ---------------------------------------------------------------------------

def check_setup():
    """Check if Google Calendar OAuth is configured."""
    print("🔍 Checking Google Calendar setup...\n")

    # Check client secret
    if not CLIENT_SECRET.exists():
        print("❌ Missing: config/google_client_secret.json")
        print()
        print("   To get this file:")
        print("   1. Go to https://console.cloud.google.com/")
        print("   2. Create a project (or select existing)")
        print("   3. Enable 'Google Calendar API' (APIs & Services → Library)")
        print("   4. Go to APIs & Services → Credentials")
        print("   5. Click '+ Create Credentials' → OAuth client ID")
        print("   6. Application type: 'Desktop app'")
        print("   7. Name it 'ADHD Co-Processor'")
        print("   8. Click 'Create'")
        print("   9. Download the JSON file")
        print(f"  10. Save it as: {CLIENT_SECRET}")
        print()
        print("   First run will open a browser for Google consent.")
        print("   After that, the token is cached at config/google_token.json")
        return False

    print(f"✅ Found: {CLIENT_SECRET}")

    # Check token
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE) as f:
                token = json.load(f)
            # Check if token has a refresh token (usable)
            if "refresh_token" in token:
                print(f"✅ Token cached: {TOKEN_FILE}")
                exp = token.get("expiry_date", "unknown")
                print(f"   Expiry: {exp}")
                return True
            else:
                print(f"⚠️  Token exists but has no refresh_token: {TOKEN_FILE}")
                print("   You'll need to re-authenticate.")
                return True  # Still usable until expiry
        except Exception as e:
            print(f"⚠️  Token file corrupted: {e}")
            return True
    else:
        print(f"ℹ️  No token yet: {TOKEN_FILE}")
        print("   First auth will create this file automatically.")
        return True  # Client secret exists, can auth


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------

def do_auth():
    """Run the OAuth flow to get/refresh credentials."""
    print("🔐 Starting Google OAuth flow...\n")

    from google_auth_oauthlib.flow import InstalledAppFlow

    SCOPES = ["https://www.googleapis.com/auth/calendar"]

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)

    # This opens a browser for consent
    creds = flow.run_local_server(port=0)

    # Save token
    TOKEN_FILE.write_text(creds.to_json())
    print(f"\n✅ Token saved to {TOKEN_FILE}")
    print(f"   Access token expires: {creds.expiry}")
    print(f"   Has refresh token: {bool(creds.refresh_token)}")


# ---------------------------------------------------------------------------
# Full sync test
# ---------------------------------------------------------------------------

def test_sync():
    """Test the full calendar sync flow."""
    print("🧪 Testing Google Calendar sync flow...\n")

    from agents.calendar_sync import CalendarSync, calendar_available

    # 1. Check connectivity
    print("1️⃣  Checking calendar connectivity...")
    try:
        available = calendar_available()
        if not available:
            print("   ❌ Calendar not reachable. Check OAuth setup.")
            return False
        print("   ✅ Connected to Google Calendar")
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False

    sync = CalendarSync()

    # 2. Read today's events
    print("\n2️⃣  Reading today's calendar events...")
    try:
        events = sync.get_events_for_day()
        print(f"   Found {len(events)} events:")
        for ev in events[:10]:
            marker = "🤖" if ev["is_adhd_copilot"] else "📅"
            start = ev["start"][:16] if len(ev["start"]) > 16 else ev["start"]
            end = ev["end"][:16] if len(ev["end"]) > 16 else ev["end"]
            print(f"     {marker} {ev['title']}  {start} → {end}")
        if len(events) > 10:
            print(f"     ... and {len(events) - 10} more")
    except Exception as e:
        print(f"   ❌ Read failed: {e}")
        return False

    # 3. Get busy blocks
    print("\n3️⃣  Identifying busy blocks (non-copilot events)...")
    try:
        busy = sync.get_busy_blocks()
        print(f"   Found {len(busy)} busy blocks:")
        for b in busy[:5]:
            start = b["start"][:16] if len(b["start"]) > 16 else b["start"]
            end = b["end"][:16] if len(b["end"]) > 16 else b["end"]
            print(f"     🔒 {b['label']}  {start} → {end}")
        if not busy:
            print("     (none — calendar is clear)")
    except Exception as e:
        print(f"   ❌ Busy block check failed: {e}")
        return False

    # 4. Build a test schedule
    print("\n4️⃣  Building test schedule with sample tasks...")
    from agents.scheduler_agent import _estimate_alpha

    test_tasks = [
        {"text": "Finish quarterly report", "estimated_minutes": 90, "priority": "now"},
        {"text": "Reply to Alice's email", "estimated_minutes": 15, "priority": "now"},
        {"text": "Research new framework", "estimated_minutes": 45, "priority": "soon"},
        {"text": "Update project README", "estimated_minutes": 20, "priority": "soon"},
    ]

    # Use default alpha if no history
    alpha = 1.6  # midpoint of 1.4–1.8
    print(f"   Tasks: {len(test_tasks)}")
    print(f"   Alpha: {alpha}")

    # 5. Sync to calendar
    print("\n5️⃣  Syncing schedule to Google Calendar...")
    try:
        result = sync.sync_today(test_tasks, alpha)
        print(f"   ✅ Sync complete:")
        print(f"      Busy blocks found: {result['busy_blocks']}")
        print(f"      Tasks scheduled: {result['tasks_scheduled']}")
        print(f"      Events created: {result['events_created']}")
        print(f"      Old events cleared: {result['events_cleared']}")

        # Show what was created
        blocks = result.get("blocks", [])
        if blocks:
            print(f"\n   📋 Schedule ({len(blocks)} blocks):")
            for b in blocks[:10]:
                btype = "🤖" if b.get("type") == "task" else "📅" if b.get("type") == "calendar_block" else "☕"
                start = b["start"][:16] if len(b["start"]) > 16 else b["start"]
                end = b["end"][:16] if len(b["end"]) > 16 else b["end"]
                mins = b.get("scaled_minutes", "?")
                note = f" ⚠️ overtime" if b.get("note") == "overtime" else ""
                print(f"     {btype} {b.get('label', '?')}  {start} → {end}  ({mins} min){note}")
    except Exception as e:
        print(f"   ❌ Sync failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 6. Verify it's on the calendar
    print("\n6️⃣  Verifying events on calendar...")
    try:
        events_after = sync.get_events_for_day()
        copilot_events = [e for e in events_after if e["is_adhd_copilot"]]
        print(f"   Copilot events on calendar: {len(copilot_events)}")
        for ev in copilot_events[:5]:
            start = ev["start"][:16] if len(ev["start"]) > 16 else ev["start"]
            end = ev["end"][:16] if len(ev["end"]) > 16 else ev["end"]
            print(f"     🤖 {ev['title']}  {start} → {end}")
    except Exception as e:
        print(f"   ⚠️  Verification failed: {e}")

    # 7. Test clear
    print("\n7️⃣  Testing clear (remove copilot events)...")
    try:
        deleted = sync.clear_copilot_events()
        print(f"   ✅ Cleared {deleted} copilot events")
    except Exception as e:
        print(f"   ⚠️  Clear failed: {e}")

    print("\n" + "=" * 50)
    print("🎉 Calendar sync test complete!")
    print("   Open Google Calendar to see the events.")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]

    if "--test" in args:
        # Check setup first
        if not check_setup():
            print("\n⚠️  Run without --test first to see setup instructions.")
            sys.exit(1)
        print()
        success = test_sync()
        sys.exit(0 if success else 1)

    elif "--auth" in args:
        if not CLIENT_SECRET.exists():
            print("❌ Client secret not found. See setup instructions below.\n")
            check_setup()
            sys.exit(1)
        do_auth()

    else:
        # Just check setup
        ready = check_setup()
        if ready:
            print("\n✅ Ready! Run with --test to test the full sync flow.")
        else:
            print("\n⚠️  Follow the instructions above to set up OAuth credentials.")
            print("   Then run: uv run python scripts/calendar_setup.py --auth")
            print("   Then run: uv run python scripts/calendar_setup.py --test")
