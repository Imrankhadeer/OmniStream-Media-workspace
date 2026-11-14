import os
import re
import http.cookiejar
from typing import Optional, Dict, Any, List

COOKIE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "cookies.txt"))

# In-memory session cookies storage (Safe: never written to disk unless explicitly requested)
_in_memory_cookies: str = ""

def parse_cookie_string(raw: str) -> str:
    """Normalize any cookie format (Cookie header, bare JWT/token, semicolon-separated, multiline, or Netscape)
    into a standard 'name=value; name2=value2' cookie header string."""
    if not raw or not raw.strip():
        return ""
    
    s = raw.strip()
    # Strip leading 'Cookie:' or 'cookie:' header prefix
    s = re.sub(r'^(?:cookie|Cookie)\s*:\s*', '', s)
    s = re.sub(r'^(?:bearer|Bearer)\s+', '', s)
    
    # Check if raw JWT token or bare session token (no '=' in input)
    if '=' not in s:
        token = s.strip()
        return f"token_v2={token}; reddit_session={token}"
    
    # Netscape format (tab-separated columns)
    if '\t' in s:
        pairs = []
        for line in s.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                name = parts[5].strip()
                val = parts[6].strip()
                if name:
                    pairs.append(f"{name}={val}")
            elif '=' in line:
                pairs.append(line)
        return '; '.join(pairs)
    
    # Multiline or semicolon-delimited key=value
    lines = [l.strip() for l in s.splitlines() if l.strip() and not l.strip().startswith('#')]
    combined = '; '.join(lines)
    pairs = []
    for part in combined.split(';'):
        part = part.strip()
        if '=' in part:
            pairs.append(part)
        elif part.startswith('eyJ'):
            pairs.append(f"token_v2={part}")
            pairs.append(f"reddit_session={part}")
            
    if not pairs and s:
        # Ultimate fallback: treat raw string as session token
        return f"token_v2={s}; reddit_session={s}"
        
    return '; '.join(pairs)

def get_auth_token() -> Optional[str]:
    """Extract raw bearer/JWT token if present in session cookies."""
    global _in_memory_cookies
    if not _in_memory_cookies:
        return None
    for part in _in_memory_cookies.split(';'):
        part = part.strip()
        if '=' in part:
            k, v = part.split('=', 1)
            v = v.strip()
            if v.startswith('eyJ'):
                return v
        elif part.startswith('eyJ'):
            return part
    return None

def get_session_cookies() -> str:
    """Return active session cookies string (prefers in-memory safe cookies, falls back to cookies.txt)."""
    global _in_memory_cookies
    if _in_memory_cookies:
        return _in_memory_cookies
    return get_cookie_header_str()

def set_session_cookies(content: str, persist_to_disk: bool = False) -> Dict[str, Any]:
    """Store session cookies in RAM. Only writes to cookies.txt on disk if persist_to_disk is True."""
    global _in_memory_cookies
    if not content or not content.strip():
        raise ValueError("Cookie content cannot be empty.")
    
    normalized = parse_cookie_string(content)
    if not normalized:
        raise ValueError("Could not parse valid cookies from the provided input.")
        
    _in_memory_cookies = normalized
    
    if persist_to_disk:
        save_cookies_content(content)
    
    return get_cookies_status()

def clear_session_cookies() -> Dict[str, Any]:
    """Clear in-memory session cookies and remove cookies.txt if present."""
    global _in_memory_cookies
    _in_memory_cookies = ""
    delete_cookies_file()
    return get_cookies_status()

def get_cookies_status() -> Dict[str, Any]:
    """Inspect and return current status of cookies (in-memory or on disk)."""
    global _in_memory_cookies
    
    count = 0
    domains = set()
    has_reddit = False
    has_redgifs = False
    is_in_memory = bool(_in_memory_cookies)
    is_persisted = os.path.exists(COOKIE_FILE)
    
    # Inspect in-memory cookies
    if _in_memory_cookies:
        for pair in _in_memory_cookies.split(";"):
            if "=" in pair:
                count += 1
                k = pair.split("=")[0].strip().lower()
                if any(x in k for x in ["reddit", "token", "loid"]):
                    has_reddit = True
                if "redgifs" in k:
                    has_redgifs = True
        domains.add("session (in-memory)")
        if has_reddit:
            domains.add("reddit.com")
        if has_redgifs:
            domains.add("redgifs.com")
            
    # Inspect disk cookies if no in-memory or in addition
    if is_persisted:
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        if not is_in_memory:
                            count += 1
                        domain = parts[0].lstrip(".").lower()
                        domains.add(domain)
                        if "reddit.com" in domain:
                            has_reddit = True
                        if "redgifs.com" in domain:
                            has_redgifs = True
                    elif "=" in line:
                        if not is_in_memory:
                            count += 1
                        if "reddit" in line.lower():
                            has_reddit = True
                        if "redgifs" in line.lower():
                            has_redgifs = True
        except Exception:
            pass

    return {
        "exists": is_in_memory or is_persisted,
        "is_in_memory": is_in_memory,
        "is_persisted": is_persisted,
        "filename": "cookies.txt" if is_persisted else None,
        "count": count,
        "has_reddit": has_reddit,
        "has_redgifs": has_redgifs,
        "domains": sorted(list(domains))[:12]
    }

def save_cookies_content(content: str) -> Dict[str, Any]:
    """Save raw text or converted cookies into cookies.txt."""
    if not content or not content.strip():
        raise ValueError("Cookie content cannot be empty.")
    
    cleaned = content.strip()
    
    if "\t" not in cleaned and "=" in cleaned:
        netscape_lines = ["# Netscape HTTP Cookie File\n# Converted by OmniStream\n"]
        pairs = cleaned.split(";")
        for pair in pairs:
            if "=" in pair:
                k, v = pair.strip().split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    netscape_lines.append(f".reddit.com\tTRUE\t/\tFALSE\t2147483647\t{k}\t{v}\n")
        final_content = "".join(netscape_lines)
    else:
        if not cleaned.startswith("# Netscape"):
            cleaned = "# Netscape HTTP Cookie File\n" + cleaned
        final_content = cleaned

    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    return get_cookies_status()

def delete_cookies_file() -> bool:
    """Delete cookies.txt if present."""
    if os.path.exists(COOKIE_FILE):
        try:
            os.remove(COOKIE_FILE)
            return True
        except Exception:
            return False
    return False

def get_cookie_jar() -> Optional[http.cookiejar.MozillaCookieJar]:
    """Load cookies.txt as MozillaCookieJar for requests/curl_cffi."""
    if not os.path.exists(COOKIE_FILE):
        return None
    try:
        cj = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
        cj.load(ignore_discard=True, ignore_expires=True)
        return cj
    except Exception:
        return None

def get_cookie_header_str() -> str:
    """Format cookies from cookies.txt into a single HTTP Cookie header string."""
    if not os.path.exists(COOKIE_FILE):
        return ""
    pairs = []
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    pairs.append(f"{parts[5]}={parts[6]}")
                elif "=" in line and "\t" not in line:
                    pairs.append(line)
        return "; ".join(pairs)
    except Exception:
        return ""
