#!/usr/bin/env python3
"""
PESU Academy PDF Fetcher

Interactive tool to fetch course PDFs from PESU Academy using the following workflow:
1. Get course codes from /Academy/a/g/getSubjectsCode
2. Get unit IDs for a course from /Academy/a/i/getCourse/[course_id]
3. Get classes for a unit from /Academy/a/i/getCourseClasses/[unit_id]
4. Download PDF from /Academy/s/studentProfilePESUAdmin
"""

import sys
import os
import json
import logging
import argparse
import subprocess
import shutil
import getpass
import tempfile
from typing import Optional, Dict, List, Any, Tuple
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from pathlib import Path
import hashlib
from pypdf import PdfWriter
from colorama import Fore, Style, init as colorama_init
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

# Initialize colorama for cross-platform colored output
colorama_init(autoreset=True)


# ============================================================================
# LOGGING SETUP
# ============================================================================


def setup_logger(
    name: str = "pdf_fetcher", log_file: Optional[Path] = None
) -> logging.Logger:
    """Set up a logger with console and file output."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.WARNING)  # Default to WARNING to reduce noise

    if logger.hasHandlers():
        logger.handlers.clear()

    # Console handler with colors
    console_handler = logging.StreamHandler()

    class ColoredFormatter(logging.Formatter):
        COLORS = {
            "DEBUG": Fore.CYAN,
            "INFO": Fore.GREEN,
            "WARNING": Fore.YELLOW,
            "ERROR": Fore.RED,
            "CRITICAL": Fore.RED + Style.BRIGHT,
        }

        ICONS = {
            "DEBUG": "·",
            "INFO": "✓",
            "WARNING": "?",
            "ERROR": "✗",
            "CRITICAL": "✗",
        }

        def format(self, record):
            # Make a copy to avoid modifying the original record
            log_record = logging.makeLogRecord(record.__dict__)
            levelname = log_record.levelname
            if levelname in self.COLORS:
                icon = self.ICONS.get(levelname, levelname)
                log_record.levelname = (
                    f"{self.COLORS[levelname]}{icon}{Style.RESET_ALL}"
                )
            return super().format(log_record)

    console_handler.setFormatter(ColoredFormatter("%(levelname)s - %(message)s"))
    logger.addHandler(console_handler)

    # File handler for failures (without colors) - only if log_file is explicitly provided
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.ERROR)  # Only log errors to file
        # Use plain formatter for file (no colors)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        logger.addHandler(file_handler)

    logger.propagate = False

    return logger


logger = setup_logger()  # Default logger for initialization


# ============================================================================
# COURSES INDEX MANAGEMENT
# ============================================================================


def update_courses_index(base_dir: Path) -> None:
    """
    Update the index.json file in the courses directory.
    This file lists all available course directories for the frontend API.
    """
    index_file = base_dir / "index.json"

    # Find all course directories
    course_dirs = []
    if base_dir.exists():
        for entry in sorted(base_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("course"):
                # Verify it has a summary file
                has_summary = any(
                    f.name.endswith("_course_summary.json")
                    for f in entry.iterdir()
                    if f.is_file()
                )
                if has_summary:
                    course_dirs.append(entry.name)

    # Write the index file
    index_data = {
        "courses": course_dirs,
        "updated_at": __import__("datetime").datetime.now().isoformat(),
    }

    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)

    logger.info(f"Updated courses index: {len(course_dirs)} courses in {index_file}")


# ============================================================================
# FILE CONVERSION UTILITIES
# ============================================================================


def _read_prefix(path: Path, size: int = 4096) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(size)
    except Exception:
        return b""


def _is_zip_container(path: Path) -> bool:
    # Office OpenXML formats (pptx/docx/xlsx) are ZIP containers.
    prefix = _read_prefix(path, 8)
    return prefix.startswith(b"PK")


def _is_pdf(path: Path) -> bool:
    prefix = _read_prefix(path, 8)
    return prefix.startswith(b"%PDF")


def _looks_like_html(path: Path) -> bool:
    prefix = _read_prefix(path, 512).lstrip()
    lower = prefix.lower()
    return lower.startswith(b"<!doctype html") or lower.startswith(b"<html")


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "y", "on"}


def _should_keep_repaired_artifacts() -> bool:
    return _truthy_env("PDF_FETCHER_KEEP_REPAIRED", default="0")


def _list_office_sources(unit_dir: Path) -> List[Path]:
    """List Office source docs in a unit directory.

    Excludes LibreOffice zip-repair artifacts (e.g. *_repaired.pptx).
    """
    office_exts = {".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls"}
    sources: List[Path] = []
    try:
        for p in unit_dir.iterdir():
            if not p.is_file():
                continue
            if p.suffix.lower() not in office_exts:
                continue
            if p.stem.endswith("_repaired"):
                continue
            sources.append(p)
    except Exception:
        return []
    return sources


def _validate_and_retry_office_conversions(
    unit_dir: Path,
) -> Tuple[int, int, List[str]]:
    """Ensure each Office source has a corresponding non-empty PDF.

    Returns:
      (total_sources, converted_sources, missing_filenames)
    """
    sources = _list_office_sources(unit_dir)
    missing: List[str] = []
    converted_ok = 0

    for src in sources:
        expected_pdf = src.with_suffix(".pdf")
        if (
            expected_pdf.exists()
            and expected_pdf.stat().st_size > 0
            and _is_pdf(expected_pdf)
        ):
            converted_ok += 1
            continue

        # Retry conversion once (synchronously) to avoid missing slides due to timing.
        try:
            pdf = convert_to_pdf(src)
        except Exception as e:
            logger.warning(f"Conversion retry exception for {src.name}: {e}")
            pdf = None

        if pdf and pdf.exists() and pdf.stat().st_size > 0 and _is_pdf(pdf):
            converted_ok += 1
            continue

        missing.append(src.name)

    return (len(sources), converted_ok, missing)


def _unique_existing_pdfs(paths: List[Path]) -> List[Path]:
    """Return a de-duplicated list of valid PDFs, preserving the first-seen order."""
    out: List[Path] = []
    seen: set[str] = set()
    for p in paths:
        try:
            if not p or not p.exists() or p.stat().st_size <= 0:
                continue
            if p.suffix.lower() != ".pdf":
                continue
            # Avoid merging previously merged outputs back into themselves.
            name = p.name
            if name.endswith("_merged.pdf") or name.endswith("_ESA.pdf"):
                continue
            if not _is_pdf(p):
                continue
            key = str(p.resolve())
        except Exception:
            # Fall back to string key without resolve()
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def convert_to_pdf(input_path: Path) -> Optional[Path]:
    """
    Convert Office documents (PPTX, DOCX, etc.) to PDF.
    Tries multiple methods in order of preference.
    Returns the PDF path if successful, None otherwise.
    """
    if not input_path.exists():
        logger.error(f"File not found: {input_path}")
        return None

    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return input_path  # Already a PDF

    output_path = input_path.with_suffix(".pdf")

    # Quick sanity checks to avoid misleading "zip repair" spam.
    if suffix in {".pptx", ".docx", ".xlsx"}:
        if not _is_zip_container(input_path):
            if _looks_like_html(input_path):
                logger.warning(
                    f"File does not look like a real {suffix} (looks like HTML). Likely an auth/redirect or server error: {input_path.name}"
                )
            else:
                logger.warning(
                    f"File does not look like a valid ZIP-based Office document: {input_path.name}"
                )
            return None

    # Method 1: Try soffice (LibreOffice) headless mode
    soffice_paths: List[str] = []
    env_soffice = os.getenv("PDF_FETCHER_SOFFICE_PATH")
    if env_soffice:
        soffice_paths.append(env_soffice)
    soffice_paths.extend(
        [
            shutil.which("soffice") or "",
            shutil.which("libreoffice") or "",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",  # macOS
            "/usr/bin/soffice",  # Linux
            "/usr/bin/libreoffice",  # Linux alternative
        ]
    )

    # De-duplicate while preserving order
    seen = set()
    soffice_paths = [p for p in soffice_paths if p and not (p in seen or seen.add(p))]

    # Track whether LibreOffice was available and capture stderr for diagnostics
    libreoffice_tried = False
    last_soffice_error = None

    for soffice in soffice_paths:
        if not soffice:
            continue
        if not Path(soffice).exists():
            continue
        try:
            logger.debug(f"Converting {input_path.name} to PDF using LibreOffice...")

            # Use an isolated LO profile to prevent first-run dialogs and avoid profile locks.
            lo_profile_dir = Path(tempfile.mkdtemp(prefix="goat_lo_profile_")).resolve()
            lo_profile_url = lo_profile_dir.as_uri()

            try:
                result = subprocess.run(
                    [
                        soffice,
                        "--headless",
                        "--nologo",
                        "--nofirststartwizard",
                        "--norestore",
                        f"-env:UserInstallation={lo_profile_url}",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(input_path.parent),
                        str(input_path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            finally:
                try:
                    shutil.rmtree(lo_profile_dir, ignore_errors=True)
                except Exception:
                    pass

            # Mark that we attempted LibreOffice and capture any stderr for diagnostics
            libreoffice_tried = True
            try:
                last_soffice_error = (result.stderr or "").strip()
            except Exception:
                last_soffice_error = None

            if (
                output_path.exists()
                and output_path.stat().st_size > 0
                and _is_pdf(output_path)
            ):
                logger.debug(f"✓ Converted to PDF: {output_path}")
                return output_path
            if output_path.exists() and (
                output_path.stat().st_size == 0 or not _is_pdf(output_path)
            ):
                # Avoid future false positives.
                try:
                    output_path.unlink()
                except Exception:
                    pass

            # Check if LibreOffice failed to load the file (corrupted zip)
            stderr_lower = (result.stderr or "").lower()
            if (
                "source file could not be loaded" in stderr_lower
                or "file format error" in stderr_lower
            ):
                print(
                    f"    {Fore.YELLOW}?{Style.RESET_ALL} LibreOffice failed to load file, attempting zip repair..."
                )

                # Try to repair the file using zip -FF
                repaired_path = (
                    input_path.parent / f"{input_path.stem}_repaired{suffix}"
                )
                try:
                    zip_exe = shutil.which("zip")
                    if not zip_exe:
                        print(
                            f"    {Fore.YELLOW}?{Style.RESET_ALL} zip tool not found; cannot attempt zip repair"
                        )
                        continue
                    repair_result = subprocess.run(
                        [
                            zip_exe,
                            "-FF",
                            str(input_path),
                            "--out",
                            str(repaired_path),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )

                    if (
                        repaired_path.exists()
                        and repaired_path.stat().st_size > 0
                        and _is_zip_container(repaired_path)
                    ):
                        logger.debug(f"✓ Repaired corrupted file: {repaired_path.name}")

                        # Try converting the repaired file without mutating the original.
                        logger.debug("Converting repaired file to PDF...")
                        lo_profile_dir = Path(
                            tempfile.mkdtemp(prefix="goat_lo_profile_")
                        ).resolve()
                        lo_profile_url = lo_profile_dir.as_uri()
                        try:
                            retry_result = subprocess.run(
                                [
                                    soffice,
                                    "--headless",
                                    "--nologo",
                                    "--nofirststartwizard",
                                    "--norestore",
                                    f"-env:UserInstallation={lo_profile_url}",
                                    "--convert-to",
                                    "pdf",
                                    "--outdir",
                                    str(input_path.parent),
                                    str(repaired_path),
                                ],
                                capture_output=True,
                                text=True,
                                timeout=180,
                            )
                        finally:
                            try:
                                shutil.rmtree(lo_profile_dir, ignore_errors=True)
                            except Exception:
                                pass

                        repaired_pdf = repaired_path.with_suffix(".pdf")
                        if (
                            repaired_pdf.exists()
                            and repaired_pdf.stat().st_size > 0
                            and _is_pdf(repaired_pdf)
                        ):
                            # Normalize final name to the original stem.
                            try:
                                repaired_pdf.replace(output_path)
                            except Exception:
                                pass
                            if (
                                output_path.exists()
                                and output_path.stat().st_size > 0
                                and _is_pdf(output_path)
                            ):
                                logger.debug(
                                    f"✓ Converted repaired file to PDF: {output_path}"
                                )

                                # Clean up repaired artifacts unless explicitly requested.
                                if not _should_keep_repaired_artifacts():
                                    try:
                                        repaired_path.unlink(missing_ok=True)
                                    except Exception:
                                        pass
                                    try:
                                        repaired_pdf.unlink(missing_ok=True)
                                    except Exception:
                                        pass
                                return output_path
                        else:
                            print(
                                f"    {Fore.YELLOW}?{Style.RESET_ALL} Failed to convert repaired file"
                            )
                    else:
                        print(
                            f"    {Fore.YELLOW}?{Style.RESET_ALL} Zip repair failed or produced empty file"
                        )
                except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                    logger.debug(f"Zip repair failed: {e}")
                    # Clean up if repair file was created
                    if repaired_path.exists():
                        repaired_path.unlink()

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            # Record the exception for later diagnostics
            try:
                last_soffice_error = str(e)
            except Exception:
                last_soffice_error = None
            logger.debug(f"LibreOffice conversion failed: {e}")
            continue

    # Method 2 (optional): Try macOS Keynote/Pages via osascript (for PPTX/DOCX)
    if sys.platform == "darwin" and _truthy_env("PDF_FETCHER_ALLOW_IWORK", default="0"):
        if suffix in [".pptx", ".ppt"]:
            try:
                logger.debug(f"Converting {input_path.name} to PDF using Keynote...")
                script = f"""
                tell application "Keynote"
                    set theDoc to open POSIX file "{input_path}"
                    export theDoc to POSIX file "{output_path}" as PDF
                    close theDoc
                end tell
                """
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if (
                    output_path.exists()
                    and output_path.stat().st_size > 0
                    and _is_pdf(output_path)
                ):
                    logger.debug(f"✓ Converted to PDF: {output_path}")
                    return output_path
            except Exception as e:
                logger.debug(f"Keynote conversion failed: {e}")

        elif suffix in [".docx", ".doc"]:
            try:
                logger.debug(f"Converting {input_path.name} to PDF using Pages...")
                script = f"""
                tell application "Pages"
                    set theDoc to open POSIX file "{input_path}"
                    export theDoc to POSIX file "{output_path}" as PDF
                    close theDoc
                end tell
                """
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if (
                    output_path.exists()
                    and output_path.stat().st_size > 0
                    and _is_pdf(output_path)
                ):
                    logger.debug(f"✓ Converted to PDF: {output_path}")
                    return output_path
            except Exception as e:
                logger.debug(f"Pages conversion failed: {e}")

    # Method 3: For PPTX, try python-pptx + reportlab (limited - only extracts text/images)
    # This is a fallback that won't preserve full formatting

    print(
        f"    {Fore.YELLOW}?{Style.RESET_ALL} Could not convert {input_path.name} to PDF. Keeping original format."
    )

    if libreoffice_tried:
        logger.debug(
            "LibreOffice was available but failed to convert this file; it may be corrupted or use unsupported features. Try opening/converting it manually."
        )
        if last_soffice_error:
            logger.debug(f"LibreOffice stderr: {last_soffice_error}")
    else:
        logger.debug(
            "Tip: Install LibreOffice for automatic conversion, or convert manually."
        )

    return None


# ============================================================================
# PESU ACADEMY PDF FETCHER
# ============================================================================


class AuthenticationError(Exception):
    """Raised when authentication with PESU Academy fails."""

    pass


class PDFDownloadError(Exception):
    """Raised when PDF download encounters an error."""

    pass


class PESUPDFFetcher:
    BASE_URL = "https://www.pesuacademy.com/Academy"

    def __init__(self, username: str, password: str) -> None:
        self.session = requests.Session()

        # Configure retry strategy with exponential backoff
        retry_strategy = Retry(
            total=3,  # Maximum number of retries
            backoff_factor=1,  # Wait 1s, 2s, 4s between retries
            status_forcelist=[
                429,
                500,
                502,
                503,
                504,
            ],  # Retry on these HTTP status codes
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],  # Methods to retry
        )

        # Configure connection pooling for better performance
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,  # Number of connection pools to cache
            pool_maxsize=20,  # Maximum number of connections to save in the pool
        )

        # Mount adapter for both HTTP and HTTPS
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # Enable compression for faster data transfer
        self.session.headers.update(
            {"Accept-Encoding": "gzip, deflate", "Connection": "keep-alive"}
        )

        self.username = username
        self.password = password
        # Track whether we have a valid authenticated session (cookie-based or validated)
        self._authenticated = False
        logger.debug(f"Initialized PDF fetcher for user: {username}")

    def _extract_csrf_token(self, html_content: str) -> str:
        """Extract CSRF token from HTML content using multiple heuristics:
        - hidden input named _csrf
        - meta tags like _csrf, csrf-token, csrf
        - inline JS assignment patterns
        - any UUID-like token as fallback
        Raises AuthenticationError if nothing found.
        """
        soup = BeautifulSoup(html_content, "html.parser")

        # 1) standard hidden input
        csrf_input = soup.find("input", {"name": "_csrf"})
        if csrf_input and csrf_input.get("value"):
            return csrf_input.get("value")  # type: ignore

        # 2) meta tags
        for meta_name in ("_csrf", "csrf-token", "csrf"):
            m = soup.find("meta", {"name": meta_name})
            if m and m.get("content"):
                return m.get("content")  # type: ignore

        # 3) JS inline assignment e.g. _csrf = 'uuid' or "_csrf":"uuid"
        m = re.search(
            r"_csrf['\"]?\s*[:=]\s*['\"]([0-9a-fA-F-]{8,})['\"]", html_content
        )
        if m:
            return m.group(1)

        # 4) fallback: any UUID in page
        m2 = re.search(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            html_content,
            re.I,
        )
        if m2:
            return m2.group(1)

        raise AuthenticationError("CSRF token not found in response")

    def login(self) -> None:
        """Authenticate with PESU Academy."""
        logger.debug("Starting authentication process...")

        try:
            # GET initial page (login landing)
            login_page_url = f"{self.BASE_URL}/"
            r0 = self.session.get(login_page_url, timeout=15)
            r0.raise_for_status()
            logger.debug(
                f"Login page GET status={getattr(r0, 'status_code', None)} url={getattr(r0, 'url', None)}"
            )
            logger.debug(
                f"Session cookies before login: {self.session.cookies.get_dict()}"
            )

            # Try multiple ways to obtain CSRF token (HTML > JS > cookie)
            try:
                csrf_token = self._extract_csrf_token(r0.text)
                csrf_source = "html"
            except AuthenticationError:
                csrf_token = self.session.cookies.get(
                    "XSRF-TOKEN"
                ) or self.session.cookies.get("CSRF-TOKEN")
                csrf_source = "cookie" if csrf_token else None

            if not csrf_token:
                raise AuthenticationError(
                    "Missing CSRF token (no HTML token or cookie)"
                )

            # Post login
            login_url = f"{self.BASE_URL}/j_spring_security_check"
            login_payload = {
                "j_username": self.username,
                "j_password": self.password,
                "_csrf": csrf_token,
            }

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": r0.url,
                "Origin": "https://www.pesuacademy.com",
            }

            resp = self.session.post(
                login_url,
                data=login_payload,
                headers=headers,
                allow_redirects=True,
                timeout=15,
            )
            logger.debug(
                f"Login POST status={getattr(resp, 'status_code', None)} url={getattr(resp, 'url', None)}"
            )
            cookies = self.session.cookies.get_dict()
            logger.debug(f"Session cookies after login: {cookies}")

            # If server set a session cookie, accept it as authentication proof (minimize extra requests)
            if "JSESSIONID" in cookies or "SESSION" in cookies:
                self._authenticated = True
                logger.debug(
                    "✓ Authentication successful (session cookie present — skipping additional validation)"
                )
                return

            # Otherwise, inspect the POST response body for hints that we are logged in
            body = (resp.text or "").lower()
            if (
                "studentprofile" in body
                or "logout" in body
                or "/a/0" in getattr(resp, "url", "")
            ):
                self._authenticated = True
                logger.debug(
                    "✓ Authentication successful (detected profile content in POST response)"
                )
                return

            # Detect explicit failed login markers
            if (
                "j_username" in body
                or "j_spring_security_check" in body
                or ("invalid" in body and "login" in body)
            ):
                raise AuthenticationError(
                    "Authentication failed: login page or error detected after POST"
                )

            # Ambiguous case: try https alternative once (if redirect to http), and only then validate profile to confirm
            try:
                if getattr(resp, "url", "").startswith("http://"):
                    alt = "https://" + resp.url.split("://", 1)[1]
                    alt_resp = self.session.get(alt, allow_redirects=True, timeout=15)
                    alt_body = (alt_resp.text or "").lower()
                    if alt_resp.status_code < 400 and (
                        "studentprofile" in alt_body
                        or "logout" in alt_body
                        or "/a/0" in getattr(alt_resp, "url", "")
                    ):
                        self._authenticated = True
                        logger.debug(
                            "✓ Authentication successful (https fallback detected profile)"
                        )
                        return
            except Exception:
                pass

            # Last resort: perform a single profile validation request
            self._validate_authentication()
            self._authenticated = True
            logger.debug("✓ Authentication successful (validated via profile check)")

        except requests.RequestException as e:
            raise AuthenticationError(f"Network error during authentication: {e}")
        except Exception as e:
            raise AuthenticationError(f"Authentication failed: {e}")

    def _validate_authentication(self) -> None:
        """Validate that authentication was successful using heuristics on profile page."""
        profile_url = f"{self.BASE_URL}/s/studentProfilePESU"

        try:
            profile_response = self.session.get(
                profile_url, allow_redirects=True, timeout=15
            )
            logger.debug(
                f"Profile fetch status={getattr(profile_response, 'status_code', None)} url={getattr(profile_response, 'url', None)}"
            )
            app_body = (profile_response.text or "").lower()

            if profile_response.status_code == 200:
                # Heuristics for successful login
                if (
                    "studentprofile" in app_body
                    or "logout" in app_body
                    or "/a/0" in getattr(profile_response, "url", "")
                ):
                    self._authenticated = True
                    return

                # Detect login form indicating failed auth
                if re.search(r'name=["\']j_username["\']', app_body):
                    raise AuthenticationError(
                        "Authentication failed: login form detected after login"
                    )

                raise AuthenticationError(
                    "Authentication failed: unexpected profile response"
                )

            elif profile_response.status_code in (301, 302):
                raise AuthenticationError("Authentication failed: redirected to login")

            elif profile_response.status_code == 404:
                # Sometimes servers return 404 for certain internal endpoints even when a session exists.
                cookies = self.session.cookies.get_dict()
                logger.debug(f"Profile returned 404; cookies={cookies}")
                if "JSESSIONID" in cookies or "SESSION" in cookies:
                    logger.warning(
                        "Profile returned 404 but session cookie found; assuming authentication succeeded"
                    )
                    self._authenticated = True
                    return
                raise AuthenticationError("Authentication failed: profile returned 404")

            else:
                raise AuthenticationError(
                    f"Authentication failed: HTTP {profile_response.status_code}"
                )

        except requests.RequestException as e:
            raise AuthenticationError(f"Failed to validate authentication: {e}")

    def logout(self) -> None:
        """Logout from PESU Academy."""
        try:
            logout_url = f"{self.BASE_URL}/logout"
            self.session.get(logout_url, timeout=10)
            # Clear authenticated state
            self._authenticated = False
            logger.debug("✓ Session terminated")
        except requests.RequestException as e:
            logger.warning(f"Error during logout: {e}")

    def is_authenticated(self) -> bool:
        """Return whether this fetcher currently has a validated authenticated session."""
        return bool(self._authenticated)

    # ========================================================================
    # STEP 1: Get Subject Codes
    # ========================================================================

    def get_subjects_code(self) -> Optional[List[Dict[str, Any]]]:
        """
        Step 1: Get all available course codes.
        Endpoint: /Academy/a/g/getSubjectsCode
        Returns HTML <option> tags that need to be parsed.
        """
        logger.debug("\n=== STEP 1: Fetching Subject Codes ===")

        try:
            url = f"{self.BASE_URL}/a/g/getSubjectsCode"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # Parse HTML options
            soup = BeautifulSoup(response.text, "html.parser")
            options = soup.find_all("option")

            courses = []
            for option in options:
                course_id = option.get("value")
                course_name = option.text.strip()

                if course_id and course_name:
                    # Clean the course ID - remove any quotes, escape characters, and backslashes
                    course_id = str(course_id).strip()
                    # Remove escaped quotes
                    course_id = course_id.replace('\\"', "").replace("\\'", "")
                    # Remove regular quotes
                    course_id = course_id.strip('"').strip("'")
                    # Remove any remaining backslashes
                    course_id = course_id.replace("\\", "")

                    # Extract subject code (before the dash if present)
                    subject_code = (
                        course_name.split("-")[0] if "-" in course_name else course_name
                    )

                    courses.append(
                        {
                            "id": course_id,
                            "subjectCode": subject_code,
                            "subjectName": course_name,
                        }
                    )

            if courses:
                logger.debug(f"✓ Found {len(courses)} courses")
                return courses
            else:
                logger.warning("No courses found in response")
                return None

        except requests.RequestException as e:
            logger.error(f"Network error fetching subjects: {e}")
            logger.error(f"URL: {url}")
            return None
        except Exception as e:
            logger.error(f"Error parsing subjects: {e}")
            logger.error(f"URL: {url}")
            return None

    # ========================================================================
    # STEP 2: Get Course Units
    # ========================================================================

    def get_course_units(self, course_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Step 2: Get units for a specific course.
        Endpoint: /Academy/a/i/getCourse/[course_id]
        Returns HTML <option> tags that need to be parsed.
        """
        logger.debug(f"\n=== STEP 2: Fetching Units for Course {course_id} ===")

        try:
            url = f"{self.BASE_URL}/a/i/getCourse/{course_id}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # The response is JSON-encoded HTML string
            html_content = (
                response.json()
                if response.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
                else response.text
            )

            # Parse HTML options
            soup = BeautifulSoup(html_content, "html.parser")
            options = soup.find_all("option")

            units = []
            for option in options:
                unit_id = option.get("value")
                unit_name = option.text.strip()

                if unit_id and unit_name:
                    # Clean the unit ID
                    unit_id = (
                        str(unit_id).strip().replace("\\", "").strip('"').strip("'")
                    )

                    # Extract unit number if present
                    unit_number = (
                        unit_name.split(":")[0].strip()
                        if ":" in unit_name
                        else unit_name
                    )

                    units.append(
                        {"id": unit_id, "unit": unit_name, "unitNumber": unit_number}
                    )

            if units:
                logger.debug(f"✓ Found {len(units)} units")
                return units
            else:
                logger.warning("No units found in response")
                return None

        except requests.RequestException as e:
            logger.error(f"Network error fetching course units: {e}")
            logger.error(f"Course ID: {course_id}")
            logger.error(f"URL: {url}")
            return None
        except Exception as e:
            logger.error(f"Failed to parse course units: {e}")
            logger.error(f"Course ID: {course_id}")
            logger.error(f"URL: {url}")
            return None

    # ========================================================================
    # STEP 3: Get Unit Classes
    # ========================================================================

    def get_unit_classes(self, unit_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Step 3: Get classes for a specific unit.
        Endpoint: /Academy/a/i/getCourseClasses/[unit_id]
        Returns HTML <option> tags that need to be parsed.
        """
        logger.debug(f"\n=== STEP 3: Fetching Classes for Unit {unit_id} ===")

        try:
            url = f"{self.BASE_URL}/a/i/getCourseClasses/{unit_id}"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # The response is JSON-encoded HTML string
            html_content = (
                response.json()
                if response.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
                else response.text
            )

            # Parse HTML options
            soup = BeautifulSoup(html_content, "html.parser")
            options = soup.find_all("option")

            classes = []
            for option in options:
                class_id = option.get("value")
                class_name = option.text.strip()

                if class_id and class_name:
                    # Clean the class ID
                    class_id = (
                        str(class_id).strip().replace("\\", "").strip('"').strip("'")
                    )

                    classes.append(
                        {
                            "id": class_id,
                            "className": class_name,
                            "classType": "Lecture",  # Default since not provided
                        }
                    )

            if classes:
                logger.debug(f"✓ Found {len(classes)} classes")
                return classes
            else:
                logger.warning("No classes found in response")
                return None

        except requests.RequestException as e:
            logger.error(f"Network error fetching unit classes: {e}")
            logger.error(f"Unit ID: {unit_id}")
            logger.error(f"URL: {url}")
            return None
        except Exception as e:
            logger.error(f"Failed to parse unit classes: {e}")
            logger.error(f"Unit ID: {unit_id}")
            logger.error(f"URL: {url}")
            return None

    # ========================================================================
    # STEP 4: Download File (PDF, PPTX, DOCX, etc.)
    # ========================================================================

    def download_pdf(
        self,
        course_id: str,
        class_id: str,
        output_path: Optional[Path] = None,
        class_name: Optional[str] = None,
        existing_summary: Optional[Dict] = None,
        content_type_id: str = "2",
    ) -> List[Dict]:
        """
        Step 4: Download file(s) for a specific class (PDF, PPTX, DOCX, etc.).
        Returns a list of successfully downloaded file paths.
        If multiple files are found, all are downloaded with meaningful names based on link text.
        Endpoint: /Academy/s/studentProfilePESUAdmin with specific parameters

        content_type_id: "2" for Slides, "3" for Notes
        """
        logger.debug(f"\n=== STEP 4: Downloading File ===")
        logger.debug(f"Course ID: {course_id}, Class ID: {class_id}")

        try:
            url = f"{self.BASE_URL}/s/studentProfilePESUAdmin"
            params = {
                "url": "studentProfilePESUAdmin",
                "controllerMode": "6403",
                "actionType": "60",
                "selectedData": course_id,
                "id": content_type_id,
                "unitid": class_id,
            }

            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()

            # Check if response is actually a PDF or HTML
            content_type = response.headers.get("Content-Type", "")

            if "application/pdf" in content_type:
                # Direct PDF download
                if output_path is None:
                    output_path = Path(f"course_{course_id}_class_{class_id}.pdf")

                with open(output_path, "wb") as f:
                    f.write(response.content)

                file_size = output_path.stat().st_size

                # Check if file is empty (0 bytes) and skip it
                if file_size == 0:
                    logger.warning("Downloaded PDF is empty (0 bytes), skipping")
                    output_path.unlink()  # Delete the 0-byte file
                    return []

                logger.debug(
                    f"✓ PDF downloaded successfully: {output_path} ({file_size:,} bytes)"
                )
                return [output_path]

            elif "text/html" in content_type:
                # Parse HTML to find download links (PDF, PPTX, DOCX, etc.)
                logger.debug("Response is HTML, parsing for download links...")
                soup = BeautifulSoup(response.text, "html.parser")

                # Look for links with onclick that call loadIframe, downloadslidecoursedoc, or downloadcoursedoc
                download_links = []
                import re

                # Search ALL elements with onclick attribute (not just <a> tags)
                for element in soup.find_all(onclick=True):
                    onclick = element.get("onclick", "")
                    text = element.text.strip()

                    # Check for downloadcoursedoc pattern (e.g., onclick="downloadcoursedoc('ID')")
                    if "downloadcoursedoc" in onclick:
                        # Extract ID from downloadcoursedoc('ID') pattern
                        match = re.search(r"downloadcoursedoc\('([^']+)'", onclick)
                        if match:
                            doc_id = match.group(1)
                            download_url = f"/Academy/s/referenceMeterials/downloadcoursedoc/{doc_id}"
                            full_url = f"https://www.pesuacademy.com{download_url}"

                            download_links.append(
                                {
                                    "text": text or "Course Document",
                                    "href": download_url,
                                    "full_url": full_url,
                                }
                            )
                            continue

                    # Check onclick for downloadslidecoursedoc pattern
                    if "downloadslidecoursedoc" in onclick:
                        # Extract the URL from onclick="loadIframe('/Academy/a/referenceMeterials/downloadslidecoursedoc/ID')"
                        match = re.search(r"loadIframe\('([^']+)'", onclick)
                        if match:
                            download_url = match.group(1)
                            # Remove the #view parameters
                            download_url = download_url.split("#")[0]

                            # Build full URL - if it starts with /Academy, use base domain only
                            if download_url.startswith("/Academy"):
                                full_url = f"https://www.pesuacademy.com{download_url}"
                            elif download_url.startswith("http"):
                                full_url = download_url
                            else:
                                full_url = f"{self.BASE_URL}/{download_url.lstrip('/')}"

                            download_links.append(
                                {
                                    "text": text or "Course Document",
                                    "href": download_url,
                                    "full_url": full_url,
                                }
                            )

                # Also check <a> tags for href-based download links
                for link in soup.find_all("a"):
                    href = link.get("href", "")
                    text = link.text.strip()

                    # Check for direct href links to downloadslidecoursedoc
                    if "downloadslidecoursedoc" in href:
                        download_url = href
                        download_url = download_url.split("#")[0]

                        if download_url.startswith("/Academy"):
                            full_url = f"https://www.pesuacademy.com{download_url}"
                        elif download_url.startswith("http"):
                            full_url = download_url
                        else:
                            full_url = f"{self.BASE_URL}/{download_url.lstrip('/')}"

                        download_links.append(
                            {
                                "text": text or "Course Document",
                                "href": download_url,
                                "full_url": full_url,
                            }
                        )

                    # Also check for any links with referenceMeterials or downloads
                    elif "referenceMeterials" in href or "download" in href.lower():
                        download_url = href
                        download_url = download_url.split("#")[0]

                        if download_url.startswith("/Academy"):
                            full_url = f"https://www.pesuacademy.com{download_url}"
                        elif download_url.startswith("http"):
                            full_url = download_url
                        else:
                            full_url = f"{self.BASE_URL}/{download_url.lstrip('/')}"

                        download_links.append(
                            {
                                "text": text or "Course Document",
                                "href": download_url,
                                "full_url": full_url,
                            }
                        )

                if not download_links:
                    logger.debug("No download links found in the response")
                    return []

                # Remove duplicates by URL while preserving order
                seen_urls = set()
                unique_links = []
                for link in download_links:
                    if link["full_url"] not in seen_urls:
                        seen_urls.add(link["full_url"])
                        unique_links.append(link)
                download_links = unique_links

                # Download ALL links (not just the first one)
                if len(download_links) > 1:
                    logger.debug(
                        f"Found {len(download_links)} download options, downloading all"
                    )
                    # Log each link for debugging
                    for idx, link in enumerate(download_links):
                        logger.debug(
                            f"  [{idx + 1}] {link['text'][:50]} -> {link['full_url']}"
                        )
                else:
                    logger.debug(
                        f"Found 1 download option: {download_links[0]['text']}"
                    )

                downloaded_files = []

                # Download each file
                for link_idx, selected_link in enumerate(download_links):
                    logger.debug(
                        f"Downloading [{link_idx + 1}/{len(download_links)}]: {selected_link['text']}"
                    )

                    # Download the selected file with proper headers (especially Referer)
                    logger.debug(f"Downloading from: {selected_link['full_url']}")
                    try:
                        # Add Referer header - required for downloadslidecoursedoc URLs
                        headers = {
                            "Referer": "https://www.pesuacademy.com/Academy/s/studentProfilePESU",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        }
                        file_response = self.session.get(
                            selected_link["full_url"],
                            stream=True,
                            headers=headers,
                            timeout=60,
                        )
                        file_response.raise_for_status()
                    except requests.RequestException as e:
                        logger.error(f"Failed to download link {link_idx + 1}: {e}")
                        continue

                    # Try to get filename from Content-Disposition header first
                    content_disposition = file_response.headers.get(
                        "Content-Disposition", ""
                    )
                    original_filename = None
                    if "filename=" in content_disposition:
                        import re

                        # Try to extract filename from Content-Disposition
                        match = re.search(
                            r'filename[*]?=["\']?(?:UTF-8\'\')?([^"\';\n]+)',
                            content_disposition,
                        )
                        if match:
                            original_filename = match.group(1).strip()
                            logger.debug(
                                f"Original filename from server: {original_filename}"
                            )

                    # Determine file extension from content-type or original filename
                    file_content_type = file_response.headers.get("Content-Type", "")
                    extension = ".pdf"  # Default

                    # First try to get extension from original filename
                    if original_filename and "." in original_filename:
                        extension = "." + original_filename.rsplit(".", 1)[-1].lower()
                    # Otherwise use content-type
                    elif "application/pdf" in file_content_type:
                        extension = ".pdf"
                    elif (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                        in file_content_type
                    ):
                        extension = ".pptx"
                    elif "application/vnd.ms-powerpoint" in file_content_type:
                        extension = ".ppt"
                    elif (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        in file_content_type
                    ):
                        extension = ".docx"
                    elif "application/msword" in file_content_type:
                        extension = ".doc"
                    elif (
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        in file_content_type
                    ):
                        extension = ".xlsx"
                    elif "application/vnd.ms-excel" in file_content_type:
                        extension = ".xls"
                    elif "application/octet-stream" in file_content_type:
                        # Generic binary - try to detect from magic bytes
                        # Read first few bytes to detect file type
                        first_chunk = next(
                            file_response.iter_content(chunk_size=8), b""
                        )
                        if first_chunk.startswith(b"PK"):
                            # ZIP-based format (pptx, docx, xlsx)
                            # Need more context, default to pptx for presentations
                            extension = ".pptx"
                            logger.debug(
                                "Detected ZIP-based format (likely Office document)"
                            )
                        elif first_chunk.startswith(b"%PDF"):
                            extension = ".pdf"

                        # Put the chunk back by creating a new iterator
                        def iter_with_first_chunk():
                            yield first_chunk
                            yield from file_response.iter_content(chunk_size=8192)

                        content_iterator = iter_with_first_chunk()
                    else:
                        content_iterator = None

                    if "content_iterator" not in locals() or content_iterator is None:
                        content_iterator = file_response.iter_content(chunk_size=8192)

                    logger.debug(f"Detected file type: {extension}")

                    # Determine output path with meaningful names for multiple files
                    if output_path is None:
                        # Try to get filename from URL or use default
                        filename = selected_link["href"].split("/")[-1]
                        if "." not in filename:
                            filename = f"class_{class_id}{extension}"
                        current_output_path = Path(filename)
                    else:
                        current_output_path = output_path
                        # If output path was provided but has wrong extension, update it
                        if current_output_path.suffix == ".pdf" and extension != ".pdf":
                            current_output_path = current_output_path.with_suffix(
                                extension
                            )

                    # For multiple files, use class_name + link_text for meaningful names
                    if len(download_links) > 1:
                        # Get the base prefix from output path (e.g., "05_" from "05_Kafka.pdf")
                        prefix = ""
                        if output_path:
                            # Extract numeric prefix like "05_"
                            stem = current_output_path.stem
                            import re

                            match = re.match(r"^(\d+)_", stem)
                            if match:
                                prefix = match.group(1) + "_"

                        # Use link text for the name, cleaning it up
                        link_text = selected_link["text"]
                        # Clean the link text: remove special chars, limit length
                        safe_link_text = "".join(
                            c if c.isalnum() or c in (" ", "-", "_") else "_"
                            for c in link_text
                        ).strip()
                        safe_link_text = "_".join(safe_link_text.split())[
                            :80
                        ]  # Join spaces with underscore, limit length

                        # Combine: prefix + class_name (if available) + link_text
                        if class_name:
                            # Extract class name without prefix
                            class_base = (
                                class_name.split(".", 1)[-1]
                                if "." in class_name
                                else class_name
                            )
                            class_base = "".join(
                                c if c.isalnum() or c in (" ", "-", "_") else "_"
                                for c in class_base
                            ).strip()
                            class_base = "_".join(class_base.split())[:50]
                            filename = (
                                f"{prefix}{class_base}_{safe_link_text}{extension}"
                            )
                        else:
                            filename = f"{prefix}{safe_link_text}{extension}"

                        current_output_path = current_output_path.parent / filename

                    # Save file (skip download if already present with checksum)
                    try:
                        # If file already exists on disk, try to skip re-downloading. Prefer verifying against
                        # the existing course summary JSON (if supplied) by comparing SHA; otherwise skip by existence.
                        if current_output_path.exists():
                            try:
                                existing_sha = compute_file_sha256(current_output_path)
                            except Exception:
                                existing_sha = None

                            skipped = False
                            if existing_summary is not None:
                                # Look up filename+sha in existing summary
                                def _match_in_summary(summary, filename, sha):
                                    if not summary:
                                        return False
                                    for u in summary.get("units", []):
                                        for c in u.get("classes", []):
                                            for f in c.get("files", []):
                                                if (
                                                    f.get("filename") == filename
                                                    and f.get("sha256") == sha
                                                ):
                                                    return True
                                    return False

                                if existing_sha and _match_in_summary(
                                    existing_summary,
                                    current_output_path.name,
                                    existing_sha,
                                ):
                                    logger.debug(
                                        f"Skipping download, file exists and checksum matches summary: {current_output_path.name}"
                                    )
                                    downloaded_files.append(
                                        {
                                            "path": current_output_path,
                                            "original_sha": None,
                                            "extension": current_output_path.suffix.lstrip(
                                                "."
                                            ),
                                        }
                                    )
                                    skipped = True

                            if not skipped:
                                # No existing summary match; skip re-download only if file is present and non-empty
                                if current_output_path.stat().st_size > 0:
                                    logger.debug(
                                        f"Skipping download, file already exists: {current_output_path.name}"
                                    )
                                    downloaded_files.append(
                                        {
                                            "path": current_output_path,
                                            "original_sha": None,
                                            "extension": current_output_path.suffix.lstrip(
                                                "."
                                            ),
                                        }
                                    )
                                    continue

                        with open(current_output_path, "wb") as f:
                            for chunk in content_iterator:
                                f.write(chunk)

                        file_size = current_output_path.stat().st_size

                        # Check if file is empty (0 bytes) and skip it
                        if file_size == 0:
                            logger.debug(
                                f"Skipping empty file (0 bytes): {current_output_path.name}"
                            )
                            logger.debug(f"Link text: {selected_link['text']}")
                            logger.debug(f"URL: {selected_link['full_url']}")
                            current_output_path.unlink()  # Delete the 0-byte file
                            continue

                        # Compute checksum of the original downloaded file (before conversion)
                        original_sha = None
                        try:
                            original_sha = compute_file_sha256(current_output_path)
                        except Exception as e:
                            logger.warning(
                                f"Failed to compute checksum for {current_output_path.name}: {e}"
                            )

                        logger.debug(
                            f"✓ File downloaded successfully: {current_output_path.name} ({file_size:,} bytes)"
                        )

                        # Defer conversions until after all downloads finish for the unit (helps with stability and concurrency)
                        if extension != ".pdf":
                            # We recorded original file and its checksum (above). Conversion will run in a separate pass after all downloads.
                            logger.debug(
                                f"Deferred conversion for {current_output_path.name} to post-download phase"
                            )
                        else:
                            # For direct PDFs, compute PDF checksum (we won't write sidecars)
                            try:
                                pdf_sha = compute_file_sha256(current_output_path)
                                # pdf_sha will be stored in JSON later
                            except Exception as e:
                                logger.warning(
                                    f"Failed to compute checksum for PDF {current_output_path.name}: {e}"
                                )

                        # Append structured file info (path + original sha) so callers can persist checksums into JSON later
                        downloaded_files.append(
                            {
                                "path": current_output_path,
                                "original_sha": original_sha,
                                "extension": extension.lstrip("."),
                            }
                        )

                    except IOError as e:
                        logger.error(f"Failed to save file {link_idx + 1}: {e}")
                        continue

                return downloaded_files

            else:
                logger.error(f"Unexpected content type: {content_type}")
                return []

        except requests.RequestException as e:
            logger.error(f"Network error downloading file: {e}")
            logger.error(f"Course ID: {course_id}")
            logger.error(f"Class ID: {class_id}")
            logger.error(f"Output Path: {output_path}")
            return []
        except IOError as e:
            logger.error(f"File I/O error during download: {e}")
            logger.error(f"Course ID: {course_id}")
            logger.error(f"Class ID: {class_id}")
            logger.error(f"Output Path: {output_path}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error during download: {e}")
            logger.error(f"Course ID: {course_id}")
            logger.error(f"Class ID: {class_id}")
            logger.error(f"Output Path: {output_path}")
            return []


# ============================================================================
# INTERACTIVE CLI
# ============================================================================


def print_table(items: List[Dict[str, Any]], keys: List[str], title: str = "") -> None:
    """Pretty print a list of dictionaries as a table."""
    if not items:
        print("No items to display")
        return

    if title:
        print(f"\n{title}")
        print("=" * len(title))

    # Calculate column widths
    widths = {}
    for key in keys:
        widths[key] = len(key)
        for item in items:
            value = str(item.get(key, ""))
            widths[key] = max(widths[key], len(value))

    # Print header
    header = " | ".join(key.ljust(widths[key]) for key in keys)
    print(f"\n{header}")
    print("-" * len(header))

    # Print rows
    for item in items:
        row = " | ".join(str(item.get(key, "")).ljust(widths[key]) for key in keys)
        print(row)

    print()


def sha_sidecar_path(path: Path) -> Path:
    """Return the sidecar .sha256 path for a given file."""
    return path.with_name(path.name + ".sha256")


def orig_sidecar_path(path: Path) -> Path:
    """Return the sidecar .orig.sha256 path (stores original file sha for converted PDFs)."""
    return path.with_name(path.name + ".orig.sha256")


def compute_file_sha256(path: Path) -> str:
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sidecar_sha(path: Path) -> Optional[str]:
    """Read the sidecar SHA256 for a file if present."""
    side = sha_sidecar_path(path)
    if side.exists():
        return side.read_text().strip()
    return None


def write_sidecar_sha(path: Path, sha_hex: str) -> None:
    """Write the sidecar SHA256 for a file."""
    side = sha_sidecar_path(path)
    side.write_text(sha_hex)


def compute_combined_sha(pdf_paths: List[Path]) -> str:
    """Compute a combined SHA over a list of PDFs using their individual sidecars or file contents.

    The resulting SHA is stable for the same set & order of files.
    """
    pieces: List[str] = []
    for p in sorted(pdf_paths, key=lambda x: x.name):
        if p.suffix.lower() != ".pdf":
            continue
        sha = read_sidecar_sha(p)
        if not sha and p.exists():
            sha = compute_file_sha256(p)
        sha_part = sha if sha else "missing"
        pieces.append(f"{p.name}:{sha_part}")
    combined = hashlib.sha256(";".join(pieces).encode()).hexdigest()
    return combined


def merge_pdfs(pdf_files: List[Path], output_path: Path) -> bool:
    """Merge multiple PDF files into a single PDF. Skips non-PDF files.

    The function computes a combined SHA of the inputs; if an existing merged
    file has the same SHA the merge is skipped to avoid unnecessary work. No
    `.sha256` sidecar files are written — checksums are stored in the course
    summary JSON instead.
    """
    try:
        # Compute combined SHA of inputs and skip merge if nothing changed
        combined = compute_combined_sha(pdf_files)
        if output_path.exists():
            try:
                existing = compute_file_sha256(output_path)
                if existing == combined:
                    logger.debug(
                        f"Skipping merge; merged PDF up-to-date: {output_path.name}"
                    )
                    return True
            except Exception:
                # If we cannot compute existing hash, proceed to re-merge
                pass

        merger = PdfWriter()
        pdf_count = 0

        for pdf_file in pdf_files:
            # Only merge PDF files
            if pdf_file.suffix.lower() != ".pdf":
                logger.debug(f"Skipping non-PDF file: {pdf_file.name}")
                continue

            if pdf_file.exists() and pdf_file.stat().st_size > 0:
                try:
                    merger.append(str(pdf_file))
                    pdf_count += 1
                except Exception as e:
                    logger.warning(f"Failed to add {pdf_file.name} to merged PDF: {e}")
                    continue

        if len(merger.pages) == 0:
            logger.warning(
                f"No valid PDFs to merge (found {len(pdf_files)} files, {pdf_count} were PDFs)"
            )
            return False

        with open(output_path, "wb") as f:
            merger.write(f)

        merger.close()

        # Compute merged file SHA (do not write a sidecar file; store in JSON instead)
        try:
            merged_sha = compute_file_sha256(output_path)
        except Exception:
            merged_sha = None

        logger.debug(
            f"✓ Merged {pdf_count} PDFs into {output_path.name} ({output_path.stat().st_size:,} bytes)"
        )
        if merged_sha:
            logger.debug(f"Merged PDF sha256: {merged_sha}")
        return True

    except Exception as e:
        logger.error(f"Failed to merge PDFs: {e}")
        logger.error(f"Output Path: {output_path}")
        logger.error(f"Number of files: {len(pdf_files)}")
        return False


def generate_esa_pdf(
    course_dir: Path, course_prefix: str, content_suffix: str = ""
) -> bool:
    """Generate ESA PDF by combining all 4 unit merged PDFs."""
    try:
        # Find all unit merged PDFs
        merged_pdfs = []
        for unit_num in range(1, 5):
            # Look for unit directories
            unit_dirs = list(course_dir.glob(f"unit_{unit_num}_*"))
            if not unit_dirs:
                continue

            # Look for merged PDF in this unit directory
            unit_dir = unit_dirs[0]
            merged_pdf_pattern = (
                f"{course_prefix}_u{unit_num}{content_suffix}_merged.pdf"
            )
            merged_pdf_files = list(unit_dir.glob(merged_pdf_pattern))

            if merged_pdf_files:
                merged_pdfs.append((unit_num, merged_pdf_files[0]))

        if len(merged_pdfs) == 0:
            logger.warning(
                f"No merged PDFs found for ESA generation in {course_dir.name}"
            )
            return False

        # Sort by unit number
        merged_pdfs.sort(key=lambda x: x[0])

        # Create ESA PDF
        esa_pdf_path = course_dir / f"{course_prefix}{content_suffix}_ESA.pdf"

        # Compute combined SHA of unit merged PDFs and skip if ESA is up-to-date
        combined = compute_combined_sha([pdf for _, pdf in merged_pdfs])
        if esa_pdf_path.exists():
            try:
                existing = compute_file_sha256(esa_pdf_path)
                if existing == combined:
                    print(f"  {Fore.GREEN}✓{Style.RESET_ALL} (up-to-date)")
                    logger.debug(
                        f"Skipping ESA generation; ESA PDF up-to-date: {esa_pdf_path.name}"
                    )
                    return True
            except Exception:
                pass

        print(
            f"  {Fore.BLUE}Creating ESA PDF from {len(merged_pdfs)} unit(s)...{Style.RESET_ALL} ",
            end="",
            flush=True,
        )

        merger = PdfWriter()
        for unit_num, pdf_path in merged_pdfs:
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                try:
                    merger.append(str(pdf_path))
                except Exception as e:
                    logger.warning(f"Failed to add unit {unit_num} to ESA PDF: {e}")
                    continue

        if len(merger.pages) == 0:
            print(f"{Fore.RED}✗{Style.RESET_ALL}")
            logger.warning("No valid PDFs to merge for ESA")
            return False

        with open(esa_pdf_path, "wb") as f:
            merger.write(f)

        merger.close()
        print(f"{Fore.GREEN}✓{Style.RESET_ALL}")
        # Compute and log ESA PDF SHA (do not write sidecar)
        try:
            esa_sha = compute_file_sha256(esa_pdf_path)
        except Exception:
            esa_sha = None

        logger.info(
            f"✓ Created ESA PDF: {esa_pdf_path.name} ({esa_pdf_path.stat().st_size:,} bytes)"
        )
        if esa_sha:
            logger.info(f"ESA PDF sha256: {esa_sha}")
        return True

    except Exception as e:
        print(f"{Fore.RED}✗{Style.RESET_ALL}")
        logger.error(f"Failed to generate ESA PDF: {e}")
        logger.error(f"Course Directory: {course_dir}")
        logger.error(f"Course Prefix: {course_prefix}")
        return False


def batch_download_all(
    fetcher: PESUPDFFetcher,
    course_id: str,
    course_name: str,
    course_dir: Path,
    unit_filter: Optional[List[int]] = None,
    class_filter: Optional[List[int]] = None,
    skip_merge: bool = False,
    max_workers: Optional[int] = None,
    content_type_id: str = "2",
) -> None:
    """
    Download all PDFs for units in a course automatically.

    Args:
        fetcher: The PDF fetcher instance
        course_id: Course ID to download
        course_name: Course name
        course_dir: Directory to save files
        unit_filter: List of unit numbers to download (None = all units)
        class_filter: List of class numbers to download per unit (None = all classes)
        skip_merge: If True, don't merge PDFs into single file per unit
        content_type_id: "2" for Slides, "3" for Notes
    """
    content_label = "Notes" if content_type_id == "3" else "Slides"
    # Suffix for filenames when downloading notes
    content_suffix = "_notes" if content_type_id == "3" else ""
    print(
        f"{Fore.YELLOW}{Style.BRIGHT}Batch Download - {content_label}{Style.RESET_ALL}"
    )

    # Setup course-specific failure log using same naming as directory
    import re

    subject_match = next(
        (s for s in fetcher.get_subjects_code() or [] if s["id"] == course_id), None
    )
    subject_code = subject_match["subjectCode"] if subject_match else course_id

    clean_name = (
        course_name.split("-", 1)[-1].strip() if "-" in course_name else course_name
    )
    safe_name = "".join(
        c if c.isalnum() or c in (" ", "-") else "-" for c in clean_name
    ).strip()
    safe_name = "-".join(safe_name.split())

    course_prefix = f"{subject_code}-{safe_name}"
    course_log_file = course_dir / f"{course_prefix}{content_suffix}_failures.log"

    # Reconfigure logger with course-specific log file
    global logger
    logger = setup_logger("pdf_fetcher", course_log_file)

    # Get all units
    units = fetcher.get_course_units(course_id)

    # Load existing summary (if available) so we can avoid re-downloading/remaring unchanged files
    existing_summary = None
    summary_file_path = (
        course_dir / f"{course_prefix}{content_suffix}_course_summary.json"
    )
    if summary_file_path.exists():
        try:
            with open(summary_file_path, "r", encoding="utf-8") as sf:
                existing_summary = json.load(sf)
        except Exception:
            existing_summary = None
    if not units:
        print(f"\n{Fore.RED}✗{Style.RESET_ALL} Failed to fetch units.")
        return

    # Filter units if specified
    if unit_filter:
        filtered_units = [
            (idx, u) for idx, u in enumerate(units, 1) if idx in unit_filter
        ]
        if not filtered_units:
            print(
                f"\n{Fore.RED}✗{Style.RESET_ALL} No units found matching filter: {unit_filter}"
            )
            return
        print(
            f"{Fore.MAGENTA}Found {len(units)} total units. Downloading {len(filtered_units)} unit(s): {unit_filter}{Style.RESET_ALL}"
        )
        units_to_process = filtered_units
    else:
        print(
            f"{Fore.MAGENTA}Found {len(units)} units. Starting download...{Style.RESET_ALL}"
        )
        units_to_process = list(enumerate(units, 1))

    total_downloaded = 0
    total_failed = 0

    # Prepare summary data
    import datetime

    summary = {
        "course_id": course_id,
        "course_name": course_name,
        "download_date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_units": len(units),
        "filtered_units": len(units_to_process) if unit_filter else None,
        "units": [],
        "failure_log": course_log_file.name,
    }

    for unit_idx, unit in units_to_process:
        unit_id = unit["id"]
        unit_name = unit["unit"]

        print(
            f"\n{Fore.CYAN}[{unit_idx}/{len(units)}]{Style.RESET_ALL} {Fore.WHITE}{Style.BRIGHT}{unit_name}{Style.RESET_ALL}"
        )

        # Get classes
        classes = fetcher.get_unit_classes(unit_id)
        if not classes:
            print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL} No classes found")
            summary["units"].append(
                {
                    "unit_number": unit_idx,
                    "unit_id": unit_id,
                    "unit_name": unit_name,
                    "classes": [],
                    "total_files": 0,
                    "failed_files": 0,
                    "merged_pdf": None,
                }
            )
            continue

        # Create unit directory - extract title after colon or use full name
        # Format: "Unit 1: Introduction" -> "Introduction" or "IoT  Analytics, Security & Privacy:" -> "IoT-Analytics-Security-Privacy"
        unit_title = (
            unit_name.split(":", 1)[-1].strip() if ":" in unit_name else unit_name
        )
        # Remove trailing colon if present
        unit_title = unit_title.rstrip(":")
        safe_unit_title = "".join(
            c if c.isalnum() or c in (" ", "-") else "-" for c in unit_title
        ).strip()
        safe_unit_title = "-".join(
            safe_unit_title.split()
        )  # Replace spaces with hyphens
        # Remove any trailing hyphens and empty strings
        safe_unit_title = safe_unit_title.strip("-")
        if not safe_unit_title:  # Fallback if title is empty
            safe_unit_title = f"Unit-{unit_idx}"
        unit_dir = course_dir / f"unit_{unit_idx}_{safe_unit_title}"
        unit_dir.mkdir(exist_ok=True)

        # Track downloaded PDFs for this unit
        unit_pdfs = []
        unit_summary = {
            "unit_number": unit_idx,
            "unit_id": unit_id,
            "unit_name": unit_name,
            "unit_directory": unit_dir.name,
            "classes": [],
            "total_files": 0,
            "failed_files": 0,
            "merged_pdf": None,
        }

        # Filter classes if specified
        classes_to_download = classes
        if class_filter:
            classes_to_download = [
                cls for idx, cls in enumerate(classes, 1) if idx in class_filter
            ]
            if not classes_to_download:
                print(
                    f"  {Fore.YELLOW}⚠ No classes match filter: {class_filter}{Style.RESET_ALL}"
                )
                summary["units"].append(unit_summary)
                continue
            print(f"  Filtering: {len(classes_to_download)}/{len(classes)} classes")

        # Helper function for parallel downloads
        def download_class(class_data: Tuple[int, Dict]) -> Tuple[Dict, List[Path]]:
            """Download a single class and return class info and downloaded files."""
            class_idx, cls = class_data
            class_id = cls["id"]
            class_name = cls["className"]

            # Safe filename with zero-padded numbering
            safe_name = "".join(
                c for c in class_name if c.isalnum() or c in (" ", "-", "_")
            ).strip()[:50]
            padded_num = str(class_idx).zfill(2)  # 01, 02, 03, etc.
            output_path = unit_dir / f"{padded_num}_{safe_name}{content_suffix}.pdf"

            class_info = {
                "class_number": class_idx,
                "class_id": class_id,
                "class_name": class_name,
                "files": [],
                "status": "failed",
            }

            # download_pdf now returns a list of downloaded file paths
            downloaded_files = fetcher.download_pdf(
                course_id,
                class_id,
                output_path,
                class_name,
                existing_summary=existing_summary,
                content_type_id=content_type_id,
            )

            return class_info, downloaded_files

        # Download classes in parallel with progress bar
        # Determine concurrency: CLI flag (max_workers param) overrides env var PDF_FETCHER_MAX_WORKERS or MAX_WORKERS; default 5
        workers = None
        if max_workers is not None:
            try:
                workers = int(max_workers)
                if workers <= 0:
                    raise ValueError("must be > 0")
            except Exception:
                logger.warning(
                    f"Invalid --max-workers='{max_workers}', falling back to env var or default"
                )
                workers = None

        if workers is None:
            _max_workers_env = os.getenv("PDF_FETCHER_MAX_WORKERS") or os.getenv(
                "MAX_WORKERS"
            )
            try:
                workers = int(_max_workers_env) if _max_workers_env is not None else 10
                if workers <= 0:
                    raise ValueError("must be > 0")
            except Exception:
                logger.warning(
                    f"Invalid PDF_FETCHER_MAX_WORKERS='{_max_workers_env}', falling back to 10"
                )
                workers = 10

        logger.debug(f"Using max_workers={workers} for concurrent downloads")

        # Configure non-blocking conversion executor (so conversion runs concurrently with other downloads)
        _conv_env = os.getenv("PDF_FETCHER_CONVERT_WORKERS")
        try:
            conv_workers = int(_conv_env) if _conv_env is not None else 2
            if conv_workers <= 0:
                raise ValueError("must be > 0")
        except Exception:
            logger.warning(
                f"Invalid PDF_FETCHER_CONVERT_WORKERS='{_conv_env}', falling back to 2"
            )
            conv_workers = 2

        logger.debug(f"Using convert_workers={conv_workers} for background conversions")

        conversion_executor = ThreadPoolExecutor(max_workers=conv_workers)
        conversion_futures = []
        import threading

        class_lock = threading.Lock()

        def _convert_and_attach(
            src_path: Path, cls_info: Dict[str, Any], orig_sha: Optional[str]
        ):
            """Convert src_path to PDF and attach metadata to cls_info in a thread-safe way."""
            try:
                # If PDF already exists (same stem), avoid re-conversion
                possible_pdf = src_path.with_suffix(".pdf")
                if possible_pdf.exists() and possible_pdf.stat().st_size > 0:
                    # Compute sha and attach if not already present
                    try:
                        pdf_sha = compute_file_sha256(possible_pdf)
                    except Exception:
                        pdf_sha = None

                    with class_lock:
                        cls_info["files"].append(
                            {
                                "filename": possible_pdf.name,
                                "file_size": possible_pdf.stat().st_size,
                                "file_type": "pdf",
                                "sha256": pdf_sha,
                                "orig_sha256": orig_sha,
                            }
                        )
                    return possible_pdf

                pdf_path = convert_to_pdf(src_path)
                if (
                    not pdf_path
                    or not pdf_path.exists()
                    or pdf_path.stat().st_size == 0
                ):
                    print(
                        f"    {Fore.YELLOW}?{Style.RESET_ALL} Conversion failed or produced empty PDF for {src_path.name}"
                    )
                    return None

                try:
                    pdf_sha = compute_file_sha256(pdf_path)
                except Exception:
                    pdf_sha = None

                with class_lock:
                    cls_info["files"].append(
                        {
                            "filename": pdf_path.name,
                            "file_size": pdf_path.stat().st_size,
                            "file_type": "pdf",
                            "sha256": pdf_sha,
                            "orig_sha256": orig_sha,
                        }
                    )

                logger.debug(f"✓ Converted {src_path.name} -> {pdf_path.name}")
                return pdf_path

            except Exception as e:
                logger.warning(f"Conversion exception for {src_path.name}: {e}")
                return None

        with tqdm(
            total=len(classes_to_download),
            desc="  Downloading",
            unit="file",
            leave=False,
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        ) as pbar:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                # Submit all download tasks
                future_to_class = {
                    executor.submit(download_class, (idx, cls)): (idx, cls)
                    for idx, cls in enumerate(classes_to_download, 1)
                }

                # Process completed downloads as they finish
                for future in as_completed(future_to_class):
                    try:
                        class_info, downloaded_files = future.result()

                        class_name = class_info["class_name"]
                        pbar.set_postfix_str(
                            f"{class_name[:40]}..."
                            if len(class_name) > 40
                            else class_name
                        )

                        if downloaded_files:
                            total_downloaded += len(downloaded_files)
                            # downloaded_files is now a list of dicts {'path', 'original_sha', 'extension'}
                            # extend unit_pdfs with the Path objects for later conversion/merge
                            for item in downloaded_files:
                                # Support both legacy Path items and new dict items
                                if isinstance(item, dict):
                                    path = item["path"]
                                else:
                                    path = item
                                unit_pdfs.append(path)

                            # Update class info with all downloaded files and store SHA in-memory (no sidecar files written)
                            for item in downloaded_files:
                                if isinstance(item, dict):
                                    path = item["path"]
                                    extension = item.get(
                                        "extension", path.suffix.lstrip(".")
                                    )
                                    orig_sha = item.get("original_sha")
                                else:
                                    path = item
                                    extension = path.suffix.lstrip(".")
                                    orig_sha = None

                                if path.exists():
                                    try:
                                        file_sha = compute_file_sha256(path)
                                    except Exception:
                                        file_sha = None
                                    class_info["files"].append(
                                        {
                                            "filename": path.name,
                                            "file_size": path.stat().st_size,
                                            "file_type": extension,
                                            "sha256": file_sha,
                                            # original_sha only applies to the downloaded original (for converted files)
                                            "orig_sha256": orig_sha,
                                        }
                                    )

                            class_info["status"] = "success"
                            unit_summary["total_files"] += len(downloaded_files)

                            file_count_msg = (
                                f" ({len(downloaded_files)} files)"
                                if len(downloaded_files) > 1
                                else ""
                            )
                            pbar.write(
                                f"    {Fore.GREEN}✓{Style.RESET_ALL} {class_name}{file_count_msg}"
                            )
                        else:
                            # logger.error(
                            #     f"FAILURE [batch_download]: Failed to download class"
                            # )
                            # logger.error(f"  Unit: {unit_name}")
                            # logger.error(f"  Class: {class_name}")
                            # logger.error(f"  Class ID: {class_info['class_id']}")
                            total_failed += 1
                            unit_summary["failed_files"] += 1
                            pbar.write(
                                f"    {Fore.YELLOW}?{Style.RESET_ALL} {class_name}"
                            )

                        unit_summary["classes"].append(class_info)
                        pbar.update(1)

                        # Schedule non-blocking conversions for any non-PDF files that were just downloaded
                        for item in downloaded_files:
                            if isinstance(item, dict):
                                path = item["path"]
                                extension = item.get(
                                    "extension", path.suffix.lstrip(".")
                                )
                                orig_sha = item.get("original_sha")
                            else:
                                path = item
                                extension = path.suffix.lstrip(".")
                                orig_sha = None

                            if extension != "pdf":
                                try:
                                    fut = conversion_executor.submit(
                                        _convert_and_attach, path, class_info, orig_sha
                                    )
                                    conversion_futures.append(fut)
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to schedule conversion for {path.name}: {e}"
                                    )

                    except Exception as e:
                        idx, cls = future_to_class[future]
                        logger.error(f"Exception downloading class {idx}: {e}")
                        total_failed += 1
                        unit_summary["failed_files"] += 1
                        pbar.update(1)

        # Wait for background conversions to finish and collect converted PDFs
        converted_pdfs: List[Path] = []
        if conversion_futures:
            converted_count = 0
            for fut in as_completed(conversion_futures):
                try:
                    res = fut.result()
                    if res:
                        converted_pdfs.append(res)
                        converted_count += 1
                except Exception as e:
                    logger.warning(f"Background conversion task failed: {e}")
            logger.debug(
                f"Completed background conversions: {converted_count} converted"
            )

        # Always shutdown conversion executor for this unit (ensure threads cleaned up)
        try:
            conversion_executor.shutdown(wait=True)
        except Exception:
            pass

        # Ensure we didn't miss any PPTX->PDF conversions.
        # This guarantees: for every Office source in the unit dir, a corresponding valid PDF exists
        # or we explicitly report it as missing.
        try:
            total_sources, converted_sources, missing_sources = (
                _validate_and_retry_office_conversions(unit_dir)
            )
        except Exception as e:
            total_sources, converted_sources, missing_sources = (0, 0, [])
            logger.warning(f"Conversion validation failed for unit '{unit_name}': {e}")

        unit_summary["office_sources"] = total_sources
        unit_summary["office_converted"] = converted_sources
        unit_summary["office_missing"] = missing_sources

        if total_sources:
            if missing_sources:
                logger.error(
                    f"Missing PDF conversions in {unit_dir.name}: {len(missing_sources)}/{total_sources} source file(s) still have no PDF"
                )
                for name in missing_sources:
                    logger.error(f"  Missing PDF for: {name}")
                print(
                    f"  {Fore.YELLOW}Office conversion:{Style.RESET_ALL} {converted_sources}/{total_sources} (missing {len(missing_sources)})"
                )
            else:
                print(
                    f"  {Fore.GREEN}Office conversion:{Style.RESET_ALL} {converted_sources}/{total_sources}"
                )

        # Build a list of PDFs to merge: include existing PDFs, newly converted PDFs, and any PDFs
        # produced by the post-validation retry pass above. Then de-duplicate and validate.
        candidate_pdfs = [
            f for f in unit_pdfs if f.suffix.lower() == ".pdf"
        ] + converted_pdfs

        # Add any PDFs that exist on disk for Office sources (covers retry conversions)
        office_pdf_candidates: List[Path] = []
        try:
            for src in _list_office_sources(unit_dir):
                office_pdf_candidates.append(src.with_suffix(".pdf"))
        except Exception:
            office_pdf_candidates = []

        candidate_pdfs.extend(office_pdf_candidates)
        pdf_files_only = _unique_existing_pdfs(candidate_pdfs)

        # Helpful breakdown to explain "Conversion X/Y" vs "Merging N PDFs"
        converted_office_pdfs = _unique_existing_pdfs(office_pdf_candidates)
        converted_office_count = len(converted_office_pdfs)
        non_office_pdf_count = max(0, len(pdf_files_only) - converted_office_count)

        # Sort PDFs by filename to ensure correct order (01_, 02_, etc.)
        pdf_files_only.sort(key=lambda x: x.name)

        # Merge PDFs for this unit (non-PDF files will be skipped) unless --no-merge flag is set
        if pdf_files_only and not skip_merge:
            print(
                f"  {Fore.BLUE}Merging {len(pdf_files_only)} PDFs{Style.RESET_ALL} ({converted_office_count} converted + {non_office_pdf_count} already-PDF)... ",
                end="",
                flush=True,
            )
            merged_pdf_path = (
                unit_dir / f"{course_prefix}_u{unit_idx}{content_suffix}_merged.pdf"
            )
            if merge_pdfs(pdf_files_only, merged_pdf_path):
                print(f"{Fore.GREEN}✓{Style.RESET_ALL}")
                unit_summary["merged_pdf"] = merged_pdf_path.name
                try:
                    merged_sha = compute_file_sha256(merged_pdf_path)
                    unit_summary["merged_pdf_sha"] = merged_sha
                except Exception:
                    unit_summary["merged_pdf_sha"] = None
            else:
                print(f"{Fore.RED}✗{Style.RESET_ALL}")
        elif pdf_files_only and skip_merge:
            logger.info(f"  Skipping merge (--no-merge flag set)")
        else:
            logger.debug(
                f"  No PDF files to merge for this unit (downloaded {len(unit_pdfs)} non-PDF files)"
            )

        # Sort classes by class_number to ensure proper order in JSON and merged PDF
        unit_summary["classes"].sort(key=lambda x: x["class_number"])

        summary["units"].append(unit_summary)

    # Add summary totals
    summary["total_downloaded"] = total_downloaded
    summary["total_failed"] = total_failed

    # Save summary to JSON file with course prefix
    summary_file = course_dir / f"{course_prefix}{content_suffix}_course_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Generate ESA PDF (combining all 4 units) unless skip_merge is set
    if not skip_merge:
        print()
        esa_created = generate_esa_pdf(
            course_dir, course_prefix, content_suffix=content_suffix
        )
        if esa_created:
            esa_pdf_path = course_dir / f"{course_prefix}{content_suffix}_ESA.pdf"
            try:
                summary["esa_pdf"] = esa_pdf_path.name
                summary["esa_pdf_sha"] = compute_file_sha256(esa_pdf_path)
            except Exception:
                summary["esa_pdf_sha"] = None

    # Update the courses index.json for the frontend API
    update_courses_index(course_dir.parent)

    print(
        f"{Fore.GREEN}{Style.BRIGHT}Complete!{Style.RESET_ALL} Downloaded: {Fore.GREEN}{total_downloaded}{Style.RESET_ALL}, Failed: {Fore.RED}{total_failed}{Style.RESET_ALL}"
    )
    print(f"{Fore.CYAN}Location:{Style.RESET_ALL} {course_dir}")
    print(f"{Fore.CYAN}Summary saved to:{Style.RESET_ALL} {summary_file}")
    if total_failed > 0:
        print(f"{Fore.YELLOW}Failure log:{Style.RESET_ALL} {course_log_file}")


def interactive_mode(
    fetcher: PESUPDFFetcher,
    course_code: Optional[str] = None,
    unit_filter: Optional[List[int]] = None,
    class_filter: Optional[List[int]] = None,
    list_units: bool = False,
    skip_merge: bool = False,
    output_dir: Optional[str] = None,
    max_workers: Optional[int] = None,
    content_type_id: str = "2",
) -> None:
    """Run the PDF fetcher in interactive mode with optional filters."""

    try:
        # Step 1: Get subject codes
        subjects = fetcher.get_subjects_code()
        if not subjects:
            print(f"\n{Fore.RED}✗{Style.RESET_ALL} Failed to fetch subjects. Exiting.")
            return

        # Save all subjects to JSON file
        subjects_file = Path("courses.json")
        with open(subjects_file, "w", encoding="utf-8") as f:
            json.dump(subjects, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved all {len(subjects)} courses to {subjects_file}")

        # If course_code provided via CLI flag, use it directly
        if course_code:
            # Check if it's a regex pattern (used internally when --pattern is passed)
            if course_code.startswith("PATTERN:"):
                import re

                pattern = course_code[8:]  # Remove "PATTERN:" prefix
                try:
                    regex = re.compile(pattern, re.IGNORECASE)
                    matches = [
                        s
                        for s in subjects
                        if regex.search(s["subjectCode"])
                        or regex.search(s.get("subjectName", ""))
                    ]

                    if not matches:
                        print(
                            f"\n{Fore.RED}✗{Style.RESET_ALL} No courses found matching pattern '{pattern}'"
                        )
                        return

                    print(
                        f"\n✓ Found {len(matches)} course(s) matching pattern '{pattern}'"
                    )
                    for match in matches:
                        print(f"  - {match['subjectCode']}: {match['subjectName']}")

                    # Download all matching courses
                    for idx, match in enumerate(matches, 1):
                        print(f"\n{'=' * 60}")
                        print(
                            f"[{idx}/{len(matches)}] Processing: {match['subjectCode']}"
                        )
                        print(f"{'=' * 60}")

                        course_id = match["id"]
                        course_name = match["subjectName"]
                        subject_code = match["subjectCode"]

                        # Create course directory
                        clean_name = (
                            course_name.split("-", 1)[-1].strip()
                            if "-" in course_name
                            else course_name
                        )
                        safe_name = "".join(
                            c if c.isalnum() or c in (" ", "-") else "-"
                            for c in clean_name
                        ).strip()
                        safe_name = "-".join(safe_name.split())

                        base_dir_env = output_dir or os.getenv(
                            "BASE_DIR", "frontend/public/courses"
                        )
                        base_dir = Path(__file__).parent / base_dir_env
                        base_dir.mkdir(parents=True, exist_ok=True)
                        course_dir = (
                            base_dir / f"course{course_id}_{subject_code}-{safe_name}"
                        )
                        course_dir.mkdir(exist_ok=True)

                        # Download all materials for this course
                        batch_download_all(
                            fetcher,
                            course_id,
                            course_name,
                            course_dir,
                            unit_filter,
                            class_filter,
                            skip_merge,
                            max_workers=max_workers,
                            content_type_id=content_type_id,
                        )

                    return

                except re.error as e:
                    print(f"\n{Fore.RED}✗{Style.RESET_ALL} Invalid regex pattern: {e}")
                    return

            # Try to match by ID first, then by subject code
            course_match = next(
                (
                    s
                    for s in subjects
                    if s["id"] == course_code or s["subjectCode"] == course_code
                ),
                None,
            )
            if not course_match:
                print(
                    f"\n{Fore.RED}✗{Style.RESET_ALL} Course code '{course_code}' not found."
                )
                print(
                    f"Hint: Use course ID or subject code (e.g., '20975' or 'UE23CS342AA3')"
                )
                return
            course_id = course_match["id"]
            course_name = course_match["subjectName"]
            print(
                f"\n{Fore.GREEN}✓{Style.RESET_ALL} Using course: {course_name} (ID: {course_id})"
            )
        else:
            # Use fzf for fuzzy finding
            print(f"\nLaunching fzf to search through {len(subjects)} courses...")

            try:
                import subprocess

                # Prepare fzf input with format: "ID | Code | Name"
                fzf_input = "\n".join(
                    [
                        f"{s['id']} | {s['subjectCode']} | {s['subjectName']}"
                        for s in subjects
                    ]
                )

                # Run fzf
                result = subprocess.run(
                    ["fzf", "--prompt=Select course: ", "--height=40%", "--reverse"],
                    input=fzf_input,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                )

                if result.returncode != 0:
                    print("No course selected. Exiting.")
                    return

                # Extract course ID from selected line
                selected = result.stdout.strip()
                if not selected:
                    print("No course selected. Exiting.")
                    return

                # Parse the selected line and extract course ID
                parts = selected.split(" | ")
                course_id = parts[0].strip()
                course_name = " | ".join(parts[1:]) if len(parts) > 1 else selected
                print(f"\n{Fore.GREEN}✓{Style.RESET_ALL} Selected: {course_name}")

            except FileNotFoundError:
                logger.error("fzf not found. Please install fzf: brew install fzf")
                print("\nFalling back to manual search...")
                print("Enter course ID or search term: ", end="")
                search_term = input().strip()

                if search_term.lower() == "q":
                    print("Exiting...")
                    return

                if search_term.isdigit():
                    course_id = search_term
                    # Find the course name
                    course_match = next(
                        (s for s in subjects if s["id"] == course_id), None
                    )
                    course_name = (
                        course_match["subjectName"]
                        if course_match
                        else f"Course {course_id}"
                    )
                else:
                    # Fallback fuzzy search
                    matches = [
                        s
                        for s in subjects
                        if search_term.lower() in s.get("subjectName", "").lower()
                    ]
                    if not matches:
                        print(
                            f"\n{Fore.RED}✗{Style.RESET_ALL} No courses found matching '{search_term}'"
                        )
                        return
                    if len(matches) == 1:
                        course_id = matches[0]["id"]
                        course_name = matches[0]["subjectName"]
                        print(
                            f"\n{Fore.GREEN}✓{Style.RESET_ALL} Selected: {course_name}"
                        )
                    else:
                        print_table(
                            matches[:20],
                            ["id", "subjectCode", "subjectName"],
                            f"Found {len(matches)} matches",
                        )
                        print("\nEnter course ID: ", end="")
                        course_id = input().strip()
                        course_match = next(
                            (s for s in subjects if s["id"] == course_id), None
                        )
                        course_name = (
                            course_match["subjectName"]
                            if course_match
                            else f"Course {course_id}"
                        )

        if course_id.lower() == "q":
            print("Exiting...")
            return

        # Create course directory with format: course{id}_{subjectCode-Course-Name}
        subject_match = next((s for s in subjects if s["id"] == course_id), None)
        subject_code = subject_match["subjectCode"] if subject_match else course_id

        # Clean course name (remove subject code prefix if present)
        clean_name = (
            course_name.split("-", 1)[-1].strip() if "-" in course_name else course_name
        )
        safe_name = "".join(
            c if c.isalnum() or c in (" ", "-") else "-" for c in clean_name
        ).strip()
        safe_name = "-".join(safe_name.split())  # Replace spaces with hyphens

        # If --list-units flag is set, just list units and exit
        if list_units:
            units = fetcher.get_course_units(course_id)
            if units:
                print(f"\n{Fore.CYAN}Units for {course_name}:{Style.RESET_ALL}")
                for idx, unit in enumerate(units, 1):
                    print(f"  {idx}. {unit['unit']}")
            else:
                print(f"\n{Fore.RED}✗{Style.RESET_ALL} Failed to fetch units.")
            return

        # Load base directory from environment variable or use default
        base_dir_env = output_dir or os.getenv("BASE_DIR", "frontend/public/courses")
        base_dir = Path(__file__).parent / base_dir_env
        base_dir.mkdir(parents=True, exist_ok=True)
        course_dir = base_dir / f"course{course_id}_{subject_code}-{safe_name}"
        course_dir.mkdir(exist_ok=True)

        # If course_code was provided via CLI, automatically download all materials
        if course_code:
            batch_download_all(
                fetcher,
                course_id,
                course_name,
                course_dir,
                unit_filter,
                class_filter,
                skip_merge,
                max_workers=max_workers,
                content_type_id=content_type_id,
            )
            return

        # Ask for download mode only in interactive mode
        print("\nDownload mode:")
        print("  1. Download ALL materials (all units, all classes)")
        print("  2. Interactive (select specific unit/class)")
        print("\nChoice (1/2, default=1): ", end="")
        mode = input().strip() or "1"

        if mode == "1":
            batch_download_all(
                fetcher,
                course_id,
                course_name,
                course_dir,
                max_workers=max_workers,
                content_type_id=content_type_id,
            )
            return

        # Continue with interactive mode...
        # Step 2: Get course units
        units = fetcher.get_course_units(course_id)
        if not units:
            print(
                f"\n{Fore.RED}✗{Style.RESET_ALL} Failed to fetch units for this course. Exiting."
            )
            return

        # Save units to JSON file
        units_file = course_dir / "units.json"
        with open(units_file, "w", encoding="utf-8") as f:
            json.dump(units, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved {len(units)} units to {units_file}")

        # Display units
        print_table(
            units, ["id", "unit", "unitNumber"], f"Units for Course {course_id}"
        )

        # Use fzf for unit selection
        print(f"\nLaunching fzf to select unit...")

        try:
            import subprocess

            # Prepare fzf input
            fzf_input = "\n".join([f"{u['id']} | {u['unit']}" for u in units])

            result = subprocess.run(
                ["fzf", "--prompt=Select unit: ", "--height=40%", "--reverse"],
                input=fzf_input,
                text=True,
                encoding="utf-8",
                capture_output=True,
            )

            if result.returncode != 0:
                print("No unit selected. Exiting.")
                return

            selected = result.stdout.strip()
            if not selected:
                print("No unit selected. Exiting.")
                return

            unit_id = selected.split(" | ")[0].strip()
            print(
                f"\n✓ Selected unit: {selected.split(' | ')[1] if len(selected.split(' | ')) > 1 else unit_id}"
            )

        except FileNotFoundError:
            # Fallback to manual input
            print("\nEnter unit ID to continue (or 'q' to quit): ", end="")
            unit_id = input().strip()

            if unit_id.lower() == "q":
                print("Exiting...")
                return

        # Step 3: Get unit classes
        classes = fetcher.get_unit_classes(unit_id)
        if not classes:
            print(
                f"\n{Fore.RED}✗{Style.RESET_ALL} Failed to fetch classes for this unit. Exiting."
            )
            return

        # Save classes to JSON file
        classes_file = course_dir / f"unit_{unit_id}_classes.json"
        with open(classes_file, "w", encoding="utf-8") as f:
            json.dump(classes, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved {len(classes)} classes to {classes_file}")

        # Display classes
        display_keys = [
            k
            for k in ["id", "className", "classType", "date", "topic"]
            if k in (classes[0] if classes else {})
        ]
        print_table(classes, display_keys, f"Classes for Unit {unit_id}")

        # Use fzf for class selection
        print(f"\nLaunching fzf to select class...")

        try:
            import subprocess

            # Prepare fzf input
            fzf_input = "\n".join([f"{c['id']} | {c['className']}" for c in classes])

            result = subprocess.run(
                ["fzf", "--prompt=Select class: ", "--height=40%", "--reverse"],
                input=fzf_input,
                text=True,
                encoding="utf-8",
                capture_output=True,
            )

            if result.returncode != 0:
                print("No class selected. Exiting.")
                return

            selected = result.stdout.strip()
            if not selected:
                print("No class selected. Exiting.")
                return

            class_id = selected.split(" | ")[0].strip()
            print(
                f"\n✓ Selected class: {selected.split(' | ')[1] if len(selected.split(' | ')) > 1 else class_id}"
            )

        except FileNotFoundError:
            # Fallback to manual input
            print("\nEnter class ID to download PDF (or 'q' to quit): ", end="")
            class_id = input().strip()

            if class_id.lower() == "q":
                print("Exiting...")
                return

        # Step 4: Download PDF
        print("\nEnter output filename (press Enter for default): ", end="")
        filename = input().strip()

        if filename:
            output_path = Path(filename)
        else:
            # Default: save in course directory
            output_path = course_dir / f"class_{class_id}.pdf"

        success = fetcher.download_pdf(course_id, class_id, output_path)

        if success:
            print(
                f"\n{Fore.GREEN}✓{Style.RESET_ALL} PDF download completed successfully!"
            )
        else:
            print(f"\n{Fore.RED}✗{Style.RESET_ALL} PDF download failed.")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")


def main():
    """Main entry point."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="PESU Academy PDF Fetcher - Download course materials automatically",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download all materials for a course
  python main.py -c UE23CS343AB2
  
  # Download all courses matching a pattern (regex)
  python main.py -p "UE23CS3.*"
  python main.py -p "UE23CS341.*"
  python main.py -p ".*BlockChain"
  
  # Download specific units only
  python main.py -c UE23CS343AB2 -u 1,3
  
  # Download specific unit with class range
  python main.py -c UE23CS343AB2 -u 2 --class-range 1-5
  
  # List available units without downloading
  python main.py -c UE23CS343AB2 --list-units
  
  # Skip merge (keep individual PDFs only)
  python main.py -c UE23CS343AB2 --no-merge
        """,
    )
    parser.add_argument(
        "-c",
        "--course-code",
        action="append",
        type=str,
        help="Course code/ID to download directly (can be given multiple times to process several courses; skips interactive selection)",
    )
    parser.add_argument(
        "-p",
        "--pattern",
        type=str,
        help="Regex pattern to match course codes (e.g., 'UE23CS3.*' or 'UE23CS341.*'). Downloads all matching courses.",
    )
    parser.add_argument(
        "-u",
        "--units",
        type=str,
        help="Comma-separated unit numbers to download (e.g., '1,3,4' or '1-3'). Downloads all units if not specified.",
    )
    parser.add_argument(
        "--class-range",
        type=str,
        help="Range of class numbers to download within each unit (e.g., '1-5' or '3,5,7')",
    )
    parser.add_argument(
        "--list-units",
        action="store_true",
        help="List all units for the course without downloading",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Skip merging PDFs into a single file per unit",
    )
    parser.add_argument(
        "-n",
        "--notes",
        action="store_true",
        help="Download Notes instead of Slides",
    )
    parser.add_argument(
        "--update-index",
        action="store_true",
        help="Only update the courses index.json file (no download)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        help="Override concurrency for parallel downloads (overrides PDF_FETCHER_MAX_WORKERS env var).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (DEBUG) for detailed per-file messages",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Custom output directory (default: frontend/public/courses)",
    )
    args = parser.parse_args()

    # Respect --verbose flag (enable debug logs) if requested
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    # Handle --update-index flag (no login required)
    if args.update_index:
        from dotenv import load_dotenv

        load_dotenv()
        base_dir_env = os.getenv("BASE_DIR", "frontend/public/courses")
        base_dir = Path(__file__).parent / base_dir_env
        if base_dir.exists():
            update_courses_index(base_dir)
            print(f"✓ Updated index.json in {base_dir}")
        else:
            print(
                f"{Fore.RED}✗{Style.RESET_ALL} Courses directory not found: {base_dir}"
            )
        return

    # Parse unit filter (e.g., "1,3,4" or "1-3")
    unit_filter = None
    if args.units:
        unit_filter = []
        for part in args.units.split(","):
            if "-" in part:
                start, end = map(int, part.split("-"))
                unit_filter.extend(range(start, end + 1))
            else:
                unit_filter.append(int(part))

    # Parse class filter (e.g., "1-5" or "3,5,7")
    class_filter = None
    if args.class_range:
        class_filter = []
        for part in args.class_range.split(","):
            if "-" in part:
                start, end = map(int, part.split("-"))
                class_filter.extend(range(start, end + 1))
            else:
                class_filter.append(int(part))

    # Determine content type: Notes (id=3) or Slides (id=2, default)
    content_type_id = "3" if args.notes else "2"

    print(f"{Fore.CYAN}{Style.BRIGHT}  \\_()_/{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  (o.o){Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  /) (\\  {Style.RESET_ALL}")
    print(f"{Fore.GREEN}{Style.BRIGHT}  goat-scraper{Style.RESET_ALL}")
    print()

    # Load credentials from .env file
    try:
        from dotenv import load_dotenv

        load_dotenv()

        username = os.getenv("PESU_USERNAME")
        password = os.getenv("PESU_PASSWORD")

        if username and password:
            logger.info(f"Loaded credentials from .env for user: {username}")
        else:
            # Fallback to manual input
            print("Enter your PESU Academy credentials:")
            username = input("Username (SRN): ").strip()
            password = getpass.getpass("Password: ").strip()
    except ImportError:
        # If dotenv not available, ask for manual input
        print("Enter your PESU Academy credentials:")
        username = input("Username (SRN): ").strip()
        password = getpass.getpass("Password: ").strip()

    if not username or not password:
        print(f"{Fore.RED}✗{Style.RESET_ALL} Username and password are required.")
        sys.exit(1)

    # Create fetcher and login
    fetcher = PESUPDFFetcher(username, password)

    try:
        fetcher.login()

        # Handle pattern flag by converting it to a special course_code format
        # Normalize course-code inputs: accept multiple -c flags or a single space/comma-separated string
        if args.course_code:
            normalized_codes: List[str] = []
            for raw in args.course_code:
                if not raw:
                    continue
                # Allow either space or comma separated lists in a single -c value
                parts = raw.replace(",", " ").split()
                for p in parts:
                    p_clean = p.strip()
                    if p_clean:
                        normalized_codes.append(p_clean)
            args.course_code = normalized_codes if normalized_codes else None

        # Decide course(s) to process
        if args.pattern:
            if args.course_code:
                print(
                    "⚠️  Warning: Both --course-code and --pattern provided. Using --pattern."
                )
            course_code_arg = f"PATTERN:{args.pattern}"

            # Pattern mode: single interactive_mode invocation will handle multiple matches
            interactive_mode(
                fetcher,
                course_code_arg,
                unit_filter,
                class_filter,
                args.list_units,
                args.no_merge,
                args.output,
                args.max_workers,
                content_type_id=content_type_id,
            )
        else:
            # Non-pattern mode: allow multiple -c values (action=append or space-separated). If none provided, go into interactive selection.
            if args.course_code:
                for provided_code in args.course_code:
                    print(
                        f"{Fore.BLUE}Processing course:{Style.RESET_ALL} {provided_code}"
                    )
                    interactive_mode(
                        fetcher,
                        provided_code,
                        unit_filter,
                        class_filter,
                        args.list_units,
                        args.no_merge,
                        args.output,
                        args.max_workers,
                        content_type_id=content_type_id,
                    )
            else:
                # No course provided: enter interactive selection
                interactive_mode(
                    fetcher,
                    None,
                    unit_filter,
                    class_filter,
                    args.list_units,
                    args.no_merge,
                    args.output,
                    args.max_workers,
                    content_type_id=content_type_id,
                )

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        fetcher.logout()


if __name__ == "__main__":
    main()
