# -*- coding: utf-8 -*-
"""
Update and download management for MLK-II ALVT main window.
Extracted from MLK-II ALVT_Tool.py to reduce main file size.
"""

import logging
from datetime import datetime, timedelta
from packaging import version
from PySide6.QtWidgets import QMessageBox
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl, QThread, Signal

logger = logging.getLogger(__name__)


class VersionCheckThread(QThread):
    """Background thread for checking remote version without blocking UI."""
    
    # Signal emitted when version check completes: (success, remote_version, error_msg)
    version_checked = Signal(bool, str, str)
    
    def __init__(self, version_url, timeout=5):
        """
        Initialize version check thread.
        
        Args:
            version_url: URL to fetch version.txt from
            timeout: Network timeout in seconds
        """
        super().__init__()
        self.version_url = version_url
        self.timeout = timeout
    
    def run(self):
        """Fetch remote version in background thread with proxy support."""
        try:
            import urllib.request
            import urllib.error
            import socket
            
            # Detect system proxies
            proxies = urllib.request.getproxies()
            if proxies:
                logger.info(f"Detected system proxies: {proxies}")
            else:
                logger.debug("No system proxies detected.")

            # Build opener with ProxyHandler (automatic system proxy detection)
            # We also add an HTTPS handler just in case, though it's usually default
            proxy_handler = urllib.request.ProxyHandler()
            opener = urllib.request.build_opener(proxy_handler)
            
            # Add random parameter to bypass caches (cache busting)
            import time
            bust_url = f"{self.version_url}?t={int(time.time())}"
            
            # Fetch version.txt with absolute no-cache headers
            req = urllib.request.Request(
                bust_url,
                headers={
                    'User-Agent': 'MLK-II-ALVT-UpdateChecker/1.2',
                    'Accept': 'text/plain, */*',
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                }
            )
            
            # Open the URL using our proxy-aware opener
            response = opener.open(req, timeout=self.timeout)
            
            # Read response
            content = response.read().decode('utf-8').strip()
            
            # Check if we got HTML instead of plain text (SharePoint error page)
            if content.lower().startswith('<!doctype') or content.lower().startswith('<html'):
                raise ValueError("Received HTML instead of version number (authentication required)")
            
            # Validate version format (e.g., "12.5.8")
            if not content or len(content) > 20:
                raise ValueError(f"Invalid version format: {content}")
            
            # Check if it looks like a version number
            if not any(c.isdigit() for c in content):
                raise ValueError(f"Invalid version format (no digits): {content}")
            
            self.version_checked.emit(True, content, "")
            logger.info(f"Remote version fetched: {content}")
            
        except urllib.error.HTTPError as e:
            error_msg = f"HTTP Error {e.code}: {e.reason}"
            if e.code == 403:
                error_msg += " (File not publicly accessible - check SharePoint permissions)"
            elif e.code == 404:
                error_msg += " (File not found - check URL)"
            logger.warning(f"Version check failed: {error_msg}")
            self.version_checked.emit(False, "", error_msg)
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Version check failed: {error_msg}")
            self.version_checked.emit(False, "", error_msg)


class UpdateManager:
    """Manages update reminders and download links for the application."""
    
    # Public OneDrive/SharePoint link for the installer (added &download=1)
    LATEST_EXE_URL = ("https://hitachigroupeur-my.sharepoint.com/:u:/g/personal/ashish_dixit_hitachirail_com/IQDRPzTnhhg6RZh0lmqAYheYAcObwZhAMwFOI72Vn9IAkEQ?e=S5p8Lr&download=1")
    
    # Direct link to version.txt (must be publicly accessible)
    # Update this URL to point to your hosted version.txt file
    VERSION_CHECK_URL = ("https://raw.githubusercontent.com/ashish45d/mlk-ii-alvt-updates/refs/heads/main/version.txt")
    
    # Set 0 to disable the startup reminder; default 30 days
    UPDATE_REMINDER_DAYS = 30
    
    # Network timeout for version check (seconds)
    VERSION_CHECK_TIMEOUT = 5
    
    def __init__(self, parent, settings, current_version):
        """
        Initialize update manager.
        
        Args:
            parent: Parent Qt widget
            settings: QSettings instance for persistent storage
            current_version: Current installed version string (e.g., "12.5.8")
        """
        self.parent = parent
        self.settings = settings
        self.current_version = current_version
        self.version_check_thread = None
    
    def shutdown(self):
        """
        Safely shutdown the update manager.
        Should be called during application close to prevent threads from causing crashes.
        """
        try:
            if self.version_check_thread:
                # Check if the C++ object still exists before calling methods on it
                # accessing isRunning() on a deleted C++ object raises RuntimeError in PySide6
                if self.version_check_thread.isRunning():
                    logger.info("Cleaning up UpdateManager background thread...")
                    
                    # Disconnect signals to prevent them from firing during/after shutdown
                    try:
                        self.version_check_thread.version_checked.disconnect()
                    except (RuntimeError, TypeError):
                        pass
                    
                    # Request stop and wait for a short time
                    self.version_check_thread.terminate()
                    self.version_check_thread.wait(500) # Wait up to 0.5s
                    logger.debug("UpdateManager background thread cleaned up.")
        except (RuntimeError, AttributeError):
            # This happens if Qt has already deleted the object (e.g. via parent/child system)
            # We can safely ignore this during application shutdown.
            pass
        except Exception as e:
            logger.debug(f"UpdateManager shutdown notice: {e}")

    def open_latest_download(self):
        """
        Opens the latest installer link in the default browser.
        Safe for corporate environments; no self-update while running.
        """
        try:
            # Allow runtime override via QSettings if you ever need
            url = self.settings.value("update/latest_exe_url", "", str).strip()
        except Exception:
            url = ""
        if not url:
            url = self.LATEST_EXE_URL
        if not url:
            QMessageBox.warning(self.parent, "Download Latest", 
                              "Download link is not configured yet.")
            return
        
        # Ensure we are on the main thread for UI operations
        try:
            if hasattr(self.parent, '_log'):
                self.parent._log("INFO", f"[UPDATE] Opening browser: {url}")
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl(url))
    
    def _is_newer_version(self, remote_version):
        """
        Compare remote version with current installed version.
        
        Args:
            remote_version: Version string from remote server (e.g., "12.5.9")
            
        Returns:
            bool: True if remote version is newer than current version
        """
        try:
            # Try semantic versioning if library exists
            try:
                from packaging import version
                return version.parse(remote_version) > version.parse(self.current_version)
            except (ImportError, Exception):
                # Fallback to simple dot-separated integer comparison
                def ver_to_tuple(v):
                    # Strip any non-version suffix like -beta
                    v_clean = v.split('-')[0].split('+')[0]
                    return tuple(map(int, (re.sub(r'[^0-9.]', '', v_clean).split('.'))))
                
                import re
                try:
                    return ver_to_tuple(remote_version) > ver_to_tuple(self.current_version)
                except Exception:
                    # Last resort: just string comparison if they match format
                    return remote_version > self.current_version
        except Exception as e:
            logger.error(f"Version comparison failed: {e}")
            return False
    
    def _on_version_checked(self, success, remote_version, error_msg, is_manual=False):
        """
        Callback when version check thread completes.
        
        Args:
            success: Whether version fetch was successful
            remote_version: Remote version string (empty if failed)
            error_msg: Error message (empty if successful)
            is_manual: Whether this was a user-initiated check
        """
        try:
            # Check if parent still exists (Qt will return False for deleted C++ objects)
            if not self.parent or (hasattr(self.parent, 'isHidden') and self.parent is None):
                return

            if not success:
                if is_manual:
                     QMessageBox.warning(self.parent, "Update Check Failed", 
                                       f"Could not connect to update server:\n{error_msg}")
                else:
                    logger.debug(f"Automatic update check skipped: {error_msg}")
                return
            
            if not remote_version:
                return

            # Compare versions
            if not self._is_newer_version(remote_version):
                if is_manual:
                     QMessageBox.information(self.parent, "Up to Date", 
                                          f"You are currently using the latest version ({self.current_version}).")
                else:
                    logger.info(f"Already on latest version (current: {self.current_version}, remote: {remote_version})")
                return
            
            # If not manual, check if we should suppress the reminder
            if not is_manual:
                last_ver = self.settings.value("update/last_reminded_version", "", str)
                last_ts = self.settings.value("update/last_reminder_ts", "", str)
                
                # 1. If this is a NEW version we haven't told them about yet, always show it!
                if remote_version != last_ver:
                    logger.info(f"New version {remote_version} found (different from last reminded: {last_ver})")
                    pass 
                else:
                    # 2. If it's the SAME version we already reminded them about, respect the interval
                    days = int(self.UPDATE_REMINDER_DAYS or 30)
                    if last_ts:
                        try:
                            last_dt = datetime.fromisoformat(last_ts)
                            # Only skip if within the reminder period
                            if (datetime.now() - last_dt) < timedelta(days=days):
                                logger.debug(f"Reminder for {remote_version} skipped (already shown within {days} days)")
                                return
                        except Exception:
                            pass

            # Show update notification
            self._show_update_notification(remote_version)
            
        except Exception as e:
            logger.error(f"Error processing version check: {e}")
    
    def _show_update_notification(self, new_version):
        """
        Display update notification dialog.
        
        Args:
            new_version: New version available for download
        """
        try:
            from PySide6.QtCore import Qt
            
            box = QMessageBox(self.parent)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle("Update Available")
            box.setTextFormat(Qt.RichText)
            
            box.setText(
                f"<b>A new version of MLK‑II ALVT is available!</b><br><br>"
                f"📦 <b>Current version:</b> {self.current_version}<br>"
                f"🚀 <b>New version:</b> {new_version}<br><br>"
                f"Click <b>Download Now</b> to get the latest installer."
            )
            
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.button(QMessageBox.Yes).setText("Download Now")
            box.button(QMessageBox.No).setText("Remind Me Later")
            
            # Store notification state
            self.settings.setValue("update/last_reminder_ts", datetime.now().isoformat())
            self.settings.setValue("update/last_reminded_version", new_version)
            
            choice = box.exec()
            if choice == QMessageBox.Yes:
                self.open_latest_download()
                
        except Exception as e:
            logger.error(f"Error showing update notification: {e}")
    
    def check_for_updates(self, is_manual=False):
        """
        Check for updates by fetching remote version and comparing with installed version.
        Runs in background thread to avoid blocking UI.
        
        Args:
            is_manual: If True, bypasses timing restrictions and shows dialog on "Up to Date".
        """
        try:
            # Thread safety: ensure only one check runs at a time
            if self.version_check_thread and self.version_check_thread.isRunning():
                if is_manual:
                    logger.info("Update check already in progress...")
                return

            # Get version check URL
            version_url = self.settings.value("update/version_check_url", "", str).strip()
            if not version_url:
                version_url = self.VERSION_CHECK_URL
            
            if not version_url:
                logger.warning("Version check URL not configured")
                return
            
            # Start background version check
            logger.info(f"Starting version check at: {version_url} (Manual: {is_manual})")
            self.version_check_thread = VersionCheckThread(version_url, self.VERSION_CHECK_TIMEOUT)
            
            # Internal cleanup wrapper to ensure thread ref is nullified
            def _cleanup():
                if self.version_check_thread:
                    self.version_check_thread.deleteLater()
                    self.version_check_thread = None

            self.version_check_thread.version_checked.connect(
                lambda success, rv, err: self._on_version_checked(success, rv, err, is_manual)
            )
            
            # Ensure the thread is cleaned up when finished
            self.version_check_thread.finished.connect(_cleanup)
            
            self.version_check_thread.start()
            
        except Exception as e:
            # Never block startup on update check issues
            logger.error(f"Update check failed: {e}")
    
    def maybe_remind_update(self):
        """Startup entry point."""
        self.check_for_updates(is_manual=False)

