#!/opt/homebrew/bin/python3.11
"""
Debug script: Extract Maps ROW STABLE versions from App Cockpit.

Steps:
  1) Open https://appcockpit.bmwgroup.net/home, search "maps row", click Maps ROW widget
  2) Filter versions to STABLE channel
  3) Extract all 2.19.x and 2.20.x versions

Usage:
    python3 debug_appcockpit_versions.py
    python3 debug_appcockpit_versions.py --headless   # run without visible browser

Note: Requires BMW SSO login — the browser will pause for manual authentication
      if no active session exists.
"""

import argparse
import re
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


APP_COCKPIT_URL = "https://appcockpit.bmwgroup.net/home"
VERSION_PATTERN = re.compile(r'^(2\.(19|20)\.\d+)')

# Persistent user data directory so cookies/sessions survive between runs
USER_DATA_DIR = str(Path(__file__).parent / ".playwright_userdata")


def _is_logged_in(page, debug=False) -> bool:
    """Check whether we are on the actual App Cockpit (not login/SSO)."""
    url = page.url
    if debug:
        print(f"     [debug] URL: {url}")
    # If on SSO domain, not logged in yet
    if "sso.bmwgroup.com" in url:
        if debug:
            print(f"     [debug] Still on SSO domain")
        return False
    # Must be on appcockpit domain
    if "appcockpit.bmwgroup.net" not in url:
        if debug:
            print(f"     [debug] Not on appcockpit domain")
        return False
    # Best signal: can we see the search input (only present when authenticated)?
    try:
        search_visible = page.locator(
            "input[placeholder*='Search'], input[type='search'], input[aria-label*='earch']"
        ).first.is_visible(timeout=2000)
        if search_visible:
            if debug:
                print(f"     [debug] Search input visible → logged in")
            return True
    except Exception:
        pass
    # Check for navigation elements that only show when authenticated
    try:
        nav_visible = page.locator("nav, [class*='toolbar'], [class*='header'] a[href*='/home']").first.is_visible(timeout=1000)
        if nav_visible:
            if debug:
                print(f"     [debug] Nav/toolbar visible → logged in")
            return True
    except Exception:
        pass
    # If the page shows the "Member Login" heading, we're on the login gate
    try:
        member_login = page.locator("text=Member Login")
        if member_login.is_visible(timeout=500):
            if debug:
                print(f"     [debug] 'Member Login' visible → not logged in")
            return False
    except Exception:
        pass
    # Fallback: if on appcockpit without destination param, likely logged in
    if "destination=" not in url:
        if debug:
            print(f"     [debug] On appcockpit without destination= → assume logged in")
        return True
    if debug:
        print(f"     [debug] destination= in URL → not logged in")
    return False


def wait_for_login(page, timeout_seconds=120):
    """Wait for full authentication (Sign In button + SSO) to complete."""
    # Wait a moment for the page to settle before first check
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    time.sleep(2)

    if _is_logged_in(page, debug=True):
        print(f"  ✓ Already authenticated.")
        return

    # Try to auto-click "Sign In" button on the login gate page
    print(f"  🔐 Login required in the PLAYWRIGHT browser window (not your regular Edge).")
    print(f"     After first login, the session is cached for future runs.")
    try:
        clicked = False
        for selector in [
            "button:has-text('Sign In')",
            "a:has-text('Sign In')",
            "button:has-text('Member Login')",
            "a:has-text('Member Login')",
            "[class*='login'] button",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    clicked = True
                    print(f"     Clicked login button. SSO page loading …")
                    break
            except Exception:
                continue
        if not clicked:
            time.sleep(3)
    except Exception as e:
        print(f"     Could not auto-click login: {e}")

    # Bring the window to front so user can see it
    try:
        page.bring_to_front()
    except Exception:
        pass

    print(f"     ➡️  Please log in via the Playwright browser window.")
    print(f"     Waiting up to {timeout_seconds}s for redirect back to App Cockpit …")

    # Wait for the page URL to return to appcockpit.bmwgroup.net (after SSO redirect)
    timeout_ms = timeout_seconds * 1000
    try:
        page.wait_for_url("**/appcockpit.bmwgroup.net/**", timeout=timeout_ms)
    except PlaywrightTimeout:
        # Check if maybe we're already there despite the timeout
        if "appcockpit.bmwgroup.net" not in page.url:
            print(f"  ❌ Login timed out after {timeout_seconds}s.")
            print(f"     [debug] Final URL: {page.url}")
            sys.exit(1)

    # Now wait for the search input to confirm we're fully loaded
    print(f"     Redirected to App Cockpit. Waiting for page to load …")
    try:
        page.locator(
            "input[placeholder*='Search'], input[type='search'], input[aria-label*='earch']"
        ).first.wait_for(state="visible", timeout=30000)
    except PlaywrightTimeout:
        # Page is on appcockpit domain, just give it a moment
        time.sleep(3)

    print(f"  ✓ Login successful.")


def search_and_click_maps_row(page):
    """Search for 'maps row' and click the Maps ROW widget."""
    print("\n[1/3] Searching for 'maps row' …")

    # Wait for page to be fully ready (post-login)
    page.wait_for_load_state("networkidle", timeout=30000)

    # Ensure we're on the home/dashboard page before typing
    # (guard against race conditions with login redirect)
    page.wait_for_selector(
        "input[placeholder*='Search'], input[type='search'], input[aria-label*='earch']",
        state="visible", timeout=15000,
    )

    # Find and fill the search field
    search_input = page.locator("input[placeholder*='Search'], input[type='search'], input[aria-label*='earch']").first
    search_input.click()
    search_input.fill("maps row")
    print("  ✓ Typed 'maps row' in search field")

    # Wait for results to appear
    time.sleep(2)

    # Click on Maps ROW widget — look for the link/card containing "Maps ROW"
    maps_row_widget = page.locator("text=Maps ROW").first
    maps_row_widget.wait_for(state="visible", timeout=15000)
    maps_row_widget.click()
    print("  ✓ Clicked 'Maps ROW' widget")

    # Wait for the app detail page to load
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_url("**/app-detail/**", timeout=15000)
    print(f"  ✓ Navigated to app detail page")


def filter_stable_channel(page):
    """Open version filters and select STABLE channel."""
    print("\n[2/3] Filtering to STABLE channel …")

    # Wait for the Versions section to appear
    page.wait_for_selector("text=Versions", timeout=15000)

    # Click the "Toggle filters" button next to Versions
    toggle_btn = page.locator("button").filter(has_text="Toggle filters").first
    if not toggle_btn.is_visible(timeout=5000):
        # Fallback: look for filter icon button near "Versions"
        toggle_btn = page.locator("text=Versions").locator("..").locator("button").first
    toggle_btn.click()
    print("  ✓ Opened filter panel")

    time.sleep(1)

    # Find and click the CHANNEL combobox/dropdown
    channel_combo = page.locator("text=CHANNEL").first
    channel_combo.click()
    print("  ✓ Opened CHANNEL dropdown")

    time.sleep(2)

    # Select STABLE from the dropdown options
    # The dropdown renders checkboxes inside listitems in an overlay panel.
    # We need to click the checkbox next to "STABLE", not the text div itself.
    stable_checkbox = page.locator("li").filter(has_text="STABLE").locator("input[type='checkbox'], [role='checkbox']").first
    if stable_checkbox.is_visible(timeout=5000):
        stable_checkbox.click(force=True)
    else:
        # Fallback: click the listitem containing STABLE
        stable_item = page.locator("li").filter(has_text="STABLE").first
        stable_item.click(force=True)
    print("  ✓ Selected STABLE")

    time.sleep(1)

    # Close the dropdown by clicking the overlay backdrop
    backdrop = page.locator(".cdk-overlay-backdrop")
    if backdrop.is_visible(timeout=3000):
        backdrop.click(force=True)
    else:
        # Press Escape as fallback
        page.keyboard.press("Escape")
    print("  ✓ Closed filter dropdown")

    # Wait for list to update
    time.sleep(2)


def extract_versions(page):
    """Extract all 2.19.x and 2.20.x versions from the filtered list."""
    print("\n[3/3] Extracting 2.19.x and 2.20.x versions …")

    versions_20x = []
    versions_19x = []
    all_found = set()

    # Use JavaScript to efficiently extract all link texts from the version list
    # This is much faster than calling inner_text() on each element individually.
    # The list uses virtual scrolling, so we need to scroll and collect.
    max_scroll_attempts = 30
    last_count = 0
    stable_count = 0

    # Find the scrollable version list container
    scroll_container_js = """
    () => {
        // Look for the scrollable list container
        const lists = document.querySelectorAll('[class*="version-list"], [class*="versions-list"], ul, ol');
        for (const el of lists) {
            if (el.scrollHeight > el.clientHeight && el.querySelectorAll('a').length > 3) {
                return true;
            }
        }
        return false;
    }
    """

    for scroll_attempt in range(max_scroll_attempts):
        # Extract all link texts in one JS call (fast!)
        link_texts = page.evaluate("""
        () => {
            const links = document.querySelectorAll('a[href*="app-detail"]');
            return Array.from(links).map(a => a.innerText.replace(/\\n/g, ' ').trim());
        }
        """)

        for text in link_texts:
            match = VERSION_PATTERN.search(text)
            if match:
                version = match.group(1)
                if version not in all_found:
                    all_found.add(version)
                    build_match = re.search(r'\((\d+)\)', text)
                    build = build_match.group(1) if build_match else "?"
                    type_match = re.search(r'(Release \w+[\w\s]*?)(?:\s+INT|$)', text)
                    release_type = type_match.group(1).strip() if type_match else "Unknown"

                    entry = {
                        "version": version,
                        "build": build,
                        "type": release_type,
                        "full_text": text[:80],
                    }

                    if version.startswith("2.20."):
                        versions_20x.append(entry)
                    elif version.startswith("2.19."):
                        versions_19x.append(entry)

        current_count = len(all_found)
        if current_count == last_count:
            stable_count += 1
            if stable_count >= 3:
                break  # No new versions found after 3 scroll attempts
        else:
            stable_count = 0
        last_count = current_count

        # Scroll the version list container down
        page.evaluate("""
        () => {
            // Find scrollable container with version links
            const containers = document.querySelectorAll('[class*="version"], [class*="list"], nav, aside, ul');
            for (const el of containers) {
                if (el.scrollHeight > el.clientHeight + 50 && el.querySelectorAll('a[href*="app-detail"]').length > 0) {
                    el.scrollTop += 600;
                    return true;
                }
            }
            // Fallback: scroll the whole page
            window.scrollBy(0, 600);
            return false;
        }
        """)
        time.sleep(0.8)

    # Sort by version number descending
    def version_sort_key(entry):
        parts = entry["version"].split(".")
        return tuple(int(p) for p in parts)

    versions_20x.sort(key=version_sort_key, reverse=True)
    versions_19x.sort(key=version_sort_key, reverse=True)

    return versions_20x, versions_19x


def print_results(versions_20x, versions_19x):
    """Print extracted versions in a formatted table."""
    sep = "=" * 62

    print(f"\n{sep}")
    print(f"  STABLE Channel — Maps ROW Versions")
    print(f"{sep}")

    print(f"\n  ── 2.20.x ({len(versions_20x)} versions) ──")
    if versions_20x:
        print(f"  {'Version':<12} {'Build':<8} {'Type'}")
        print(f"  {'─'*12} {'─'*8} {'─'*30}")
        for v in versions_20x:
            print(f"  {v['version']:<12} {v['build']:<8} {v['type']}")
    else:
        print(f"  (none found)")

    print(f"\n  ── 2.19.x ({len(versions_19x)} versions) ──")
    if versions_19x:
        print(f"  {'Version':<12} {'Build':<8} {'Type'}")
        print(f"  {'─'*12} {'─'*8} {'─'*30}")
        for v in versions_19x:
            print(f"  {v['version']:<12} {v['build']:<8} {v['type']}")
    else:
        print(f"  (none found)")

    print(f"\n  Latest 2.20.x: {versions_20x[0]['version']} (build {versions_20x[0]['build']})" if versions_20x else "")
    print(f"  Latest 2.19.x: {versions_19x[0]['version']} (build {versions_19x[0]['build']})" if versions_19x else "")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Extract Maps ROW STABLE versions from App Cockpit",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode (skips SSO login prompt)",
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="SSO login timeout in seconds (default: 120)",
    )
    args = parser.parse_args()

    print(f"\n{'='*62}")
    print(f"  App Cockpit — Extract Maps ROW STABLE Versions")
    print(f"{'='*62}\n")

    with sync_playwright() as p:
        # Use persistent isolated profile — first run needs manual SSO login,
        # subsequent runs reuse the cached session.
        launch_args = dict(
            user_data_dir=USER_DATA_DIR,
            headless=args.headless,
            viewport={"width": 1400, "height": 900},
            ignore_https_errors=True,
        )
        try:
            context = p.chromium.launch_persistent_context(channel="msedge", **launch_args)
        except Exception:
            print("  ⚠️  Edge not found, falling back to Chromium")
            context = p.chromium.launch_persistent_context(**launch_args)
        page = context.new_page()

        # Step 0: Navigate to App Cockpit
        print(f"[0] Opening {APP_COCKPIT_URL} …")
        page.goto(APP_COCKPIT_URL, wait_until="domcontentloaded")

        # Handle SSO login if needed
        wait_for_login(page, timeout_seconds=args.timeout)

        # Step 1: Search and click Maps ROW
        search_and_click_maps_row(page)

        # Step 2: Filter to STABLE channel
        filter_stable_channel(page)

        # Step 3: Extract versions
        versions_20x, versions_19x = extract_versions(page)

        # Print results
        print_results(versions_20x, versions_19x)

        context.close()

    print("Done.")


if __name__ == "__main__":
    main()
