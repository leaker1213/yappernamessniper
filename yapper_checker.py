"""
Yapper Username Availability Checker — nodriver edition
=========================================================
Uses nodriver (2026 standard) which patches Chrome at a lower level
than standard Selenium/undetected-chromedriver. It removes CDP leaks
and navigator.webdriver flags that Cloudflare specifically looks for,
making the browser appear as a real Chrome instance.

Flow:
  1. Go to yapper.website
  2. Click "Log in" → Click "Create an account"
  3. For each username:
       - Fill random email, username, password (human-like typing)
       - Check ToS checkbox → click Sign up
       - If CAPTCHA appears: pause and wait for YOU to solve it
       - If AVAILABLE → remove from names.txt, print, stop
       - If TAKEN     → clear form and try next

Requirements:
    pip install nodriver
    Google Chrome must be installed.

Usage:
    Put usernames in names.txt (one per line), then run:
    py yapper_checker.py
"""

import asyncio
import random
import string
import re
import nodriver as uc

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
NAMES_FILE = "names.txt"
PASSWORD   = "Test1234!"
SITE_URL   = "https://www.yapper.website/"
# ─────────────────────────────────────────────

CF_FAIL_PHRASES = [
    "couldn't confirm you're human",
    "bot check couldn't confirm",
    "überprüfung fehlgeschlagen",
    "verification failed",
    "fehlgeschlagen",
    "try the check again",
    "problembehebung",
]


# ── Utility ───────────────────────────────────────────────────────────────────

def random_email() -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=14)) + "@gmail.com"


def is_valid_username(name: str) -> bool:
    if len(name) < 2 or len(name) > 32:
        return False
    if re.search(r'[^a-zA-Z0-9_\-\.]', name):
        return False
    return True


def load_names(filepath: str) -> list:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except FileNotFoundError:
        print(f"[ERROR] '{filepath}' not found!")
        exit(1)
    names = [n for n in raw if is_valid_username(n)]
    skipped = len(raw) - len(names)
    if not names:
        print(f"[ERROR] No valid usernames in '{filepath}'.")
        exit(1)
    print(f"[INFO] Loaded {len(names)} username(s) ({skipped} junk lines skipped).\n")
    return names


def remove_from_names_file(filepath: str, username: str):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(l for l in lines if l.strip() != username)
    except Exception as e:
        print(f"[WARN] Could not update {filepath}: {e}")


# ── Human-like typing ─────────────────────────────────────────────────────────

async def human_type(elem, text: str):
    """Type one character at a time with random delays."""
    for char in text:
        await elem.send_keys(char)
        await asyncio.sleep(random.uniform(0.02, 0.07))
        if random.random() < 0.06:
            await asyncio.sleep(random.uniform(0.05, 0.15))


# ── Page helpers ──────────────────────────────────────────────────────────────

async def page_text(tab) -> str:
    """Get the visible text of the current page."""
    try:
        return await tab.evaluate("document.body.innerText") or ""
    except Exception:
        return ""


async def get_text_inputs(tab) -> list:
    """Return all visible text-type inputs on the page."""
    skip = {"checkbox", "radio", "submit", "button", "hidden", "image"}
    try:
        all_inputs = await tab.select_all("input")
        result = []
        for inp in all_inputs:
            try:
                t = (inp.attrs.get("type") or "text").lower()
                if t not in skip:
                    result.append(inp)
            except Exception:
                pass
        return result
    except Exception:
        return []


async def find_clickable(tab, texts: list, timeout: int = 8):
    """
    Try to find and return an element matching any of the given text strings.
    Searches by exact text match first, then partial.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        for text in texts:
            try:
                elem = await tab.find(text, best_match=True)
                if elem:
                    return elem
            except Exception:
                pass
        await asyncio.sleep(0.4)
    return None


# ── CAPTCHA handler ───────────────────────────────────────────────────────────

async def handle_captcha(tab, detect_timeout: int = 4) -> bool:
    """
    Detects Cloudflare Turnstile (iframe or failure message).
    Pauses and waits for the USER to solve it in Chrome.
    Returns True if a CAPTCHA was found and resolved, False if none seen.
    """
    async def cf_present() -> bool:
        try:
            iframes = await tab.select_all("iframe")
            for iframe in iframes:
                src   = (iframe.attrs.get("src")   or "").lower()
                title = (iframe.attrs.get("title") or "").lower()
                if any(k in src   for k in ("cloudflare", "turnstile", "challenge")) or \
                   any(k in title for k in ("human", "robot", "mensch", "captcha")):
                    return True
        except Exception:
            pass
        try:
            txt = (await page_text(tab)).lower()
            if any(p in txt for p in CF_FAIL_PHRASES):
                return True
        except Exception:
            pass
        return False

    # Quick scan
    deadline = asyncio.get_event_loop().time() + detect_timeout
    found = False
    while asyncio.get_event_loop().time() < deadline:
        if await cf_present():
            found = True
            break
        await asyncio.sleep(0.4)

    if not found:
        return False

    txt = (await page_text(tab)).lower()
    is_failure = any(p in txt for p in CF_FAIL_PHRASES)

    print("\n" + "=" * 55)
    if is_failure:
        print("  ❌  CLOUDFLARE VERIFICATION FAILED")
        print("  Click 'Try the check again' in Chrome,")
        print("  then tick the checkbox.")
    else:
        print("  ⚠️  CAPTCHA DETECTED")
        print("  Please tick the Cloudflare checkbox in Chrome.")
    print("  Script continues automatically once resolved.")
    print("=" * 55)

    while await cf_present():
        await asyncio.sleep(0.5)

    print("[INFO] CAPTCHA resolved — continuing...\n")
    await asyncio.sleep(0.6)
    return True


# ── Navigation ────────────────────────────────────────────────────────────────

async def navigate_to_create_account(tab):
    """Homepage → Log in → Create an account form."""
    await tab.get(SITE_URL)
    await asyncio.sleep(random.uniform(1.0, 1.5))

    # Step 1 — click Log in
    btn = await find_clickable(tab, ["Log in", "Log In"])
    if not btn:
        raise RuntimeError("Could not find Log in button.")
    await btn.click()
    print("[INFO] Clicked: Log in button")
    await asyncio.sleep(random.uniform(1.0, 1.5))

    # Handle any CAPTCHA that appears on the login modal
    await handle_captcha(tab, detect_timeout=2)

    # Step 2 — click Create an account
    btn = await find_clickable(tab, ["Create an account"], timeout=8)
    if not btn:
        raise RuntimeError("Could not find 'Create an account' link.")
    await btn.click()
    print("[INFO] Clicked: Create an account")
    await asyncio.sleep(random.uniform(0.8, 1.2))


async def is_on_create_account_form(tab) -> bool:
    """True if the signup form (with Sign up button + 3 inputs) is open."""
    try:
        txt = await page_text(tab)
        if "Sign up" not in txt:
            return False
        inputs = await get_text_inputs(tab)
        return len(inputs) >= 3
    except Exception:
        return False


async def ensure_create_account_form(tab, max_attempts: int = 3):
    for attempt in range(1, max_attempts + 1):
        if await is_on_create_account_form(tab):
            return
        print(f"[INFO] Re-navigating to Create an account (attempt {attempt})...")
        try:
            await navigate_to_create_account(tab)
        except RuntimeError as e:
            print(f"[WARN] {e}")
        await asyncio.sleep(1)
    if not await is_on_create_account_form(tab):
        raise RuntimeError("Cannot reach the Create an account form.")


# ── Form filling ──────────────────────────────────────────────────────────────

async def fill_and_submit(tab, username: str) -> bool:
    """Fill form with reliable selectors, ensure checkbox is ticked, submit."""
    email = random_email()

    # 1. Fill Email, Username, Password directly via JavaScript using exact attributes/labels
    # This prevents misaligned indexes or stale elements.
    fill_script = f"""
    (function() {{
        let emailInput = document.querySelector('input[type="email"]') || 
                         document.querySelector('input[autocomplete="email"]') ||
                         document.querySelector('input[placeholder*="email" i]') ||
                         document.querySelectorAll('input:not([type="checkbox"]):not([type="hidden"])')[0];
                         
        let usernameInput = document.querySelector('input[autocomplete="username"]') || 
                            document.querySelector('input[placeholder*="user" i]') ||
                            document.querySelectorAll('input:not([type="checkbox"]):not([type="hidden"])')[1];
                            
        let passwordInput = document.querySelector('input[type="password"]') ||
                            document.querySelectorAll('input:not([type="checkbox"]):not([type="hidden"])')[2];

        function setVal(input, val) {{
            if (!input) return false;
            let setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(input, val);
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }}

        let ok1 = setVal(emailInput, {repr(email)});
        let ok2 = setVal(usernameInput, {repr(username)});
        let ok3 = setVal(passwordInput, {repr(PASSWORD)});
        return ok1 && ok2 && ok3;
    }})();
    """
    
    success = await tab.evaluate(fill_script)
    if not success:
        # Fallback if evaluation didn't find inputs
        inputs = await get_text_inputs(tab)
        if len(inputs) < 2:
            return False
        if len(inputs) >= 1:
            await inputs[0].clear_input()
            await inputs[0].send_keys(email)
        if len(inputs) >= 2:
            await inputs[1].clear_input()
            await inputs[1].send_keys(username)
        if len(inputs) >= 3:
            await inputs[2].clear_input()
            await inputs[2].send_keys(PASSWORD)

    await asyncio.sleep(0.2)

    # 2. Checkbox: only check if it is NOT checked!
    # Clicking it when already checked was unchecking it.
    ensure_checkbox_script = """
    (function() {
        let cb = document.querySelector('input[type="checkbox"]');
        if (!cb) return false;
        if (!cb.checked) {
            cb.click();
            if (!cb.checked) {
                let setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked').set;
                setter.call(cb, true);
                cb.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
        return cb.checked;
    })();
    """
    await tab.evaluate(ensure_checkbox_script)
    await asyncio.sleep(0.2)

    # 3. Check for CAPTCHA before submitting
    await handle_captcha(tab, detect_timeout=2)

    # Make double sure checkbox is STILL checked before clicking submit
    await tab.evaluate(ensure_checkbox_script)
    await asyncio.sleep(0.1)

    # 4. Click Sign Up
    click_signup_script = """
    (function() {
        // Look for buttons that say 'Sign up'
        let buttons = Array.from(document.querySelectorAll('button'));
        let signupBtn = buttons.find(b => b.innerText.trim().toLowerCase().includes('sign up')) ||
                        document.querySelector('button[type="submit"]');
        if (signupBtn) {
            signupBtn.click();
            return true;
        }
        return false;
    })();
    """
    clicked = await tab.evaluate(click_signup_script)
    if not clicked:
        signup = await find_clickable(tab, ["Sign up"], timeout=4)
        if signup:
            await signup.click()
            clicked = True

    if not clicked:
        return False

    # Quick check for potential CAPTCHA triggered by click
    await asyncio.sleep(0.3)
    await handle_captcha(tab, detect_timeout=1)

    return True


# ── Result detection ──────────────────────────────────────────────────────────

async def detect_result(tab, max_wait: float = 3.0) -> str:
    """
    Poll the page text quickly every 100ms instead of a fixed long sleep.
    Returns immediately as soon as 'already taken' or success appears!
    """
    taken = [
        "username is taken", "username already", "already taken",
        "not available", "unavailable", "already in use",
        "already exists", "try another", "username taken",
    ]
    available = [
        "verify your email", "check your email", "account created",
        "confirmation", "sent you", "you're in", "almost there",
    ]

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < max_wait:
        try:
            txt = (await page_text(tab)).lower()
            for p in taken:
                if p in txt:
                    return "TAKEN"
            for p in available:
                if p in txt:
                    return "AVAILABLE"

            url = tab.url.lower()
            if any(x in url for x in ("channels", "/app", "dashboard")):
                return "AVAILABLE"
        except Exception:
            pass
        await asyncio.sleep(0.1)

    return "UNKNOWN"


# ── Main ──────────────────────────────────────────────────────────────────────

async def amain():
    names = load_names(NAMES_FILE)

    print("=" * 55)
    print("  Yapper Username Availability Checker")
    print("  (nodriver — 2026 Cloudflare bypass)")
    print("=" * 55)
    # Add flags to prevent Chrome from freezing/throttling when minimized or running in background
    browser_args = [
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    browser = await uc.start(headless=False, browser_args=browser_args)
    tab = await browser.get(SITE_URL)

    print("[INFO] Opening Yapper → Log in → Create an account...")
    try:
        await navigate_to_create_account(tab)
        print("[INFO] Create an account form is open.\n")
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        browser.stop()
        return

    found = None

    for i, username in enumerate(names, 1):
        print(f"[{i}/{len(names)}] Checking: {username!r} ...", end=" ", flush=True)
        try:
            await ensure_create_account_form(tab)

            submitted = await fill_and_submit(tab, username)
            if not submitted:
                print("ERROR:form not filled")
                continue

            status = await detect_result(tab)
            print(status)

            if status == "AVAILABLE":
                found = username
                remove_from_names_file(NAMES_FILE, username)
                break
            elif status == "TAKEN":
                # Remove taken name from names.txt so it doesn't get checked again!
                remove_from_names_file(NAMES_FILE, username)

            await asyncio.sleep(random.uniform(0.1, 0.2))

        except RuntimeError as e:
            print(f"\n[ERROR] {e}")
            break
        except Exception as e:
            print(f"\n[ERROR] Unexpected: {e}")
            break

    print("\n" + "=" * 55)
    if found:
        print(f"🎉  AVAILABLE USERNAME FOUND: {found}")
        print(f"    Browser left open — finish signing up!")
    else:
        print("❌  No available usernames found in the list.")
        browser.stop()
    print("=" * 55)


if __name__ == "__main__":
    uc.loop().run_until_complete(amain())
