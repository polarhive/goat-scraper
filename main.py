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
from typing import Optional, Dict, List, Any
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from pypdf import PdfWriter
from colorama import Fore, Style, init as colorama_init
from tqdm import tqdm

# Initialize colorama for cross-platform colored output
colorama_init(autoreset=True)


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logger(name: str = "pdf_fetcher", log_file: Optional[Path] = None) -> logging.Logger:
    """Set up a logger with console and file output."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    
    class ColoredFormatter(logging.Formatter):
        COLORS = {
            'DEBUG': Fore.CYAN,
            'INFO': Fore.GREEN,
            'WARNING': Fore.YELLOW,
            'ERROR': Fore.RED,
            'CRITICAL': Fore.RED + Style.BRIGHT,
        }
        
        def format(self, record):
            # Make a copy to avoid modifying the original record
            log_record = logging.makeLogRecord(record.__dict__)
            levelname = log_record.levelname
            if levelname in self.COLORS:
                log_record.levelname = f"{self.COLORS[levelname]}{levelname}{Style.RESET_ALL}"
            return super().format(log_record)
    
    console_handler.setFormatter(
        ColoredFormatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    logger.addHandler(console_handler)
    
    # File handler for failures (without colors) - only if log_file is explicitly provided
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.ERROR)  # Only log errors to file
        # Use plain formatter for file (no colors)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
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
                has_summary = any(f.name.endswith("_course_summary.json") for f in entry.iterdir() if f.is_file())
                if has_summary:
                    course_dirs.append(entry.name)
    
    # Write the index file
    index_data = {
        "courses": course_dirs,
        "updated_at": __import__("datetime").datetime.now().isoformat()
    }
    
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Updated courses index: {len(course_dirs)} courses in {index_file}")


# ============================================================================
# FILE CONVERSION UTILITIES  
# ============================================================================

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
    if suffix == '.pdf':
        return input_path  # Already a PDF
    
    output_path = input_path.with_suffix('.pdf')
    
    # Method 1: Try soffice (LibreOffice) headless mode
    soffice_paths = [
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',  # macOS
        '/usr/bin/soffice',  # Linux
        '/usr/bin/libreoffice',  # Linux alternative
        shutil.which('soffice'),
        shutil.which('libreoffice'),
    ]
    
    for soffice in soffice_paths:
        if soffice and Path(soffice).exists() if isinstance(soffice, str) else soffice:
            try:
                logger.info(f"Converting {input_path.name} to PDF using LibreOffice...")
                result = subprocess.run([
                    soffice,
                    '--headless',
                    '--convert-to', 'pdf',
                    '--outdir', str(input_path.parent),
                    str(input_path)
                ], capture_output=True, text=True, timeout=120)
                
                if output_path.exists():
                    logger.info(f"✓ Converted to PDF: {output_path}")
                    # Optionally remove original
                    # input_path.unlink()
                    return output_path
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                logger.debug(f"LibreOffice conversion failed: {e}")
                continue
    
    # Method 2: Try macOS Keynote/Pages via osascript (for PPTX/DOCX)
    if sys.platform == 'darwin':
        if suffix in ['.pptx', '.ppt']:
            try:
                logger.info(f"Converting {input_path.name} to PDF using Keynote...")
                script = f'''
                tell application "Keynote"
                    set theDoc to open POSIX file "{input_path}"
                    export theDoc to POSIX file "{output_path}" as PDF
                    close theDoc
                end tell
                '''
                result = subprocess.run(['osascript', '-e', script], 
                                       capture_output=True, text=True, timeout=120)
                if output_path.exists():
                    logger.info(f"✓ Converted to PDF: {output_path}")
                    return output_path
            except Exception as e:
                logger.debug(f"Keynote conversion failed: {e}")
        
        elif suffix in ['.docx', '.doc']:
            try:
                logger.info(f"Converting {input_path.name} to PDF using Pages...")
                script = f'''
                tell application "Pages"
                    set theDoc to open POSIX file "{input_path}"
                    export theDoc to POSIX file "{output_path}" as PDF
                    close theDoc
                end tell
                '''
                result = subprocess.run(['osascript', '-e', script],
                                       capture_output=True, text=True, timeout=120)
                if output_path.exists():
                    logger.info(f"✓ Converted to PDF: {output_path}")
                    return output_path
            except Exception as e:
                logger.debug(f"Pages conversion failed: {e}")
    
    # Method 3: For PPTX, try python-pptx + reportlab (limited - only extracts text/images)
    # This is a fallback that won't preserve full formatting
    
    logger.warning(f"Could not convert {input_path.name} to PDF. Keeping original format.")
    logger.info("Tip: Install LibreOffice for automatic conversion, or convert manually.")
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
        self.username = username
        self.password = password
        logger.info(f"Initialized PDF fetcher for user: {username}")
    
    def _extract_csrf_token(self, html_content: str) -> str:
        """Extract CSRF token from HTML content."""
        soup = BeautifulSoup(html_content, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf"})
        
        if not csrf_input or not csrf_input.get("value"):
            raise AuthenticationError("CSRF token not found in response")
        
        return csrf_input.get("value")
    
    def login(self) -> None:
        """Authenticate with PESU Academy."""
        logger.info("Starting authentication process...")
        
        try:
            # Get login page and extract CSRF token
            login_page_url = f"{self.BASE_URL}/"
            response = self.session.get(login_page_url)
            response.raise_for_status()
            
            csrf_token = self._extract_csrf_token(response.text)
            
            # Submit login credentials
            login_url = f"{self.BASE_URL}/j_spring_security_check"
            login_payload = {
                "j_username": self.username,
                "j_password": self.password,
                "_csrf": csrf_token,
            }
            
            login_response = self.session.post(login_url, data=login_payload)
            login_response.raise_for_status()
            
            # Validate authentication
            self._validate_authentication()
            
            logger.info("✓ Authentication successful")
            
        except requests.RequestException as e:
            raise AuthenticationError(f"Network error during authentication: {e}")
        except Exception as e:
            raise AuthenticationError(f"Authentication failed: {e}")
    
    def _validate_authentication(self) -> None:
        """Validate that authentication was successful."""
        profile_url = f"{self.BASE_URL}/s/studentProfilePESU"
        
        try:
            profile_response = self.session.get(profile_url, allow_redirects=False)
            
            if profile_response.status_code in (302, 301):
                raise AuthenticationError("Authentication failed: Invalid credentials")
                
        except requests.RequestException as e:
            raise AuthenticationError(f"Failed to validate authentication: {e}")
    
    def logout(self) -> None:
        """Logout from PESU Academy."""
        try:
            logout_url = f"{self.BASE_URL}/logout"
            self.session.get(logout_url)
            logger.info("✓ Session terminated")
        except requests.RequestException as e:
            logger.warning(f"Error during logout: {e}")
    
    # ========================================================================
    # STEP 1: Get Subject Codes
    # ========================================================================
    
    def get_subjects_code(self) -> Optional[List[Dict[str, Any]]]:
        """
        Step 1: Get all available course codes.
        Endpoint: /Academy/a/g/getSubjectsCode
        Returns HTML <option> tags that need to be parsed.
        """
        logger.info("\n=== STEP 1: Fetching Subject Codes ===")
        
        try:
            url = f"{self.BASE_URL}/a/g/getSubjectsCode"
            response = self.session.get(url)
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
                    course_id = course_id.replace('\\"', '').replace("\\'", '')
                    # Remove regular quotes
                    course_id = course_id.strip('"').strip("'")
                    # Remove any remaining backslashes
                    course_id = course_id.replace('\\', '')
                    
                    # Extract subject code (before the dash if present)
                    subject_code = course_name.split("-")[0] if "-" in course_name else course_name
                    
                    courses.append({
                        "id": course_id,
                        "subjectCode": subject_code,
                        "subjectName": course_name
                    })
            
            if courses:
                logger.info(f"✓ Found {len(courses)} courses")
                return courses
            else:
                logger.warning("No courses found in response")
                return None
                
        except requests.RequestException as e:
            logger.error(f"FAILURE [get_subjects_code]: Network error fetching subjects - {e}")
            logger.error(f"  URL: {url}")
            return None
        except Exception as e:
            logger.error(f"FAILURE [get_subjects_code]: Error parsing subjects - {e}")
            logger.error(f"  URL: {url}")
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
        logger.info(f"\n=== STEP 2: Fetching Units for Course {course_id} ===")
        
        try:
            url = f"{self.BASE_URL}/a/i/getCourse/{course_id}"
            response = self.session.get(url)
            response.raise_for_status()
            
            # The response is JSON-encoded HTML string
            html_content = response.json() if response.headers.get('Content-Type', '').startswith('application/json') else response.text
            
            # Parse HTML options
            soup = BeautifulSoup(html_content, "html.parser")
            options = soup.find_all("option")
            
            units = []
            for option in options:
                unit_id = option.get("value")
                unit_name = option.text.strip()
                
                if unit_id and unit_name:
                    # Clean the unit ID
                    unit_id = str(unit_id).strip().replace('\\', '').strip('"').strip("'")
                    
                    # Extract unit number if present
                    unit_number = unit_name.split(":")[0].strip() if ":" in unit_name else unit_name
                    
                    units.append({
                        "id": unit_id,
                        "unit": unit_name,
                        "unitNumber": unit_number
                    })
            
            if units:
                logger.info(f"✓ Found {len(units)} units")
                return units
            else:
                logger.warning("No units found in response")
                return None
                
        except requests.RequestException as e:
            logger.error(f"FAILURE [get_course_units]: Network error fetching course units - {e}")
            logger.error(f"  Course ID: {course_id}")
            logger.error(f"  URL: {url}")
            return None
        except Exception as e:
            logger.error(f"FAILURE [get_course_units]: Error parsing units - {e}")
            logger.error(f"  Course ID: {course_id}")
            logger.error(f"  URL: {url}")
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
        logger.info(f"\n=== STEP 3: Fetching Classes for Unit {unit_id} ===")
        
        try:
            url = f"{self.BASE_URL}/a/i/getCourseClasses/{unit_id}"
            response = self.session.get(url)
            response.raise_for_status()
            
            # The response is JSON-encoded HTML string
            html_content = response.json() if response.headers.get('Content-Type', '').startswith('application/json') else response.text
            
            # Parse HTML options
            soup = BeautifulSoup(html_content, "html.parser")
            options = soup.find_all("option")
            
            classes = []
            for option in options:
                class_id = option.get("value")
                class_name = option.text.strip()
                
                if class_id and class_name:
                    # Clean the class ID
                    class_id = str(class_id).strip().replace('\\', '').strip('"').strip("'")
                    
                    classes.append({
                        "id": class_id,
                        "className": class_name,
                        "classType": "Lecture",  # Default since not provided
                    })
            
            if classes:
                logger.info(f"✓ Found {len(classes)} classes")
                return classes
            else:
                logger.warning("No classes found in response")
                return None
                
        except requests.RequestException as e:
            logger.error(f"FAILURE [get_unit_classes]: Network error fetching unit classes - {e}")
            logger.error(f"  Unit ID: {unit_id}")
            logger.error(f"  URL: {url}")
            return None
        except Exception as e:
            logger.error(f"FAILURE [get_unit_classes]: Error parsing classes - {e}")
            logger.error(f"  Unit ID: {unit_id}")
            logger.error(f"  URL: {url}")
            return None
    
    # ========================================================================
    # STEP 4: Download File (PDF, PPTX, DOCX, etc.)
    # ========================================================================
    
    def download_pdf(self, course_id: str, class_id: str, output_path: Optional[Path] = None, class_name: Optional[str] = None) -> List[Path]:
        """
        Step 4: Download file(s) for a specific class (PDF, PPTX, DOCX, etc.).
        Returns a list of successfully downloaded file paths.
        If multiple files are found, all are downloaded with meaningful names based on link text.
        Endpoint: /Academy/s/studentProfilePESUAdmin with specific parameters
        """
        logger.info(f"\n=== STEP 4: Downloading File ===")
        logger.info(f"Course ID: {course_id}, Class ID: {class_id}")
        
        try:
            url = f"{self.BASE_URL}/s/studentProfilePESUAdmin"
            params = {
                "url": "studentProfilePESUAdmin",
                "controllerMode": "6403",
                "actionType": "60",
                "selectedData": course_id,
                "id": "2",
                "unitid": class_id
            }
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            # Check if response is actually a PDF or HTML
            content_type = response.headers.get('Content-Type', '')
            
            if 'application/pdf' in content_type:
                # Direct PDF download
                if output_path is None:
                    output_path = Path(f"course_{course_id}_class_{class_id}.pdf")
                
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                
                file_size = output_path.stat().st_size
                
                # Check if file is empty (0 bytes) and skip it
                if file_size == 0:
                    logger.warning(f"⚠ Downloaded PDF is empty (0 bytes), skipping")
                    output_path.unlink()  # Delete the 0-byte file
                    return []
                
                logger.info(f"✓ PDF downloaded successfully: {output_path} ({file_size:,} bytes)")
                return [output_path]
            
            elif 'text/html' in content_type:
                # Parse HTML to find download links (PDF, PPTX, DOCX, etc.)
                logger.info("Response is HTML, parsing for download links...")
                soup = BeautifulSoup(response.text, "html.parser")
                
                # Look for links with onclick that call loadIframe, downloadslidecoursedoc, or downloadcoursedoc
                download_links = []
                import re
                
                # Search ALL elements with onclick attribute (not just <a> tags)
                for element in soup.find_all(onclick=True):
                    onclick = element.get('onclick', '')
                    text = element.text.strip()
                    
                    # Check for downloadcoursedoc pattern (e.g., onclick="downloadcoursedoc('ID')")
                    if 'downloadcoursedoc' in onclick:
                        # Extract ID from downloadcoursedoc('ID') pattern
                        match = re.search(r"downloadcoursedoc\('([^']+)'", onclick)
                        if match:
                            doc_id = match.group(1)
                            download_url = f"/Academy/s/referenceMeterials/downloadcoursedoc/{doc_id}"
                            full_url = f"https://www.pesuacademy.com{download_url}"
                            
                            download_links.append({
                                'text': text or 'Course Document',
                                'href': download_url,
                                'full_url': full_url
                            })
                            continue
                    
                    # Check onclick for downloadslidecoursedoc pattern
                    if 'downloadslidecoursedoc' in onclick:
                        # Extract the URL from onclick="loadIframe('/Academy/a/referenceMeterials/downloadslidecoursedoc/ID')"
                        match = re.search(r"loadIframe\('([^']+)'", onclick)
                        if match:
                            download_url = match.group(1)
                            # Remove the #view parameters
                            download_url = download_url.split('#')[0]
                            
                            # Build full URL - if it starts with /Academy, use base domain only
                            if download_url.startswith('/Academy'):
                                full_url = f"https://www.pesuacademy.com{download_url}"
                            elif download_url.startswith('http'):
                                full_url = download_url
                            else:
                                full_url = f"{self.BASE_URL}/{download_url.lstrip('/')}"
                            
                            download_links.append({
                                'text': text or 'Course Document',
                                'href': download_url,
                                'full_url': full_url
                            })
                
                # Also check <a> tags for href-based download links
                for link in soup.find_all('a'):
                    href = link.get('href', '')
                    text = link.text.strip()
                    
                    # Check for direct href links to downloadslidecoursedoc
                    if 'downloadslidecoursedoc' in href:
                        download_url = href
                        download_url = download_url.split('#')[0]
                        
                        if download_url.startswith('/Academy'):
                            full_url = f"https://www.pesuacademy.com{download_url}"
                        elif download_url.startswith('http'):
                            full_url = download_url
                        else:
                            full_url = f"{self.BASE_URL}/{download_url.lstrip('/')}"
                        
                        download_links.append({
                            'text': text or 'Course Document',
                            'href': download_url,
                            'full_url': full_url
                        })
                    
                    # Also check for any links with referenceMeterials or downloads
                    elif 'referenceMeterials' in href or 'download' in href.lower():
                        download_url = href
                        download_url = download_url.split('#')[0]
                        
                        if download_url.startswith('/Academy'):
                            full_url = f"https://www.pesuacademy.com{download_url}"
                        elif download_url.startswith('http'):
                            full_url = download_url
                        else:
                            full_url = f"{self.BASE_URL}/{download_url.lstrip('/')}"
                        
                        download_links.append({
                            'text': text or 'Course Document',
                            'href': download_url,
                            'full_url': full_url
                        })
                
                if not download_links:
                    logger.error("No download links found in the response")
                    return []
                
                # Remove duplicates by URL while preserving order
                seen_urls = set()
                unique_links = []
                for link in download_links:
                    if link['full_url'] not in seen_urls:
                        seen_urls.add(link['full_url'])
                        unique_links.append(link)
                download_links = unique_links
                
                # Download ALL links (not just the first one)
                if len(download_links) > 1:
                    logger.info(f"Found {len(download_links)} download options, downloading all")
                    # Log each link for debugging
                    for idx, link in enumerate(download_links):
                        logger.info(f"  [{idx + 1}] {link['text'][:50]} -> {link['full_url']}")
                else:
                    logger.info(f"Found 1 download option: {download_links[0]['text']}")
                
                downloaded_files = []
                
                # Download each file
                for link_idx, selected_link in enumerate(download_links):
                    logger.info(f"Downloading [{link_idx + 1}/{len(download_links)}]: {selected_link['text']}")
                
                    # Download the selected file with proper headers (especially Referer)
                    logger.info(f"Downloading from: {selected_link['full_url']}")
                    try:
                        # Add Referer header - required for downloadslidecoursedoc URLs
                        headers = {
                            'Referer': 'https://www.pesuacademy.com/Academy/s/studentProfilePESU',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        }
                        file_response = self.session.get(selected_link['full_url'], stream=True, headers=headers)
                        file_response.raise_for_status()
                    except requests.RequestException as e:
                        logger.error(f"Failed to download link {link_idx + 1}: {e}")
                        continue
                    
                    # Try to get filename from Content-Disposition header first
                    content_disposition = file_response.headers.get('Content-Disposition', '')
                    original_filename = None
                    if 'filename=' in content_disposition:
                        import re
                        # Try to extract filename from Content-Disposition
                        match = re.search(r'filename[*]?=["\']?(?:UTF-8\'\')?([^"\';\n]+)', content_disposition)
                        if match:
                            original_filename = match.group(1).strip()
                            logger.info(f"Original filename from server: {original_filename}")
                    
                    # Determine file extension from content-type or original filename
                    file_content_type = file_response.headers.get('Content-Type', '')
                    extension = '.pdf'  # Default
                    
                    # First try to get extension from original filename
                    if original_filename and '.' in original_filename:
                        extension = '.' + original_filename.rsplit('.', 1)[-1].lower()
                    # Otherwise use content-type
                    elif 'application/pdf' in file_content_type:
                        extension = '.pdf'
                    elif 'application/vnd.openxmlformats-officedocument.presentationml.presentation' in file_content_type:
                        extension = '.pptx'
                    elif 'application/vnd.ms-powerpoint' in file_content_type:
                        extension = '.ppt'
                    elif 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' in file_content_type:
                        extension = '.docx'
                    elif 'application/msword' in file_content_type:
                        extension = '.doc'
                    elif 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' in file_content_type:
                        extension = '.xlsx'
                    elif 'application/vnd.ms-excel' in file_content_type:
                        extension = '.xls'
                    elif 'application/octet-stream' in file_content_type:
                        # Generic binary - try to detect from magic bytes
                        # Read first few bytes to detect file type
                        first_chunk = next(file_response.iter_content(chunk_size=8), b'')
                        if first_chunk.startswith(b'PK'):
                            # ZIP-based format (pptx, docx, xlsx)
                            # Need more context, default to pptx for presentations
                            extension = '.pptx'
                            logger.info("Detected ZIP-based format (likely Office document)")
                        elif first_chunk.startswith(b'%PDF'):
                            extension = '.pdf'
                        # Put the chunk back by creating a new iterator
                        def iter_with_first_chunk():
                            yield first_chunk
                            yield from file_response.iter_content(chunk_size=8192)
                        content_iterator = iter_with_first_chunk()
                    else:
                        content_iterator = None
                    
                    if 'content_iterator' not in locals() or content_iterator is None:
                        content_iterator = file_response.iter_content(chunk_size=8192)
                    
                    logger.info(f"Detected file type: {extension}")
                    
                    # Determine output path with meaningful names for multiple files
                    if output_path is None:
                        # Try to get filename from URL or use default
                        filename = selected_link['href'].split('/')[-1]
                        if '.' not in filename:
                            filename = f"class_{class_id}{extension}"
                        current_output_path = Path(filename)
                    else:
                        current_output_path = output_path
                        # If output path was provided but has wrong extension, update it
                        if current_output_path.suffix == '.pdf' and extension != '.pdf':
                            current_output_path = current_output_path.with_suffix(extension)
                    
                    # For multiple files, use class_name + link_text for meaningful names
                    if len(download_links) > 1:
                        # Get the base prefix from output path (e.g., "05_" from "05_Kafka.pdf")
                        prefix = ""
                        if output_path:
                            # Extract numeric prefix like "05_"
                            stem = current_output_path.stem
                            import re
                            match = re.match(r'^(\d+)_', stem)
                            if match:
                                prefix = match.group(1) + "_"
                        
                        # Use link text for the name, cleaning it up
                        link_text = selected_link['text']
                        # Clean the link text: remove special chars, limit length
                        safe_link_text = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in link_text).strip()
                        safe_link_text = '_'.join(safe_link_text.split())[:80]  # Join spaces with underscore, limit length
                        
                        # Combine: prefix + class_name (if available) + link_text
                        if class_name:
                            # Extract class name without prefix
                            class_base = class_name.split('.', 1)[-1] if '.' in class_name else class_name
                            class_base = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in class_base).strip()
                            class_base = '_'.join(class_base.split())[:50]
                            filename = f"{prefix}{class_base}_{safe_link_text}{extension}"
                        else:
                            filename = f"{prefix}{safe_link_text}{extension}"
                        
                        current_output_path = current_output_path.parent / filename
                    
                    # Save file
                    try:
                        with open(current_output_path, 'wb') as f:
                            for chunk in content_iterator:
                                f.write(chunk)
                        
                        file_size = current_output_path.stat().st_size
                        
                        # Check if file is empty (0 bytes) and skip it
                        if file_size == 0:
                            logger.warning(f"⚠ Skipping empty file (0 bytes): {current_output_path.name}")
                            logger.warning(f"   Link text: {selected_link['text']}")
                            logger.warning(f"   URL: {selected_link['full_url']}")
                            current_output_path.unlink()  # Delete the 0-byte file
                            continue
                        
                        logger.info(f"✓ File downloaded successfully: {current_output_path.name} ({file_size:,} bytes)")
                        
                        # Convert to PDF if not already a PDF
                        if extension != '.pdf':
                            pdf_path = convert_to_pdf(current_output_path)
                            if pdf_path:
                                # Update the output path to the PDF
                                current_output_path = pdf_path
                                # Re-check file size after conversion
                                if current_output_path.exists():
                                    converted_size = current_output_path.stat().st_size
                                    if converted_size == 0:
                                        logger.warning(f"⚠ Converted PDF is empty (0 bytes): {current_output_path.name}")
                                        current_output_path.unlink()  # Delete the 0-byte PDF
                                        continue
                        
                        downloaded_files.append(current_output_path)
                        
                    except IOError as e:
                        logger.error(f"Failed to save file {link_idx + 1}: {e}")
                        continue
                
                return downloaded_files
            
            else:
                logger.error(f"Unexpected content type: {content_type}")
                return []
            
        except requests.RequestException as e:
            logger.error(f"FAILURE [download_pdf]: Network error downloading file - {e}")
            logger.error(f"  Course ID: {course_id}")
            logger.error(f"  Class ID: {class_id}")
            logger.error(f"  Output Path: {output_path}")
            return []
        except IOError as e:
            logger.error(f"FAILURE [download_pdf]: File I/O error - {e}")
            logger.error(f"  Course ID: {course_id}")
            logger.error(f"  Class ID: {class_id}")
            logger.error(f"  Output Path: {output_path}")
            return []
        except Exception as e:
            logger.error(f"FAILURE [download_pdf]: Unexpected error - {e}")
            logger.error(f"  Course ID: {course_id}")
            logger.error(f"  Class ID: {class_id}")
            logger.error(f"  Output Path: {output_path}")
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


def merge_pdfs(pdf_files: List[Path], output_path: Path) -> bool:
    """Merge multiple PDF files into a single PDF. Skips non-PDF files."""
    try:
        merger = PdfWriter()
        pdf_count = 0
        
        for pdf_file in pdf_files:
            # Only merge PDF files
            if pdf_file.suffix.lower() != '.pdf':
                logger.info(f"Skipping non-PDF file: {pdf_file.name}")
                continue
                
            if pdf_file.exists() and pdf_file.stat().st_size > 0:
                try:
                    merger.append(str(pdf_file))
                    pdf_count += 1
                except Exception as e:
                    logger.warning(f"Failed to add {pdf_file.name} to merged PDF: {e}")
                    continue
        
        if len(merger.pages) == 0:
            logger.warning(f"No valid PDFs to merge (found {len(pdf_files)} files, {pdf_count} were PDFs)")
            return False
        
        with open(output_path, 'wb') as f:
            merger.write(f)
        
        merger.close()
        logger.info(f"✓ Merged {pdf_count} PDFs into {output_path.name} ({output_path.stat().st_size:,} bytes)")
        return True
        
    except Exception as e:
        logger.error(f"FAILURE [merge_pdfs]: Failed to merge PDFs - {e}")
        logger.error(f"  Output Path: {output_path}")
        logger.error(f"  Number of files: {len(pdf_files)}")
        return False


def generate_esa_pdf(course_dir: Path, course_prefix: str) -> bool:
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
            merged_pdf_pattern = f"{course_prefix}_u{unit_num}_merged.pdf"
            merged_pdf_files = list(unit_dir.glob(merged_pdf_pattern))
            
            if merged_pdf_files:
                merged_pdfs.append((unit_num, merged_pdf_files[0]))
        
        if len(merged_pdfs) == 0:
            logger.warning(f"No merged PDFs found for ESA generation in {course_dir.name}")
            return False
        
        # Sort by unit number
        merged_pdfs.sort(key=lambda x: x[0])
        
        # Create ESA PDF
        esa_pdf_path = course_dir / f"{course_prefix}_ESA.pdf"
        
        print(f"  {Fore.BLUE}Creating ESA PDF from {len(merged_pdfs)} unit(s)...{Style.RESET_ALL} ", end="", flush=True)
        
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
        
        with open(esa_pdf_path, 'wb') as f:
            merger.write(f)
        
        merger.close()
        print(f"{Fore.GREEN}✓{Style.RESET_ALL}")
        logger.info(f"✓ Created ESA PDF: {esa_pdf_path.name} ({esa_pdf_path.stat().st_size:,} bytes)")
        return True
        
    except Exception as e:
        print(f"{Fore.RED}✗{Style.RESET_ALL}")
        logger.error(f"FAILURE [generate_esa_pdf]: Failed to generate ESA PDF - {e}")
        logger.error(f"  Course Directory: {course_dir}")
        logger.error(f"  Course Prefix: {course_prefix}")
        return False


def batch_download_all(fetcher: PESUPDFFetcher, course_id: str, course_name: str, course_dir: Path, 
                       unit_filter: Optional[List[int]] = None, class_filter: Optional[List[int]] = None,
                       skip_merge: bool = False) -> None:
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
    """
    print("\n" + "=" * 60)
    print("Batch Download - All Course Materials")
    print("=" * 60)
    
    # Setup course-specific failure log using same naming as directory
    import re
    subject_match = next((s for s in fetcher.get_subjects_code() or [] if s["id"] == course_id), None)
    subject_code = subject_match["subjectCode"] if subject_match else course_id
    
    clean_name = course_name.split('-', 1)[-1].strip() if '-' in course_name else course_name
    safe_name = "".join(c if c.isalnum() or c in (' ', '-') else '-' for c in clean_name).strip()
    safe_name = '-'.join(safe_name.split())
    
    course_prefix = f"{subject_code}-{safe_name}"
    course_log_file = course_dir / f"{course_prefix}_failures.log"
    
    # Reconfigure logger with course-specific log file
    global logger
    logger = setup_logger("pdf_fetcher", course_log_file)
    
    # Get all units
    units = fetcher.get_course_units(course_id)
    if not units:
        print("\n❌ Failed to fetch units.")
        return
    
    # Filter units if specified
    if unit_filter:
        filtered_units = [(idx, u) for idx, u in enumerate(units, 1) if idx in unit_filter]
        if not filtered_units:
            print(f"\n❌ No units found matching filter: {unit_filter}")
            return
        print(f"\nFound {len(units)} total units. Downloading {len(filtered_units)} unit(s): {unit_filter}\n")
        units_to_process = filtered_units
    else:
        print(f"\nFound {len(units)} units. Starting download...\n")
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
        "failure_log": course_log_file.name
    }
    
    for unit_idx, unit in units_to_process:
        unit_id = unit['id']
        unit_name = unit['unit']
        
        print(f"\n{Fore.CYAN}[{unit_idx}/{len(units)}]{Style.RESET_ALL} {Fore.WHITE}{Style.BRIGHT}{unit_name}{Style.RESET_ALL}")
        
        # Get classes
        classes = fetcher.get_unit_classes(unit_id)
        if not classes:
            print(f"  {Fore.YELLOW}⚠ No classes found{Style.RESET_ALL}")
            summary["units"].append({
                "unit_number": unit_idx,
                "unit_id": unit_id,
                "unit_name": unit_name,
                "classes": [],
                "total_files": 0,
                "failed_files": 0,
                "merged_pdf": None
            })
            continue
        
        # Create unit directory - extract title after colon or use full name
        # Format: "Unit 1: Introduction" -> "Introduction" or "IoT  Analytics, Security & Privacy:" -> "IoT-Analytics-Security-Privacy"
        unit_title = unit_name.split(':', 1)[-1].strip() if ':' in unit_name else unit_name
        # Remove trailing colon if present
        unit_title = unit_title.rstrip(':')
        safe_unit_title = "".join(c if c.isalnum() or c in (' ', '-') else '-' for c in unit_title).strip()
        safe_unit_title = '-'.join(safe_unit_title.split())  # Replace spaces with hyphens
        # Remove any trailing hyphens and empty strings
        safe_unit_title = safe_unit_title.strip('-')
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
            "merged_pdf": None
        }
        
        # Filter classes if specified
        classes_to_download = classes
        if class_filter:
            classes_to_download = [cls for idx, cls in enumerate(classes, 1) if idx in class_filter]
            if not classes_to_download:
                print(f"  {Fore.YELLOW}⚠ No classes match filter: {class_filter}{Style.RESET_ALL}")
                summary["units"].append(unit_summary)
                continue
            print(f"  Filtering: {len(classes_to_download)}/{len(classes)} classes")
        
        # Download each class with progress bar
        with tqdm(total=len(classes_to_download), desc="  Downloading", unit="file", leave=False, 
                  bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]") as pbar:
            for class_idx, cls in enumerate(classes_to_download, 1):
                class_id = cls['id']
                class_name = cls['className']
                
                # Safe filename with zero-padded numbering
                safe_name = "".join(c for c in class_name if c.isalnum() or c in (' ', '-', '_')).strip()[:50]
                padded_num = str(class_idx).zfill(2)  # 01, 02, 03, etc.
                output_path = unit_dir / f"{padded_num}_{safe_name}.pdf"
                
                pbar.set_postfix_str(f"{class_name[:40]}..." if len(class_name) > 40 else class_name)
                
                class_info = {
                    "class_number": class_idx,
                    "class_id": class_id,
                    "class_name": class_name,
                    "files": [],  # Changed to list to support multiple files
                    "status": "failed"
                }
                
                # download_pdf now returns a list of downloaded file paths
                downloaded_files = fetcher.download_pdf(course_id, class_id, output_path, class_name)
                
                if downloaded_files:
                    total_downloaded += len(downloaded_files)
                    unit_pdfs.extend(downloaded_files)
                    
                    # Update class info with all downloaded files
                    for actual_file in downloaded_files:
                        if actual_file.exists():
                            class_info["files"].append({
                                "filename": actual_file.name,
                                "file_size": actual_file.stat().st_size,
                                "file_type": actual_file.suffix.lstrip('.')
                            })
                    
                    class_info["status"] = "success"
                    unit_summary["total_files"] += len(downloaded_files)
                    
                    file_count_msg = f" ({len(downloaded_files)} files)" if len(downloaded_files) > 1 else ""
                    pbar.write(f"    {Fore.GREEN}✓{Style.RESET_ALL} {class_name}{file_count_msg}")
                else:
                    logger.error(f"FAILURE [batch_download]: Failed to download class")
                    logger.error(f"  Unit: {unit_name}")
                    logger.error(f"  Class: {class_name}")
                    logger.error(f"  Class ID: {class_id}")
                    logger.error(f"  Expected Output: {output_path}")
                    total_failed += 1
                    unit_summary["failed_files"] += 1
                    pbar.write(f"    {Fore.RED}✗{Style.RESET_ALL} {class_name}")
                
                unit_summary["classes"].append(class_info)
                pbar.update(1)
        
        # Merge PDFs for this unit (non-PDF files will be skipped) unless --no-merge flag is set
        if unit_pdfs and not skip_merge:
            pdf_files_only = [f for f in unit_pdfs if f.suffix.lower() == '.pdf']
            if pdf_files_only:
                print(f"  {Fore.BLUE}Merging {len(pdf_files_only)} PDFs...{Style.RESET_ALL} ", end="", flush=True)
                merged_pdf_path = unit_dir / f"{course_prefix}_u{unit_idx}_merged.pdf"
                if merge_pdfs(unit_pdfs, merged_pdf_path):
                    print(f"{Fore.GREEN}✓{Style.RESET_ALL}")
                    unit_summary["merged_pdf"] = merged_pdf_path.name
                else:
                    print(f"{Fore.RED}✗{Style.RESET_ALL}")
            else:
                logger.info(f"  No PDF files to merge for this unit (downloaded {len(unit_pdfs)} non-PDF files)")
        elif unit_pdfs and skip_merge:
            logger.info(f"  Skipping merge (--no-merge flag set)")
        
        summary["units"].append(unit_summary)
    
    # Add summary totals
    summary["total_downloaded"] = total_downloaded
    summary["total_failed"] = total_failed
    
    # Save summary to JSON file with course prefix
    summary_file = course_dir / f"{course_prefix}_course_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    # Generate ESA PDF (combining all 4 units) unless skip_merge is set
    if not skip_merge:
        print()
        generate_esa_pdf(course_dir, course_prefix)
    
    # Update the courses index.json for the frontend API
    update_courses_index(course_dir.parent)
    
    print("\n" + "=" * 60)
    print(f"{Fore.GREEN}{Style.BRIGHT}Complete!{Style.RESET_ALL} Downloaded: {Fore.GREEN}{total_downloaded}{Style.RESET_ALL}, Failed: {Fore.RED}{total_failed}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Location:{Style.RESET_ALL} {course_dir}")
    print(f"{Fore.CYAN}Summary saved to:{Style.RESET_ALL} {summary_file}")
    if total_failed > 0:
        print(f"{Fore.YELLOW}Failure log:{Style.RESET_ALL} {course_log_file}")
    print("=" * 60)


def interactive_mode(fetcher: PESUPDFFetcher, course_code: Optional[str] = None, 
                    unit_filter: Optional[List[int]] = None, class_filter: Optional[List[int]] = None,
                    list_units: bool = False, skip_merge: bool = False, output_dir: Optional[str] = None) -> None:
    """Run the PDF fetcher in interactive mode with optional filters."""
    print("\n" + "=" * 60)
    print("PESU Academy PDF Fetcher - Interactive Mode")
    print("=" * 60)
    
    try:
        # Step 1: Get subject codes
        subjects = fetcher.get_subjects_code()
        if not subjects:
            print("\n❌ Failed to fetch subjects. Exiting.")
            return
        
        # Save all subjects to JSON file
        subjects_file = Path("courses.json")
        with open(subjects_file, 'w', encoding='utf-8') as f:
            json.dump(subjects, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved all {len(subjects)} courses to {subjects_file}")
        
        # If course_code provided via CLI flag, use it directly
        if course_code:
            # Try to match by ID first, then by subject code
            course_match = next((s for s in subjects if s["id"] == course_code or s["subjectCode"] == course_code), None)
            if not course_match:
                print(f"\n❌ Course code '{course_code}' not found.")
                print(f"Hint: Use course ID or subject code (e.g., '20975' or 'UE23CS342AA3')")
                return
            course_id = course_match["id"]
            course_name = course_match["subjectName"]
            print(f"\n✓ Using course: {course_name} (ID: {course_id})")
        else:
            # Use fzf for fuzzy finding
            print(f"\nLaunching fzf to search through {len(subjects)} courses...")
            
            try:
                import subprocess
                
                # Prepare fzf input with format: "ID | Code | Name"
                fzf_input = "\n".join([
                    f"{s['id']} | {s['subjectCode']} | {s['subjectName']}" 
                    for s in subjects
                ])
                
                # Run fzf
                result = subprocess.run(
                    ['fzf', '--prompt=Select course: ', '--height=40%', '--reverse'],
                    input=fzf_input,
                    text=True,
                    capture_output=True
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
                print(f"\n✓ Selected: {course_name}")
                
            except FileNotFoundError:
                logger.error("fzf not found. Please install fzf: brew install fzf")
                print("\nFalling back to manual search...")
                print("Enter course ID or search term: ", end="")
                search_term = input().strip()
                
                if search_term.lower() == 'q':
                    print("Exiting...")
                    return
                
                if search_term.isdigit():
                    course_id = search_term
                    # Find the course name
                    course_match = next((s for s in subjects if s["id"] == course_id), None)
                    course_name = course_match["subjectName"] if course_match else f"Course {course_id}"
                else:
                    # Fallback fuzzy search
                    matches = [s for s in subjects if search_term.lower() in s.get("subjectName", "").lower()]
                    if not matches:
                        print(f"\n❌ No courses found matching '{search_term}'")
                        return
                    if len(matches) == 1:
                        course_id = matches[0]["id"]
                        course_name = matches[0]["subjectName"]
                        print(f"\n✓ Selected: {course_name}")
                    else:
                        print_table(matches[:20], ["id", "subjectCode", "subjectName"], f"Found {len(matches)} matches")
                        print("\nEnter course ID: ", end="")
                        course_id = input().strip()
                        course_match = next((s for s in subjects if s["id"] == course_id), None)
                        course_name = course_match["subjectName"] if course_match else f"Course {course_id}"
        
        if course_id.lower() == 'q':
            print("Exiting...")
            return
        
        # Create course directory with format: course{id}_{subjectCode-Course-Name}
        subject_match = next((s for s in subjects if s["id"] == course_id), None)
        subject_code = subject_match["subjectCode"] if subject_match else course_id
        
        # Clean course name (remove subject code prefix if present)
        clean_name = course_name.split('-', 1)[-1].strip() if '-' in course_name else course_name
        safe_name = "".join(c if c.isalnum() or c in (' ', '-') else '-' for c in clean_name).strip()
        safe_name = '-'.join(safe_name.split())  # Replace spaces with hyphens
        
        # If --list-units flag is set, just list units and exit
        if list_units:
            units = fetcher.get_course_units(course_id)
            if units:
                print(f"\n{Fore.CYAN}Units for {course_name}:{Style.RESET_ALL}")
                for idx, unit in enumerate(units, 1):
                    print(f"  {idx}. {unit['unit']}")
            else:
                print("\n❌ Failed to fetch units.")
            return
        
        # Load base directory from environment variable or use default
        base_dir_env = output_dir or os.getenv("BASE_DIR", "frontend/public/courses")
        base_dir = Path(__file__).parent / base_dir_env
        base_dir.mkdir(parents=True, exist_ok=True)
        course_dir = base_dir / f"course{course_id}_{subject_code}-{safe_name}"
        course_dir.mkdir(exist_ok=True)
        
        # If course_code was provided via CLI, automatically download all materials
        if course_code:
            batch_download_all(fetcher, course_id, course_name, course_dir, unit_filter, class_filter, skip_merge)
            return
        
        # Ask for download mode only in interactive mode
        print("\nDownload mode:")
        print("  1. Download ALL materials (all units, all classes)")
        print("  2. Interactive (select specific unit/class)")
        print("\nChoice (1/2, default=1): ", end="")
        mode = input().strip() or "1"
        
        if mode == "1":
            batch_download_all(fetcher, course_id, course_name, course_dir)
            return
        
        # Continue with interactive mode...
        # Step 2: Get course units
        units = fetcher.get_course_units(course_id)
        if not units:
            print("\n❌ Failed to fetch units for this course. Exiting.")
            return
        
        # Save units to JSON file
        units_file = course_dir / "units.json"
        with open(units_file, 'w', encoding='utf-8') as f:
            json.dump(units, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved {len(units)} units to {units_file}")
        
        # Display units
        print_table(units, ["id", "unit", "unitNumber"], f"Units for Course {course_id}")
        
        # Use fzf for unit selection
        print(f"\nLaunching fzf to select unit...")
        
        try:
            import subprocess
            
            # Prepare fzf input
            fzf_input = "\n".join([f"{u['id']} | {u['unit']}" for u in units])
            
            result = subprocess.run(
                ['fzf', '--prompt=Select unit: ', '--height=40%', '--reverse'],
                input=fzf_input,
                text=True,
                capture_output=True
            )
            
            if result.returncode != 0:
                print("No unit selected. Exiting.")
                return
            
            selected = result.stdout.strip()
            if not selected:
                print("No unit selected. Exiting.")
                return
            
            unit_id = selected.split(" | ")[0].strip()
            print(f"\n✓ Selected unit: {selected.split(' | ')[1] if len(selected.split(' | ')) > 1 else unit_id}")
            
        except FileNotFoundError:
            # Fallback to manual input
            print("\nEnter unit ID to continue (or 'q' to quit): ", end="")
            unit_id = input().strip()
            
            if unit_id.lower() == 'q':
                print("Exiting...")
                return
        
        # Step 3: Get unit classes
        classes = fetcher.get_unit_classes(unit_id)
        if not classes:
            print("\n❌ Failed to fetch classes for this unit. Exiting.")
            return
        
        # Save classes to JSON file
        classes_file = course_dir / f"unit_{unit_id}_classes.json"
        with open(classes_file, 'w', encoding='utf-8') as f:
            json.dump(classes, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Saved {len(classes)} classes to {classes_file}")
        
        # Display classes
        display_keys = [k for k in ["id", "className", "classType", "date", "topic"] if k in (classes[0] if classes else {})]
        print_table(classes, display_keys, f"Classes for Unit {unit_id}")
        
        # Use fzf for class selection
        print(f"\nLaunching fzf to select class...")
        
        try:
            import subprocess
            
            # Prepare fzf input
            fzf_input = "\n".join([f"{c['id']} | {c['className']}" for c in classes])
            
            result = subprocess.run(
                ['fzf', '--prompt=Select class: ', '--height=40%', '--reverse'],
                input=fzf_input,
                text=True,
                capture_output=True
            )
            
            if result.returncode != 0:
                print("No class selected. Exiting.")
                return
            
            selected = result.stdout.strip()
            if not selected:
                print("No class selected. Exiting.")
                return
            
            class_id = selected.split(" | ")[0].strip()
            print(f"\n✓ Selected class: {selected.split(' | ')[1] if len(selected.split(' | ')) > 1 else class_id}")
            
        except FileNotFoundError:
            # Fallback to manual input
            print("\nEnter class ID to download PDF (or 'q' to quit): ", end="")
            class_id = input().strip()
            
            if class_id.lower() == 'q':
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
            print("\n✓ PDF download completed successfully!")
        else:
            print("\n❌ PDF download failed.")
    
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
  
  # Download specific units only
  python main.py -c UE23CS343AB2 -u 1,3
  
  # Download specific unit with class range
  python main.py -c UE23CS343AB2 -u 2 --class-range 1-5
  
  # List available units without downloading
  python main.py -c UE23CS343AB2 --list-units
  
  # Skip merge (keep individual PDFs only)
  python main.py -c UE23CS343AB2 --no-merge
        """
    )
    parser.add_argument(
        "-c", "--course-code",
        type=str,
        help="Course code/ID to download directly (skips interactive selection)"
    )
    parser.add_argument(
        "-u", "--units",
        type=str,
        help="Comma-separated unit numbers to download (e.g., '1,3,4' or '1-3'). Downloads all units if not specified."
    )
    parser.add_argument(
        "--class-range",
        type=str,
        help="Range of class numbers to download within each unit (e.g., '1-5' or '3,5,7')"
    )
    parser.add_argument(
        "--list-units",
        action="store_true",
        help="List all units for the course without downloading"
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Skip merging PDFs into a single file per unit"
    )
    parser.add_argument(
        "--update-index",
        action="store_true",
        help="Only update the courses index.json file (no download)"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        help="Custom output directory (default: frontend/public/courses)"
    )
    args = parser.parse_args()
    
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
            print(f"❌ Courses directory not found: {base_dir}")
        return
    
    # Parse unit filter (e.g., "1,3,4" or "1-3")
    unit_filter = None
    if args.units:
        unit_filter = []
        for part in args.units.split(','):
            if '-' in part:
                start, end = map(int, part.split('-'))
                unit_filter.extend(range(start, end + 1))
            else:
                unit_filter.append(int(part))
    
    # Parse class filter (e.g., "1-5" or "3,5,7")
    class_filter = None
    if args.class_range:
        class_filter = []
        for part in args.class_range.split(','):
            if '-' in part:
                start, end = map(int, part.split('-'))
                class_filter.extend(range(start, end + 1))
            else:
                class_filter.append(int(part))
    
    print("PESU Academy PDF Fetcher")
    print("-" * 60)
    
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
            password = input("Password: ").strip()
    except ImportError:
        # If dotenv not available, ask for manual input
        print("Enter your PESU Academy credentials:")
        username = input("Username (SRN): ").strip()
        password = input("Password: ").strip()
    
    if not username or not password:
        print("❌ Username and password are required.")
        sys.exit(1)
    
    # Create fetcher and login
    fetcher = PESUPDFFetcher(username, password)
    
    try:
        fetcher.login()
        
        # Run interactive mode with filters
        interactive_mode(fetcher, args.course_code, unit_filter, class_filter, 
                        args.list_units, args.no_merge, args.output)
        
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
