"""
CHUNITHM-NET login / cookie-session layer (blocking, requests-based).

Ported from chuni-penguin's login code (adapters/chunithm_net/_hooks.py,
cogs/chunithm/auth.py, ui/login.py). The original is async httpx + SQLAlchemy
and needs Python 3.12; this version uses `requests` and the JSON store the
rest of this bot uses, and stays Python 3.9 safe.

How the session works
---------------------
The only long-lived secret is the `clal` cookie issued by the Aime gateway
(lng-tgk-aime-gw.am-all.net, path /common_auth). A "session" is an LWP cookie
jar seeded with that cookie. When CHUNITHM-NET bounces a request back to its
login page (/mobile/) or to an error page, the session re-authenticates by
visiting the Aime login URL with the clal cookie (which hands out fresh
chunithm-net-eng.com cookies) and retries the request once. The refreshed jar
is serialised back to disk by the caller so the next use starts warm.

  - ChunithmNetSession   one user's jar + requests.Session, with re-auth
  - sega_id_login()      SEGA ID (+ optional 2FA) login -> clal
  - build_lwp_from_clal  clal string -> serialised jar
  - store helpers        data/chunithm_sessions.json  {"users": {id: {"cookie": lwp}}}

Everything here blocks; cogs/login_cog.py runs it with asyncio.to_thread.
"""
import io
import os
import string
import threading
import time
from http.cookiejar import Cookie as HTTPCookie
from http.cookiejar import DefaultCookiePolicy, LWPCookieJar
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import DATA_DIR, _load, _save

SESSIONS_FILE = os.path.join(DATA_DIR, "chunithm_sessions.json")

BASE_URL = "https://chunithm-net-eng.com"
NET_HOST = "chunithm-net-eng.com"
AIME_HOST = "lng-tgk-aime-gw.am-all.net"
AUTH_URL = (
    "https://lng-tgk-aime-gw.am-all.net/common_auth/login"
    "?site_id=chuniex"
    "&redirect_url=https://chunithm-net-eng.com/mobile/"
    "&back_url=https://chunithm.sega.com/"
)
ADD_ACCESS_CODE_URL = "https://common-access.am-all.net/access/code/add"
LWP_HEADER = "#LWP-Cookies-2.0"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/114.0"
)
TIMEOUT = 60
MIN_REQUEST_INTERVAL = 0.1   # 10 requests/second, same budget as the original

_COOKIE_CHARACTERS = set(string.ascii_lowercase + string.digits)
_store_lock = threading.Lock()


# -- errors -----------------------------------------------------------------
class SessionError(Exception):
    """Base class for everything this module raises on purpose."""


class AuthenticationError(SessionError):
    """The clal cookie is invalid/expired, or the login was rejected."""


class NoCardsRegistered(AuthenticationError):
    """The Aime account has no access codes registered."""


class NetworkError(SessionError):
    """The request itself failed (timeout, DNS, connection reset...)."""


class LoginError(SessionError):
    """SEGA ID login failed; the message is safe to show to the user."""


class ChuniNetError(SessionError):
    """CHUNITHM-NET answered with its own /mobile/error/ page."""

    def __init__(self, code: Optional[int], description: str = ""):
        self.code = code
        self.description = description
        super().__init__(
            "CHUNITHM-NET error {}{}".format(
                code if code is not None else "?",
                ": " + description if description else "",
            )
        )


# -- clal helpers -------------------------------------------------------------
def strip_clal_prefix(clal: str) -> str:
    clal = clal.strip()
    return clal[5:] if clal.startswith("clal=") else clal


def is_valid_clal(clal: str) -> bool:
    clal = strip_clal_prefix(clal)
    return len(clal) == 64 and all(c in _COOKIE_CHARACTERS for c in clal)


def _new_jar() -> LWPCookieJar:
    return LWPCookieJar(policy=DefaultCookiePolicy(hide_cookie2=True))


def build_lwp_from_clal(clal: str) -> str:
    """Serialised cookie jar containing only the clal cookie."""
    clal = strip_clal_prefix(clal)
    cookie = HTTPCookie(
        version=0,
        name="clal",
        value=clal,
        port=None,
        port_specified=False,
        domain=AIME_HOST,
        domain_specified=True,
        domain_initial_dot=False,
        path="/common_auth",
        path_specified=True,
        secure=False,
        expires=3856586927,  # 2092-03-17 10:08:47Z, same as the original
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
    )
    jar = _new_jar()
    jar.set_cookie(cookie)
    return "{}\n{}".format(LWP_HEADER, jar.as_lwp_str())


def load_jar(raw: str) -> LWPCookieJar:
    jar = _new_jar()
    # Session-only cookies (discard=True) are skipped on load on purpose, so a
    # restored jar always re-authenticates from the persistent clal cookie.
    jar._really_load(io.StringIO(raw), "?", ignore_discard=False, ignore_expires=False)
    return jar


def extract_clal(raw: str) -> Optional[str]:
    """The clal value inside a serialised jar, or None."""
    for cookie in load_jar(raw):
        if cookie.name == "clal" and cookie.domain.lstrip(".") == AIME_HOST:
            return cookie.value
    return None


def _http_session(user_agent: str) -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(max_retries=Retry(total=5, connect=5, read=0, status=0,
                                            backoff_factor=0.5))
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "user-agent": user_agent,
        "accept-language": "en-US,en;q=0.5",
        "upgrade-insecure-requests": "1",
        "referer": BASE_URL + "/",
    })
    return s


# -- the session --------------------------------------------------------------
class ChunithmNetSession:
    """One user's CHUNITHM-NET session. Not thread-safe; use one per task."""

    def __init__(self, lwp_cookie_jar: str, user_agent: str = DEFAULT_USER_AGENT):
        self._jar = load_jar(lwp_cookie_jar)
        self._http = _http_session(user_agent)
        self._http.cookies = self._jar
        self._last_request = 0.0

    @property
    def lwp_cookie_jar(self) -> str:
        return "{}\n{}".format(LWP_HEADER, self._jar.as_lwp_str())

    def close(self) -> None:
        self._http.close()

    # -- low level -----------------------------------------------------------
    def _send(self, method: str, url: str, **kwargs) -> requests.Response:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        kwargs.setdefault("timeout", TIMEOUT)
        try:
            return self._http.request(method, url, **kwargs)
        except requests.RequestException as e:
            raise NetworkError("{}: {}".format(e.__class__.__name__, e)) from e
        finally:
            self._last_request = time.monotonic()

    @staticmethod
    def _parse_error_page(resp: requests.Response) -> Optional[ChuniNetError]:
        if urlparse(resp.url).path != "/mobile/error/":
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        blocks = soup.select(".block.text_l .font_small")
        code = None
        if blocks:
            try:
                code = int(blocks[0].get_text().split(": ", 1)[1])
            except (IndexError, ValueError):
                pass
        description = blocks[1].get_text(strip=True) if len(blocks) > 1 else ""
        return ChuniNetError(code, description)

    @staticmethod
    def _bounced_to_login(resp: requests.Response) -> bool:
        u = urlparse(resp.url)
        return u.hostname == NET_HOST and u.path == "/mobile/"

    def _reauthenticate(self) -> None:
        """Trade the clal cookie for fresh chunithm-net cookies."""
        resp = self._send("GET", AUTH_URL)
        u = urlparse(resp.url)

        # Still sitting on the Aime login page: clal was rejected.
        if u.hostname == AIME_HOST and u.path == "/common_auth/login":
            raise AuthenticationError(
                "The saved login token is invalid or has expired. Please log in again."
            )

        if u.hostname == AIME_HOST and u.path == "/common_auth/redirect":
            form = BeautifulSoup(resp.text, "html.parser").find("form")
            if form is not None and form.get("action") == ADD_ACCESS_CODE_URL:
                raise NoCardsRegistered(
                    "The account does not have any access codes registered. "
                    "Register one on https://my-aime.net before logging in."
                )

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Request a CHUNITHM-NET page, re-authenticating once if needed.

        Any bounce to the login page or CHUNITHM-NET error page triggers a single
        re-auth + retry; if it still fails the error is raised.
        """
        url = urljoin(BASE_URL + "/", path.lstrip("/"))
        kwargs.setdefault("allow_redirects", True)

        resp = self._send(method, url, **kwargs)
        if self._bounced_to_login(resp) or self._parse_error_page(resp) is not None:
            self._reauthenticate()
            resp = self._send(method, url, **kwargs)
            if self._bounced_to_login(resp):
                raise AuthenticationError(
                    "CHUNITHM-NET rejected the session even after re-authenticating."
                )

        error = self._parse_error_page(resp)
        if error is not None:
            raise error
        return resp

    # -- high level ----------------------------------------------------------
    def verify(self) -> None:
        """Raises SessionError unless the home page loads while logged in."""
        resp = self.request("GET", "/mobile/home/")
        if urlparse(resp.url).hostname != NET_HOST or resp.status_code != 200:
            raise AuthenticationError("Could not load the CHUNITHM-NET home page.")

    def logout(self) -> None:
        """Sign out of CHUNITHM-NET, which makes the clal token unusable."""
        self.request("GET", "/mobile/home/userOption/logout/")


# -- SEGA ID login ------------------------------------------------------------
def sega_id_login(username: str, password: str, otp: Optional[str] = None) -> str:
    """Log in with SEGA ID (and a 2FA code if enabled) and return the clal.

    The credentials only live in this call's stack; nothing is stored or logged.
    Raises LoginError with a user-presentable message.
    """
    s = _http_session(DEFAULT_USER_AGENT)
    sid_url = "https://{}/common_auth/login/sid".format(AIME_HOST)
    otp_url = "https://{}/common_auth/login/otp".format(AIME_HOST)
    otpauth_url = "https://{}/common_auth/login/otpauth".format(AIME_HOST)

    try:
        s.get(AUTH_URL, timeout=TIMEOUT)
        resp = s.post(sid_url, data={"retention": "1", "sid": username,
                                     "password": password},
                      allow_redirects=False, timeout=TIMEOUT)
        location = resp.headers.get("location")
        credentials_ok = False

        if location == otp_url:
            credentials_ok = True
            if not otp:
                raise LoginError("Two-factor authentication was enabled, "
                                 "but a code was not provided.")
            resp = s.post(otpauth_url, data={"password": otp},
                          allow_redirects=False, timeout=TIMEOUT)
            location = resp.headers.get("location")

        if location is None or BASE_URL not in location:
            raise LoginError("Invalid two-factor authentication code."
                             if credentials_ok else "Invalid username or password.")

        clal = next((c.value for c in s.cookies
                     if c.name == "clal" and c.domain.lstrip(".") == AIME_HOST), None)
        if clal is None:
            raise LoginError("Login was successful, but could not retrieve token.")
        return clal
    except requests.RequestException as e:
        raise LoginError("Network error while logging in: {}".format(e.__class__.__name__)) from e
    finally:
        s.close()


# -- persistence --------------------------------------------------------------
def get_cookie(user_id: int) -> Optional[str]:
    """Serialised jar for a user, or None if not logged in."""
    with _store_lock:
        db = _load(SESSIONS_FILE, {"users": {}})
    raw = db.get("users", {}).get(str(user_id), {}).get("cookie")
    return raw if raw and raw.startswith(LWP_HEADER) else None


def set_cookie(user_id: int, lwp_cookie_jar: Optional[str]) -> None:
    """Store a user's jar; None removes the entry (logout)."""
    with _store_lock:
        db = _load(SESSIONS_FILE, {"users": {}})
        db.setdefault("users", {})
        if lwp_cookie_jar is None:
            db["users"].pop(str(user_id), None)
        else:
            db["users"][str(user_id)] = {"cookie": lwp_cookie_jar}
        _save(SESSIONS_FILE, db)
        try:
            os.chmod(SESSIONS_FILE, 0o600)   # holds login tokens; best effort
        except OSError:
            pass
