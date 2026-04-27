"""Blackboard scraper module using Playwright.

Authenticates to Blackboard, navigates to calendar/assignments pages,
extracts assignment data, and returns structured Assignment objects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dateutil import parser as dateutil_parser
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import stealth

from config import Config

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

VIEWPORT_WIDTH: int = 1920
VIEWPORT_HEIGHT: int = 1080

MIN_DELAY_MS: int = 1000
MAX_DELAY_MS: int = 3000

DEFAULT_SESSION_FILE_PATH: str = "./session.json"

# Primary selectors (common Blackboard patterns)
SELECTORS_CALENDAR: list[str] = [
    "#calendarContainer",
    ".calendar-event",
    ".calDay",
    "[data-event-type='assignment']",
    ".bb-base--calendar-event",
]

# Ultra calendar selectors (Blackboard Ultra SPA)
SELECTORS_ULTRA_CALENDAR: list[str] = [
    "[data-testid='calendar-event']",
    ".CalendarEvent",
    ".calendar-event",
    "[data-testid='event-card']",
    ".ultra-calendar-event",
]

SELECTORS_ASSIGNMENTS: list[str] = [
    ".assignment-list",
    "li.assignment",
    "div.assignment",
    ".bb-base--assignment",
    "[data-assignmentid]",
]

# Ultra assignment selectors (Blackboard Ultra SPA)
SELECTORS_ULTRA_ASSIGNMENT: list[str] = [
    "[data-testid='assignment-card']",
    ".assignment-card",
    "[data-testid='gradebook-item']",
    ".ultra-assignment-card",
]

SELECTORS_COURSE_NAME: list[str] = [
    ".course-name",
    ".courseTitle",
    "h3.course-title",
    ".course-name-link",
    "[data-course-name]",
]

SELECTORS_DUE_DATE: list[str] = [
    ".dueDate",
    ".due",
    "span.due",
    ".bb-base--item-due-date",
    "[data-due-date]",
    "time.due",
]

SELECTORS_ASSIGNMENT_TITLE: list[str] = [
    ".assignment-title",
    ".itemTitle",
    "a.assignment-name",
    "h3.assignmentTitle",
    ".bb-base--assignment-title",
]

SELECTORS_STATUS: list[str] = [
    ".status",
    ".assignment-status",
    "[data-status]",
    ".bb-base--status",
]


# ─── Dataclass ────────────────────────────────────────────────────────────────


@dataclass
class Assignment:
    """Represents a scraped Blackboard assignment."""

    assignment_id: str
    title: str
    course_name: str
    due_date: datetime  # timezone-aware
    status: str  # e.g., "Pending", "In Progress", or "Unknown"
    source_url: str
    scraped_at: datetime  # timezone-aware


# ─── Exceptions ───────────────────────────────────────────────────────────────


class ScrapingError(Exception):
    """Raised when scraping fails after all retries."""

    pass


class LoginError(ScrapingError):
    """Raised when Blackboard login fails."""

    pass


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _random_delay() -> None:
    """Sleep for a random delay to mimic human behavior."""
    delay_ms = random.randint(MIN_DELAY_MS, MAX_DELAY_MS)
    logger.debug("Sleeping for %dms to mimic human behavior", delay_ms)
    return asyncio.sleep(delay_ms / 1000.0)


def _utc_now() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


def _load_session(session_path: Path) -> dict | None:
    """Load session data from JSON file. Returns None if missing/invalid."""
    if not session_path.exists():
        logger.debug("No session file found at %s", session_path)
        return None
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        logger.debug("Loaded session from %s", session_path)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load session from %s: %s", session_path, exc)
        return None


def _save_session(session_path: Path, data: dict) -> None:
    """Save session data to JSON file atomically."""
    temp_path = session_path.with_suffix(".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")
    try:
        temp_path.rename(session_path)
    except OSError:
        # Cross-device rename fallback
        session_path.write_text(text, encoding="utf-8")
        temp_path.unlink(missing_ok=True)
    logger.info("Session saved to %s", session_path)


def _generate_assignment_id(raw: dict, fallback_index: int = 0) -> str:
    """Generate a deterministic assignment ID from raw data.

    Tries to find a natural ID from the raw dict, otherwise derives
    from title + course_name + due_date hash.
    """
    # Try natural IDs first
    for key in ("id", "assignment_id", "data-assignment-id", "itemId"):
        if key in raw and raw[key]:
            return str(raw[key])

    # Derive from attributes
    title = raw.get("title", "")
    course = raw.get("course_name", "")
    due = raw.get("due_date", "")
    composite = f"{title}|{course}|{due}|{fallback_index}"
    import hashlib

    return hashlib.sha256(composite.encode()).hexdigest()[:16]


# ─── BlackboardScraper ────────────────────────────────────────────────────────


class BlackboardScraper:
    """Scrapes assignments from Blackboard using headless Playwright.

    Handles authentication, session persistence, and graceful error handling.

    Args:
        config: Config object with Blackboard credentials and settings.
        session_file_path: Path to session JSON file for login persistence.
    """

    def __init__(
        self,
        config: Config,
        session_file_path: str | Path | None = None,
    ) -> None:
        self._config = config
        self._session_file_path = Path(
            session_file_path
            if session_file_path is not None
            else DEFAULT_SESSION_FILE_PATH
        )
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._playwright = None  # Store playwright instance for EPIPE recovery

    # ── Public API ─────────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """Authenticate to Blackboard.

        Navigates to the Blackboard URL, fills credentials, submits,
        and waits for the dashboard to confirm success.

        Returns:
            True if login was successful, False otherwise.
        """
        logger.info("Starting login to %s", self._config.blackboard_url)

        try:
            await self._ensure_browser()

            # Navigate to the Blackboard login page
            login_url = self._config.blackboard_url.rstrip("/")
            logger.debug("Navigating to %s", login_url)
            await self._page.goto(login_url, wait_until="domcontentloaded")

            # Random delay to mimic human
            await _random_delay()

            # Dismiss cookie consent if present
            await self._dismiss_cookie_consent()

            # Check if we're already logged in (session restored)
            if await self._is_logged_in():
                logger.info("Already logged in (session restored)")
                return True

            # TRY clicking O365/SAML button (new step)
            await self._click_o365_login_button()

            # Check if Microsoft auto-authenticated and we're already on Ultra.
            # "Stay signed in" can auto-redirect back to Blackboard Ultra without
            # showing the login form.
            if "/ultra/" in self._page.url.lower():
                logger.info("Microsoft auto-login succeeded (redirected to Ultra)")
                await self._save_session()
                return True

            # Wait for Microsoft login page to appear (SAML redirect may take a few seconds)
            try:
                await self._page.wait_for_url("**login.microsoftonline.com**", timeout=15000)
                logger.info("Redirected to Microsoft login")
            except Exception:
                logger.warning(f"Not on Microsoft login, URL: {self._page.url}")

            # Check if already on Ultra after O365 click (auto-login case)
            if "/ultra/" in self._page.url.lower():
                logger.info("Already on Ultra after O365 click")
                await self._save_session()
                return True

            # Wait a moment for page to fully render
            await asyncio.sleep(2)

            # Now fill Microsoft login form
            await self._fill_login_form()

            # Submit and wait for navigation
            await self._submit_login_form()

            # Verify login success
            if await self._is_logged_in():
                await self._save_session()
                logger.info("Login successful")
                return True

            logger.warning("Login appeared to succeed but dashboard not detected")
            return False

        except Exception as exc:
            logger.error("Login failed: %s", exc)
            await self._take_screenshot("login_error")
            return False

    async def scrape_assignments(self) -> list[Assignment]:
        """Scrape all assignments from Blackboard.

        New strategy: Don't navigate away from the current page (which causes
        EPIPE crashes on heavy Ultra SPA). Instead:
        1. Try to extract data from the current page via page.evaluate()
        2. If nothing found, try navigating to Ultra calendar with crash protection
        3. Extract from captured API responses if available

        Returns:
            List of Assignment objects. Empty list if scraping fails.
        """
        logger.info("Starting assignment scrape")

        raw_assignments: list[dict] = []

        try:
            # Ensure browser and try to restore session
            await self._ensure_browser()

            if not await self._try_restore_session():
                logger.info("No valid session found, attempting login")
                if not await self.login():
                    logger.error("Login failed, cannot scrape assignments")
                    return []
                await self._save_session()

            # Step 1: Try to extract from current page first (no navigation)
            logger.debug("Attempting extraction from current page via evaluate()")
            raw_assignments = await self._extract_assignments_from_dom()

            # Step 2: If nothing found, try Ultra calendar with crash protection
            if not raw_assignments:
                logger.debug("No data from current page, trying Ultra calendar")
                if await self._try_ultra_calendar():
                    await asyncio.sleep(2)  # Wait for JS to settle
                    # Activate the deadline view to show all assignments
                    await self._activate_ultra_deadline_view()
                    await asyncio.sleep(2)  # Wait for view switch
                    raw_assignments = await self._extract_assignments_from_dom()

        except Exception as exc:
            # Check for EPIPE (browser crash)
            if "EPIPE" in str(exc) or "write EPIPE" in str(exc):
                logger.error("Browser crashed (EPIPE), attempting to relaunch")
                await self._close_browser_and_playwright()
                await self._ensure_browser()
                if not await self._try_restore_session():
                    if not await self.login():
                        return []
                    await self._save_session()
                # Try evaluate-based extraction (no navigation) after relaunch
                try:
                    raw_assignments = await self._extract_assignments_from_dom()
                except Exception as inner_exc:
                    logger.warning("Extraction failed after relaunch: %s", inner_exc)
                    raw_assignments = []
            else:
                logger.error("Scrape failed: %s", exc)
                await self._take_screenshot("scrape_error")
                return []

        if not raw_assignments:
            logger.warning("No assignments found on page")

        # Normalize to Assignment objects
        assignments = [self._normalize_assignment(raw) for raw in raw_assignments]

        # Filter out assignments without due dates (as per spec)
        assignments = [a for a in assignments if a.due_date is not None]

        logger.info("Scraped %d assignments", len(assignments))
        return assignments

    async def close(self) -> None:
        """Close the browser and clean up resources."""
        logger.debug("Closing browser")
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        self._page = None

    # ── Private: Browser Setup ─────────────────────────────────────────────────

    async def _close_browser_and_playwright(self) -> None:
        """Close browser and playwright to handle EPIPE crashes."""
        logger.debug("Closing browser and playwright for EPIPE recovery")
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    async def _ensure_browser(self) -> None:
        """Launch browser if not already running."""
        if self._browser is not None:
            return

        logger.debug("Launching browser (headless=%s)", self._config.headless)
        playwright = await async_playwright().start()
        self._playwright = playwright

        self._browser = await playwright.chromium.launch(
            headless=self._config.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent=DEFAULT_USER_AGENT,
        )

        # Apply stealth to the context
        # NOTE: Most stealth scripts interfere with Microsoft Entra ID SSO redirects,
        # causing the SAML page to enter a refresh loop instead of redirecting to
        # Microsoft login. We use minimal stealth - only what's necessary.
        stealth_ctx = stealth.Stealth(
            # Enable these so overrides don't trigger warnings
            navigator_user_agent=True,
            navigator_platform=True,
            navigator_languages=True,
            # Keep webdriver hidden - required for Microsoft SSO
            navigator_webdriver=False,
            # Disable most stealth scripts - they interfere with SSO redirects
            chrome_app=False,
            chrome_csi=False,
            chrome_load_times=False,
            chrome_runtime=False,
            hairline=False,
            iframe_content_window=False,
            media_codecs=False,
            navigator_hardware_concurrency=False,
            navigator_permissions=False,
            navigator_plugins=False,
            navigator_user_agent_data=False,
            navigator_vendor=False,
            error_prototype=False,
            sec_ch_ua=False,
            webgl_vendor=False,
        )
        await stealth_ctx.apply_stealth_async(self._context)

        self._page = await self._context.new_page()
        logger.debug("Browser launched and stealth applied")

    async def _try_restore_session(self) -> bool:
        """Try to restore session from saved session.json.

        Returns:
            True if session was restored and we're logged in, False otherwise.
        """
        session_data = _load_session(self._session_file_path)
        if session_data is None:
            return False

        try:
            # Restore cookies and storage state
            if "cookies" in session_data:
                await self._context.add_cookies(session_data["cookies"])
                logger.debug("Restored %d cookies", len(session_data["cookies"]))

            # Navigate to check if session is still valid
            await self._page.goto(
                self._config.blackboard_url, wait_until="domcontentloaded"
            )
            await _random_delay()

            if await self._is_logged_in():
                logger.info("Session restored successfully")
                return True

            logger.info("Session expired or invalid")
            return False

        except Exception as exc:
            logger.warning("Failed to restore session: %s", exc)
            return False

    async def _save_session(self) -> None:
        """Save current session (cookies, storage) to session.json."""
        try:
            cookies = await self._context.cookies()
            storage_state = await self._context.storage_state()
            session_data = {
                "cookies": cookies,
                "storage_state": storage_state,
                "saved_at": _utc_now().isoformat(),
            }
            _save_session(self._session_file_path, session_data)
            logger.info("Session saved")
        except Exception as exc:
            logger.warning("Failed to save session: %s", exc)

    # ── Private: Login Flow ───────────────────────────────────────────────────

    async def _wait_for_microsoft_login_page(self, timeout_ms: int = 15000) -> bool:
        """Wait for the Microsoft login page to load after clicking O365.

        The SAML endpoint (auth-saml/saml/login) performs a meta refresh or
        JavaScript redirect to Microsoft, but the URL doesn't change. Instead,
        we wait for Microsoft login form fields to appear.

        Returns True if Microsoft login form detected, False if timeout.
        """
        microsoft_field_selectors = [
            "input[name='loginfmt']",   # Microsoft email field
            "input[type='email']",       # Generic email field on Microsoft
            "input[autocomplete='username']",  # Microsoft username field
        ]

        start_time = asyncio.get_event_loop().time()
        check_interval = 0.5  # seconds between checks

        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout_ms:
            # Check if we're on Microsoft URL (some setups may redirect properly)
            current_url = self._page.url.lower()
            if "microsoftonline" in current_url or "login.microsoft" in current_url:
                logger.debug(
                    "Microsoft login URL detected: %s",
                    self._page.url,
                )
                return True

            # Check for Microsoft login form fields
            for selector in microsoft_field_selectors:
                try:
                    count = await self._page.locator(selector).count()
                    if count > 0:
                        logger.debug(
                            "Microsoft login field found: %s",
                            selector,
                        )
                        return True
                except Exception:
                    pass

            # Small delay before next check
            await asyncio.sleep(check_interval)

        logger.warning(
            "Timeout waiting for Microsoft login page, URL: %s",
            self._page.url,
        )
        return False

    async def _fill_login_form(self) -> None:
        """Fill in the login form with credentials.

        Detects whether we're on a Blackboard form or Microsoft Entra ID
        login page and routes to the appropriate handler.
        """
        current_url = self._page.url.lower()

        # Check for Microsoft login URLs
        if "microsoftonline" in current_url or "login.microsoft" in current_url:
            logger.debug("Detected Microsoft login page via URL")
            await self._fill_microsoft_login()
            return

        # Check if we have Microsoft login form fields (SAML redirect case)
        microsoft_field_selectors = [
            "input[name='loginfmt']",
            "input[type='email']",
            "input[autocomplete='username']",
        ]
        for selector in microsoft_field_selectors:
            try:
                count = await self._page.locator(selector).count()
                if count > 0:
                    logger.debug(
                        "Detected Microsoft login page via form field: %s",
                        selector,
                    )
                    await self._fill_microsoft_login()
                    return
            except Exception:
                pass

        # Default to Blackboard login
        logger.debug("Detected Blackboard login page")
        await self._fill_blackboard_login()

    async def _fill_blackboard_login(self) -> None:
        """Fill in the Blackboard login form with credentials."""
        logger.debug("Filling Blackboard login form")

        # Blackboard commonly uses these selectors for login forms
        # Try multiple approaches since we don't know exact structure
        selectors_to_try_user = [
            "#username",
            "#user_id",
            "input[name='username']",
            "input[name='userId']",
            "input[type='text']",
            "input[autocomplete='username']",
        ]

        selectors_to_try_pass = [
            "#password",
            "input[name='password']",
            "input[type='password']",
        ]

        # Fill username
        user_filled = False
        for selector in selectors_to_try_user:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.fill(self._config.blackboard_user)
                    logger.debug("Filled username with selector: %s", selector)
                    user_filled = True
                    break
            except Exception:
                continue

        if not user_filled:
            logger.warning("Could not find username field, trying direct fill")
            await self._page.fill("input[type='text']", self._config.blackboard_user)

        await _random_delay()

        # Fill password
        pass_filled = False
        for selector in selectors_to_try_pass:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.fill(self._config.blackboard_pass)
                    logger.debug("Filled password with selector: %s", selector)
                    pass_filled = True
                    break
            except Exception:
                continue

        if not pass_filled:
            logger.warning("Could not find password field, trying direct fill")
            await self._page.fill("input[type='password']", self._config.blackboard_pass)

    async def _fill_microsoft_login(self) -> None:
        """Fill Microsoft Entra ID login form.

        Handles the multi-step Microsoft login flow:
        1. Enter email and click Next
        2. Wait for password field to appear
        3. Enter password and click Sign in
        4. Handle "Stay signed in?" prompt (click No)
        """
        logger.info("Filling Microsoft Entra ID login form")

        # Step 1: Fill email field
        email_selectors = [
            "input[name='loginfmt']",
            "input[type='email']",
            "input[name='email']",
        ]

        email_filled = False
        for selector in email_selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.fill(self._config.blackboard_user)
                    logger.debug("Filled email with selector: %s", selector)
                    email_filled = True
                    break
            except Exception:
                continue

        if not email_filled:
            logger.warning("Could not find email field, trying fallback")
            await self._page.fill("input[type='email']", self._config.blackboard_user)

        await _random_delay()

        # Step 2: Click "Next" button
        next_selectors = [
            "#idSIButton9",
            "input[type='submit']",
            "button[type='submit']",
        ]

        next_clicked = False
        for selector in next_selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.click()
                    logger.debug("Clicked Next with selector: %s", selector)
                    next_clicked = True
                    break
            except Exception:
                continue

        if not next_clicked:
            logger.warning("Could not find Next button")

        # Step 3: Wait for password field to appear
        # Microsoft uses JavaScript transitions, so we need to wait
        logger.debug("Waiting for password field to appear")
        password_selectors = [
            "input[type='password']",
            "input[name='passwd']",
            "#passwordInput",
        ]

        password_found = False
        for selector in password_selectors:
            try:
                await self._page.wait_for_selector(
                    selector, state="attached", timeout=5000
                )
                logger.debug("Password field appeared: %s", selector)
                password_found = True
                break
            except Exception:
                continue

        if not password_found:
            logger.warning("Password field did not appear, proceeding anyway")

        await _random_delay()

        # Step 4: Fill password field
        pass_filled = False
        for selector in password_selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.fill(self._config.blackboard_pass)
                    logger.debug("Filled password with selector: %s", selector)
                    pass_filled = True
                    break
            except Exception:
                continue

        if not pass_filled:
            logger.warning("Could not find password field, trying fallback")
            await self._page.fill("input[type='password']", self._config.blackboard_pass)

        await _random_delay()

        # Step 5: Click "Sign in" button (same ID as Next: #idSIButton9)
        signin_selectors = [
            "#idSIButton9",
            "input[type='submit']",
            "button[type='submit']",
        ]

        signin_clicked = False
        for selector in signin_selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.click()
                    logger.debug("Clicked Sign in with selector: %s", selector)
                    signin_clicked = True
                    break
            except Exception:
                continue

        if not signin_clicked:
            logger.warning("Could not find Sign in button")

        await _random_delay()

        # Step 6: Handle "Stay signed in?" prompt (optional)
        # Try to detect and click "No" button
        stay_signed_in_selectors = [
            "#idBtn_Back",  # "No" button
        ]

        try:
            # Wait briefly for the prompt to appear
            for selector in stay_signed_in_selectors:
                try:
                    await self._page.wait_for_selector(
                        selector, state="attached", timeout=3000
                    )
                    el = self._page.locator(selector).first
                    if await el.count() > 0:
                        await el.click()
                        logger.debug("Clicked 'Stay signed in' No button")
                        break
                except Exception:
                    continue
        except Exception:
            # If the prompt doesn't appear, that's fine - login may have succeeded
            logger.debug("Stay signed in prompt not shown, continuing")

    async def _submit_login_form(self) -> None:
        """Submit the login form and wait for navigation."""
        logger.debug("Submitting login form")

        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button[name='login']",
            "#loginBtn",
            ".login-btn",
        ]

        for selector in submit_selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    await el.click()
                    logger.debug("Clicked submit with selector: %s", selector)
                    break
            except Exception:
                continue

        # Wait for navigation after login
        try:
            await self._page.wait_for_load_state(
                "networkidle", timeout=self._config.request_timeout_seconds * 1000
            )
        except Exception:
            logger.debug("Network idle timeout, continuing anyway")

        await _random_delay()

    async def _is_logged_in(self) -> bool:
        """Check if the current page shows a logged-in state.

        Detects if we've been redirected to a login page (not logged in)
        or if we're on the dashboard (logged in).
        """
        current_url = self._page.url.lower()

        # Check for login page indicators
        login_indicators = ["login", "signin", "auth", "credential", "microsoftonline"]
        for indicator in login_indicators:
            if indicator in current_url:
                logger.debug("Detected login page at: %s", current_url)
                return False

        # Check for dashboard/main indicators
        dashboard_indicators = [
            "dashboard",
            "home",
            "myBb",
            "courses",
            "calendar",
            "ultra",
        ]
        for indicator in dashboard_indicators:
            if indicator in current_url:
                return True

        # Check page content for user-specific elements
        try:
            # Look for common elements that appear when logged in
            selectors = [
                ".header-inner",
                ".global-nav",
                "#mygrades",
                ".course-list",
                ".bb-home-link",
            ]
            for selector in selectors:
                if await self._page.locator(selector).count() > 0:
                    return True
        except Exception:
            pass

        return False

    async def _dismiss_cookie_consent(self) -> None:
        """Click the cookie consent 'Aceptar' button if present."""
        consent_selectors = [
            "button:has-text('Aceptar')",
            "#agree-button",
            ".cookie-consent-accept",
            "[data-testid='cookie-consent-accept']",
        ]
        for selector in consent_selectors:
            try:
                btn = self._page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click()
                    await _random_delay()
                    logger.info("Cookie consent dismissed")
                    return
            except Exception:
                continue

    async def _click_o365_login_button(self) -> bool:
        """Click the 'Ingresa con tu correo @senati.pe' button if present.

        Returns True if button was clicked, False if not found.
        This button triggers a redirect to Microsoft Entra ID SAML login.
        Only clicks visible buttons to avoid clicking hidden elements.
        """
        # Try multiple selectors for the O365/SAML login button
        selectors = [
            "a.icon-o365",
            "a[href*='auth-saml/saml/login']",
            "a:has-text('@senati.pe')",
            "a:has-text('Ingresa con tu correo')",
            "a:has-text('Acceder con O365')",
            "a[href*='saml']",
            "a[href*='o365']",
            "button:has-text('O365')",
            "a:has-text('O365')",
        ]

        for selector in selectors:
            try:
                btn = self._page.locator(selector).first
                if await btn.count() > 0:
                    # Only click if element is visible (skip hidden buttons)
                    is_visible = await btn.is_visible()
                    if not is_visible:
                        logger.debug("O365 button found but not visible, skipping: %s", selector)
                        continue
                    
                    logger.info("Found O365 login button, clicking...")
                    await btn.click()
                    
                    # Don't wait for redirect here - let the caller handle it.
                    # SAML redirects may take a few seconds and waiting here
                    # could cause race conditions.
                    await asyncio.sleep(1)
                    return True
            except Exception:
                continue

        logger.debug("No visible O365 login button found")
        return False

    async def _click_nav_menu_item(self, item_text: str) -> bool:
        """Click a navigation menu item by text.

        In Blackboard Ultra, the nav menu is rendered with text labels
        like 'Calendario', 'Cursos', 'Calificaciones', etc.

        Args:
            item_text: Text of the nav item to click (e.g., 'Calendario')

        Returns:
            True if clicked successfully, False otherwise.
        """
        selectors = [
            f"a:has-text('{item_text}')",
            f"button:has-text('{item_text}')",
            f"span:has-text('{item_text}')",
            f"[role='menuitem']:has-text('{item_text}')",
            f"[data-testid*='{item_text.lower()}']",
            f"nav a:has-text('{item_text}')",
        ]

        for selector in selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0:
                    logger.info(f"Clicking nav item: {item_text}")
                    await el.click()
                    await asyncio.sleep(3)  # Wait for SPA transition
                    return True
            except Exception:
                continue

        logger.warning(f"Nav item not found: {item_text}")
        return False

    # ── Private: Navigation ───────────────────────────────────────────────────

    async def _navigate_to_assignments(self) -> None:
        """Navigate to the calendar or assignments page.

        For Ultra (SPA): tries clicking navigation menu items first,
        then falls back to URL navigation.
        For Original: uses URL paths directly.
        """
        logger.info("Navigating to assignments page")

        # Check if we're in Ultra
        if self._page is not None and "/ultra/" in self._page.url.lower():
            logger.info("Detected Ultra experience, using nav menu")
            # Try clicking nav items first (avoids EPIPE crashes from goto)
            for nav_text in ["Calendario", "Calendar", "Calificaciones"]:
                if await self._click_nav_menu_item(nav_text):
                    logger.info("Successfully navigated via Ultra nav menu: %s", nav_text)
                    await self._wait_for_page_settle()
                    # Activate the deadline view in Ultra calendar
                    await self._activate_ultra_deadline_view()
                    return
            logger.warning("Could not navigate via Ultra nav menu, falling back to goto")

        # ── Ultra paths (fallback for Ultra, direct for Original) ─────────────────
        ultra_paths: list[str] = [
            "/ultra/calendar",
            "/ultra/grades",
            "/ultra/courses",
            "/ultra",
        ]

        for path in ultra_paths:
            try:
                url = self._config.blackboard_url.rstrip("/") + path
                logger.debug("Trying Ultra path: %s", url)
                timeout = self._config.request_timeout_seconds * 1000
                await self._page.goto(url, wait_until="load", timeout=timeout)
                # Ultra is a SPA — wait for network to settle then for content
                try:
                    await self._page.wait_for_load_state(
                        "networkidle", timeout=15000
                    )
                except Exception:
                    pass
                await self._wait_for_page_settle()

                if await self._page_has_assignments():
                    logger.info("Found assignments on Ultra page: %s", url)
                    # Activate the deadline view in Ultra calendar
                    await self._activate_ultra_deadline_view()
                    return
            except Exception as exc:
                logger.debug("Ultra path %s failed: %s", path, exc)
                continue

        # ── Calendar paths ──────────────────────────────────────────────────────
        calendar_paths: list[str] = [
            "/webapps/calendar/view/",
            "/webapps/calendar/",
            "/calendar",
        ]

        for path in calendar_paths:
            try:
                url = self._config.blackboard_url.rstrip("/") + path
                logger.debug("Trying calendar path: %s", url)
                timeout = self._config.request_timeout_seconds * 1000
                await self._page.goto(url, wait_until="load", timeout=timeout)
                await self._wait_for_page_settle()

                if await self._page_has_assignments():
                    logger.info("Found assignments on calendar page: %s", url)
                    return
            except Exception as exc:
                logger.debug("Calendar path %s failed: %s", path, exc)
                continue

        # ── Assignments page paths ──────────────────────────────────────────────
        assignment_paths: list[str] = [
            "/webapps/assignment/list",
            "/webapps/assignment/",
            "/assignments",
        ]

        for path in assignment_paths:
            try:
                url = self._config.blackboard_url.rstrip("/") + path
                logger.debug("Trying assignments path: %s", url)
                timeout = self._config.request_timeout_seconds * 1000
                await self._page.goto(url, wait_until="load", timeout=timeout)
                await self._wait_for_page_settle()

                if await self._page_has_assignments():
                    logger.info("Found assignments on page: %s", url)
                    return
            except Exception as exc:
                logger.debug("Assignment path %s failed: %s", path, exc)
                continue

        # If we got here without raising, log that we're on the current page
        logger.warning(
            "Could not navigate to dedicated assignments page. "
            "Using current page: %s",
            self._page.url,
        )

    async def _page_has_assignments(self) -> bool:
        """Check if the current page appears to have assignment content."""
        for selector in (
            SELECTORS_ASSIGNMENTS
            + SELECTORS_CALENDAR
            + SELECTORS_ULTRA_ASSIGNMENT
            + SELECTORS_ULTRA_CALENDAR
        ):
            try:
                count = await self._page.locator(selector).count()
                if count > 0:
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_page_settle(self, delay: float = 3.0) -> None:
        """Wait for Ultra SPA page to settle after navigation.

        Ultra is a SPA that renders content asynchronously. A fixed delay
        is more reliable than waiting for specific selectors.
        """
        await asyncio.sleep(delay)
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    async def _wait_for_assignments_content(self) -> None:
        """Wait for assignment list to render."""
        timeout_ms = self._config.request_timeout_seconds * 1000

        all_selectors = (
            SELECTORS_ASSIGNMENTS
            + SELECTORS_CALENDAR
            + SELECTORS_ULTRA_ASSIGNMENT
            + SELECTORS_ULTRA_CALENDAR
            + ["body"]
        )

        for selector in all_selectors:
            try:
                await self._page.wait_for_selector(
                    selector, state="attached", timeout=timeout_ms
                )
                logger.debug("Found selector: %s", selector)
                return
            except Exception:
                continue

        # If no specific selector found, just wait a bit for JS to render
        logger.debug("No assignment selectors found, waiting for body")
        try:
            await self._page.wait_for_selector("body", state="attached", timeout=timeout_ms)
        except Exception as exc:
            logger.warning("Timeout waiting for page content: %s", exc)

        await _random_delay()

    # ── Private: Extraction ─────────────────────────────────────────────────────

    async def _activate_ultra_deadline_view(self) -> bool:
        """Click the 'Fechas de vencimiento' (deadline) button to show all assignments.

        In Blackboard Ultra calendar, there is a dedicated button to switch to
        the deadline/deadline view that shows ALL assignments with due dates
        in a scrollable list (deadlineContainer).

        Returns:
            True if button was clicked successfully, False otherwise.
        """
        selectors = [
            "#bb-calendar1-deadline",
            "button:has-text('Fechas de vencimiento')",
            "button[id*='deadline']",
            "[aria-controls='deadlineContainer']",
        ]

        for selector in selectors:
            try:
                btn = self._page.locator(selector).first
                if await btn.count() > 0 and await btn.is_visible():
                    logger.info(f"Clicking deadline view button: {selector}")
                    await btn.click()
                    await asyncio.sleep(4)  # Wait for AngularJS view switch
                    return True
            except Exception:
                continue

        logger.debug("Deadline view button not found")
        return False

    async def _click_ultra_calendar_day(self, day: str = "") -> bool:
        """Click a specific day in Ultra calendar to show its events.

        Args:
            day: Specific day text to click. If empty, clicks the day with items.

        Returns:
            True if a day was clicked successfully, False otherwise.
        """
        if not day:
            # Find the day with items (contains text like "1 elemento programado")
            day_selectors = [
                "td:has-text('elemento programado') button",
                "td:has-text('elemento programado')",
                "[class*='day-cell']:has-text('elemento programado')",
                "[class*='day']:has-text('1')",
                ".calendar-day:has-text('1')",
            ]
        else:
            day_selectors = [
                f"button:has-text('{day}')",
                f"td:has-text('{day}')",
                f"[class*='day']:has-text('{day}')",
            ]

        for selector in day_selectors:
            try:
                el = self._page.locator(selector).first
                if await el.count() > 0 and await el.is_visible():
                    logger.info(f"Clicking calendar day: {selector}")
                    await el.click()
                    await asyncio.sleep(2)
                    return True
            except Exception:
                continue

        logger.debug("Calendar day not found")
        return False

    async def _extract_ultra_data_via_evaluate(self) -> list[dict]:
        """Extract course/assignment data from Ultra page using JavaScript evaluation.

        Parses the Ultra deadline view format where each assignment appears as two lines:
        - Line 1: Assignment TITLE
        - Line 2: "Fecha de entrega 2: {DATE} {TIME} ({TZ}) ∙ {COURSE_ID}: {COURSE_NAME}"

        Returns:
            List of raw assignment/course data dictionaries.
        """
        if self._page is None:
            logger.warning("Cannot extract: page is None")
            return []

        logger.debug("Extracting Ultra data via page.evaluate()")
        results: list[dict] = []

        try:
            data = await self._page.evaluate("""() => {
                const results = [];

                // Get all visible text
                const bodyText = document.body?.innerText || '';
                const lines = bodyText.split('\\n').map(l => l.trim()).filter(l => l);

                // Parse the deadline format: title line followed by "Fecha de entrega" line
                for (let i = 0; i < lines.length - 1; i++) {
                    const currentLine = lines[i];
                    const nextLine = lines[i + 1];

                    // Check if next line matches the deadline format
                    if (nextLine.includes('Fecha de entrega') && nextLine.includes('\\u2219')) {
                        const title = currentLine;

                        // Parse: "Fecha de entrega 2: 2/5/26 23:59 (UTC-5) ∙ COURSE_ID: COURSE_NAME"
                        // The separator is ∙ (U+2219 bullet operator)
                        const parts = nextLine.split('\\u2219');
                        const datePart = parts[0]?.trim() || '';
                        const coursePart = parts[1]?.trim() || '';

                        // Extract date: "Fecha de entrega 2: 2/5/26 23:59 (UTC-5)"
                        const dateMatch = datePart.match(/(\\d{1,2}\\/\\d{1,2}\\/\\d{2,4})\\s+(\\d{1,2}:\\d{2})/);
                        const dueDate = dateMatch ? dateMatch[1] + ' ' + dateMatch[2] : '';

                        // Extract course: "COURSE_ID: COURSE_NAME"
                        const courseParts = coursePart.split(':');
                        const courseId = courseParts[0]?.trim() || '';
                        const courseName = courseParts.slice(1).join(':').trim() || '';

                        results.push({
                            title: title,
                            course_name: courseName,
                            course_id: courseId,
                            due_date: dueDate,
                            status: 'Pending',
                        });
                    }
                }

                return results;
            }""")

            if data:
                results.extend(data)
                logger.debug("Extracted %d items via evaluate()", len(data))
            else:
                logger.debug("No data extracted via evaluate()")

        except Exception as exc:
            logger.warning("page.evaluate() extraction failed: %s", exc)

        return results

    # Backward-compatible entry point - tests mock this, so scrape_assignments
    # calls this method which delegates to the new evaluate-based extraction.
    async def _extract_assignments_from_dom(self) -> list[dict]:
        """Extract assignments using page.evaluate() (modern Ultra approach).

        This method exists for backward compatibility with tests. The new
        implementation uses page.evaluate() to extract data from Ultra's SPA
        rather than navigating to different pages.
        """
        return await self._extract_ultra_data_via_evaluate()

    async def _try_ultra_calendar(self) -> bool:
        """Try navigating to Ultra calendar with crash protection.

        Returns:
            True if navigation succeeded and page loaded, False otherwise.
        """
        if self._page is None:
            logger.warning("Cannot navigate to Ultra calendar: page is None")
            return False
        try:
            url = self._config.blackboard_url.rstrip("/") + "/ultra/calendar"
            logger.debug("Attempting Ultra calendar navigation: %s", url)
            await self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)  # Wait for JS to settle
            logger.info("Ultra calendar page loaded successfully")
            return True
        except Exception as exc:
            logger.warning("Ultra calendar navigation failed (expected): %s", exc)
            return False

    async def _extract_calendar_events(self) -> list[dict]:
        """Extract assignments from calendar view."""
        results: list[dict] = []

        for selector in SELECTORS_CALENDAR + SELECTORS_ULTRA_CALENDAR:
            try:
                elements = self._page.locator(selector).all()
                count = await elements.count()
                if count == 0:
                    continue

                logger.debug("Found %d calendar events with selector: %s", count, selector)

                for i in range(count):
                    item = elements[i]
                    raw = await self._extract_item_data(item, i)
                    if raw:
                        results.append(raw)

                if results:
                    return results
            except Exception as exc:
                logger.warning(
                    "Selector %s failed for calendar extraction: %s", selector, exc
                )
                continue

        return results

    async def _extract_assignment_list_items(self) -> list[dict]:
        """Extract assignments from assignment list view."""
        results: list[dict] = []

        for selector in SELECTORS_ASSIGNMENTS + SELECTORS_ULTRA_ASSIGNMENT:
            try:
                elements = self._page.locator(selector).all()
                count = await elements.count()
                if count == 0:
                    continue

                logger.debug(
                    "Found %d assignment items with selector: %s", count, selector
                )

                for i in range(count):
                    item = elements[i]
                    raw = await self._extract_item_data(item, i)
                    if raw:
                        results.append(raw)

                if results:
                    return results
            except Exception as exc:
                logger.warning(
                    "Selector %s failed for list extraction: %s", selector, exc
                )
                continue

        return results

    async def _extract_item_data(self, item, index: int) -> dict | None:
        """Extract all relevant data from a single assignment element.

        Tries primary selectors first, falls back to text parsing.
        """
        raw: dict[str, Any] = {"index": index}

        # Extract title
        for selector in SELECTORS_ASSIGNMENT_TITLE:
            try:
                el = item.locator(selector).first
                if await el.count() > 0:
                    raw["title"] = (await el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Fallback: get text content
        if "title" not in raw:
            try:
                raw["title"] = (await item.inner_text())[:100].strip()
            except Exception:
                raw["title"] = f"Assignment {index}"

        # Extract course name
        for selector in SELECTORS_COURSE_NAME:
            try:
                el = item.locator(selector).first
                if await el.count() > 0:
                    raw["course_name"] = (await el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Extract due date
        for selector in SELECTORS_DUE_DATE:
            try:
                el = item.locator(selector).first
                if await el.count() > 0:
                    raw["due_date"] = (await el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Try data attributes as fallback
        try:
            attrs = await item.all_attributes()
            if "data-assignment-id" in attrs:
                raw["assignment_id"] = attrs["data-assignment-id"]
            if "data-due-date" in attrs:
                raw["due_date"] = attrs["data-due-date"]
            if "data-course-name" in attrs:
                raw["course_name"] = attrs["data-course-name"]
        except Exception:
            pass

        # Extract status
        for selector in SELECTORS_STATUS:
            try:
                el = item.locator(selector).first
                if await el.count() > 0:
                    raw["status"] = (await el.inner_text()).strip()
                    break
            except Exception:
                continue

        # Extract source URL
        try:
            link = item.locator("a").first
            if await link.count() > 0:
                raw["source_url"] = await link.get_attribute("href") or ""
        except Exception:
            raw["source_url"] = ""

        return raw if raw.get("title") else None

    def _normalize_assignment(self, raw: dict) -> Assignment:
        """Convert a raw assignment dictionary to an Assignment object.

        Handles missing fields with sensible defaults:
        - No due_date -> due_date=None (will be filtered out)
        - No assignment_id -> generated from title+course+due_date hash
        - No status -> "Unknown"
        - No source_url -> ""
        """
        # Parse due date
        due_date: datetime | None = None
        raw_due = raw.get("due_date", "")
        if raw_due:
            # Try common formats first (D/M/YY is common in Latin America)
            for fmt in ("%d/%m/%y %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M"):
                try:
                    due_date = datetime.strptime(raw_due, fmt)
                    due_date = due_date.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                # Fallback to dateutil parser for ISO formats
                try:
                    due_date = dateutil_parser.isoparse(raw_due)
                    if due_date.tzinfo is None:
                        due_date = due_date.replace(tzinfo=timezone.utc)
                except Exception as exc:
                    logger.warning("Failed to parse due date '%s': %s", raw_due, exc)

        # Generate or extract assignment ID
        # Use course_id + title for better deduplication when available
        id_raw = raw.copy()
        if raw.get("course_id") and raw.get("title"):
            id_raw["assignment_id"] = raw.get("course_id") + "|" + raw.get("title")
        assignment_id = id_raw.get("assignment_id") or _generate_assignment_id(
            id_raw, id_raw.get("index", 0)
        )

        # Get status with fallback
        status = raw.get("status", "").strip() or "Unknown"

        # Source URL
        source_url = raw.get("source_url", "")
        if source_url and not source_url.startswith(("http://", "https://")):
            source_url = self._config.blackboard_url.rstrip("/") + source_url

        return Assignment(
            assignment_id=assignment_id,
            title=raw.get("title", "Unknown Assignment")[:500],
            course_name=raw.get("course_name", "Unknown Course")[:200],
            due_date=due_date,
            status=status,
            source_url=source_url,
            scraped_at=_utc_now(),
        )

    # ── Private: Error Handling ────────────────────────────────────────────────

    async def _take_screenshot(self, prefix: str) -> None:
        """Take a screenshot on error for debugging.

        Args:
            prefix: Prefix for the filename (e.g., 'login_error', 'scrape_error').
        """
        if self._page is None:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"error_{prefix}_{timestamp}.png"

        try:
            await self._page.screenshot(path=filename, full_page=True)
            logger.info("Screenshot saved to %s", filename)
        except Exception as exc:
            logger.warning("Failed to take screenshot: %s", exc)
