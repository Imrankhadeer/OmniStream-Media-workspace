import re
import html
import json
import base64
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, urljoin
import requests as std_requests

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def http_get(url: str, headers: dict = None, timeout: int = 10, allow_redirects: bool = True, cookies: dict = None):
    """HTTP GET wrapper with Chrome TLS fingerprint impersonation to bypass Cloudflare bot checks."""
    req_headers = dict(headers or {})
    if 'User-Agent' not in req_headers:
        req_headers['User-Agent'] = DEFAULT_UA

    if cookies is None and 'Cookie' not in req_headers:
        try:
            import cookies_mgr
            session_cookie = cookies_mgr.get_session_cookies()
            if session_cookie:
                req_headers['Cookie'] = session_cookie
            else:
                cookies = cookies_mgr.get_cookie_jar()
        except Exception:
            pass

    if 'Authorization' not in req_headers:
        try:
            import cookies_mgr
            auth_token = cookies_mgr.get_auth_token()
            # Reddit reject cookie JWTs if passed as Bearer tokens to www.reddit.com
            if auth_token and not ('reddit.com' in url and 'oauth.reddit.com' not in url):
                req_headers['Authorization'] = f"Bearer {auth_token}"
        except Exception:
            pass
        
    if HAS_CURL_CFFI and curl_requests is not None:
        return curl_requests.get(
            url,
            headers=req_headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
            cookies=cookies,
            impersonate="chrome124"
        )
    else:
        return std_requests.get(
            url,
            headers=req_headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
            cookies=cookies
        )

def clean_url(raw_url: str) -> str:
    """Clean and normalize URL: remove quotes, spaces, tracking params, markdown."""
    if not raw_url:
        return ""
    
    u = raw_url.strip()
    
    # Handle markdown format like [title](https://...)
    md_match = re.search(r'\((https?://[^\s)]+)\)', u)
    if md_match:
        u = md_match.group(1)
        
    # Strip quotes, brackets, angle brackets
    u = u.strip("'\"<>[]() \t\r\n")
    
    # Auto prepend https:// if missing
    if not u.startswith("http://") and not u.startswith("https://"):
        u = "https://" + u
        
    try:
        parsed = urlparse(u)
        tracking_keys = {
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 
            'utm_content', 'fbclid', 'gclid', 'ref', 'source', 'spm'
        }
        qs = parse_qs(parsed.query, keep_blank_values=True)
        filtered_qs = {k: v for k, v in qs.items() if k.lower() not in tracking_keys}
        
        new_query = urlencode(filtered_qs, doseq=True)
        cleaned = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            "" # strip hash fragments
        ))
        return cleaned.rstrip('/') if parsed.path == '/' else cleaned
    except Exception:
        return u.strip()

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes, terminal color markers, and raw brackets."""
    if not text:
        return ""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    text = re.sub(r'\[[0-9]{1,2}(?:;[0-9]{1,2})*m', '', text)
    text = re.sub(r'^ERROR:\s*', '', text, flags=re.I)
    return text.strip()

def format_friendly_error(err_msg: str, url: str) -> str:
    """Translate raw engine errors into actionable, clear feedback."""
    clean_err = strip_ansi(err_msg)
    domain = urlparse(url).netloc or "the website"

    if "Cloudflare" in clean_err or "anti-bot" in clean_err.lower() or "challenge" in clean_err.lower():
        return (
            f"🛡️ Cloudflare Bot Protection Encountered on {domain}\n\n"
            "This website is presenting an interactive Cloudflare verification challenge to background scrapers.\n\n"
            "Quick 10-Second Solution:\n"
            "1. Open the video page in your browser (where you can watch it).\n"
            "2. Press F12 to open Developer Tools and click the 'Network' tab.\n"
            "3. In the filter box, type 'm3u8' and click Play on the video.\n"
            "4. Right-click the playlist link (e.g. playlist.m3u8) -> Copy URL, and paste it here.\n\n"
            "The direct stream will download immediately without any Cloudflare blocks!"
        )

    if "Unsupported URL" in clean_err:
        return (
            f"Unable to auto-extract video directly from {domain}.\n\n"
            "💡 Tip: This page embeds its video in a custom player. "
            "To download it:\n"
            "1. Open the video in your browser and press F12 (Developer Tools).\n"
            "2. In the Network tab, type 'm3u8' in the filter box.\n"
            "3. Play the video, right-click the stream URL -> Copy URL, and paste it into OmniStream!"
        )
        
    if "cloudwindow" in clean_err.lower() or "cloudwindow" in url.lower() or "surrit" in clean_err.lower() or "surrit" in url.lower():
        return (
            f"🛡️ CDN Hotlink Protection Detected on {domain}\n\n"
            "This streaming CDN (used by MissAV / Surrit) requires the matching Referer header ('https://missav.ai/').\n\n"
            "💡 OmniStream automatically injects this header and downloads the stream segments in parallel at maximum speed!"
        )

    if "403" in clean_err or "Forbidden" in clean_err:
        return (
            f"Access was denied (403 Forbidden) by {domain}.\n\n"
            "💡 Tip: The streaming server rejected the connection. This usually happens when:\n"
            "1. The stream requires a matching Referer header (the app will attempt to inject this automatically).\n"
            "2. The stream security token has expired — refresh the video page in your browser to get a fresh stream URL.\n"
            "3. If you have cookies, paste them in the Cookies field below or place a cookies.txt in the app folder."
        )

    if "timed out" in clean_err.lower() or "timeout" in clean_err.lower():
        return (
            "Connection timed out while reaching the remote video server.\n\n"
            "💡 Tip: The streaming CDN host might be slow, down, or blocking direct traffic. "
            "Please check if the video plays in your browser or try an alternate stream mirror if available."
        )

    return clean_err

def unpack_dean_edwards(packed_code: str) -> str:
    """Unpack Dean Edwards p,a,c,k packed JavaScript."""
    match = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)",
        packed_code,
        re.DOTALL
    )
    if not match:
        return ""

    p, a, c, k = match.groups()
    radix = int(a)
    count = int(c)
    words = k.split('|')

    digits = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    def unbase(val, base):
        res = 0
        for char in val:
            res = res * base + digits.index(char)
        return res

    def lookup(m):
        w = m.group(0)
        try:
            idx = unbase(w, radix)
            if idx < len(words) and words[idx]:
                return words[idx]
        except Exception:
            pass
        return w

    try:
        return re.sub(r'\b[0-9a-zA-Z]+\b', lookup, p)
    except Exception:
        return ""

def is_stream_reachable(m3u8_url: str, referer: str) -> bool:
    """Quick check to verify that an m3u8 playlist is responsive."""
    try:
        headers = {
            'User-Agent': DEFAULT_UA,
            'Referer': referer,
            'Origin': referer.rstrip('/')
        }
        r = http_get(m3u8_url, headers=headers, timeout=4)
        return r.status_code == 200
    except Exception:
        return False

def parse_hls_qualities(m3u8_url: str, referer: str = None):
    """Parse #EXT-X-STREAM-INF from HLS master playlist to return available resolutions."""
    qualities = []
    try:
        eff_referer = referer
        if not eff_referer:
            if any(k in m3u8_url for k in ['surrit.com', 'cloudwindow-route.com', 'sixyik.com']):
                eff_referer = 'https://missav.ai/'
            else:
                parsed = urlparse(m3u8_url)
                eff_referer = f"{parsed.scheme}://{parsed.netloc}/"
        headers = {'User-Agent': DEFAULT_UA, 'Referer': eff_referer}
        r = http_get(m3u8_url, headers=headers, timeout=6)
        if r.status_code == 200 and '#EXT-X-STREAM-INF' in r.text:
            lines = r.text.splitlines()
            for i, line in enumerate(lines):
                if line.startswith('#EXT-X-STREAM-INF:'):
                    res_m = re.search(r'RESOLUTION=(\d+x\d+)', line)
                    bw_m = re.search(r'BANDWIDTH=(\d+)', line)
                    res_str = res_m.group(1) if res_m else "HD"
                    height = res_str.split('x')[1] if 'x' in res_str else res_str
                    mbps = f" ({round(int(bw_m.group(1))/1_000_000, 1)} Mbps)" if bw_m else ""
                    
                    try:
                        h_num = int(height)
                        if h_num >= 2160:
                            qlbl = f"4K UHD {h_num}p{mbps}"
                        elif h_num >= 1440:
                            qlbl = f"2K QHD {h_num}p{mbps}"
                        elif h_num >= 1080:
                            qlbl = f"1080p Full HD{mbps}"
                        elif h_num >= 720:
                            qlbl = f"720p HD{mbps}"
                        else:
                            qlbl = f"{h_num}p{mbps}"
                    except Exception:
                        qlbl = f"{height}p{mbps}"

                    sub_url = ""
                    for j in range(i+1, len(lines)):
                        l = lines[j].strip()
                        if l and not l.startswith('#'):
                            sub_url = urljoin(m3u8_url, l)
                            break
                    if sub_url:
                        qualities.append({
                            'id': sub_url,
                            'resolution': f"{height}p",
                            'label': qlbl,
                            'sub_url': sub_url
                        })
    except Exception:
        pass
    return qualities

def resolve_missav(url: str, custom_cookies: str = None):
    """Deep resolver for MissAV and its CDN stream backends (surrit.com, cloudwindow-route.com, sixyik.com)."""
    cleaned = clean_url(url)
    
    # 1. Direct stream link from surrit or cloudwindow
    if any(k in cleaned for k in ['surrit.com', 'cloudwindow-route.com', 'sixyik.com']):
        formats = parse_hls_qualities(cleaned, referer="https://missav.ai/")
        return {
            'stream_url': cleaned,
            'referer': 'https://missav.ai/',
            'title': 'MissAV Stream',
            'thumbnail': None,
            'formats': formats
        }
        
    # 2. MissAV webpage
    try:
        headers = {
            'User-Agent': DEFAULT_UA,
            'Referer': 'https://missav.ai/'
        }
        if custom_cookies:
            headers['Cookie'] = custom_cookies
            
        r = http_get(cleaned, headers=headers, timeout=10)
        if r.status_code == 200:
            html = r.text
            
            # Extract Title & Thumbnail
            title_m = re.search(r'<title>(.*?)</title>', html, re.I)
            title = title_m.group(1).replace(' - MissAV', '').replace(' | MissAV', '').strip() if title_m else 'MissAV Video'
            
            og_img = re.search(r'<meta\s+property=[\"\']og:image[\"\']\s+content=[\"\']([^\"\']+)[\"\']', html, re.I)
            thumbnail = og_img.group(1) if og_img else None
            
            stream_url = None
            
            # Direct surrit UUID match
            match = re.search(r'surrit\.com\\?/([0-9a-f\-]{36})', html)
            if match:
                video_uuid = match.group(1)
                stream_url = f"https://surrit.com/{video_uuid}/playlist.m3u8"
            else:
                # Check eval unpackers
                scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
                for s in scripts:
                    if 'eval(function(p,a,c,k' in s and any(k in s for k in ['surrit', 'm3u8', 'source']):
                        unpacked = unpack_dean_edwards(s)
                        m_m3u8 = re.search(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', unpacked)
                        if m_m3u8:
                            stream_url = m_m3u8.group(0).replace(r'\/', '/')
                            break
                            
            if stream_url:
                formats = parse_hls_qualities(stream_url, referer="https://missav.ai/")
                return {
                    'stream_url': stream_url,
                    'referer': 'https://missav.ai/',
                    'title': title,
                    'thumbnail': thumbnail,
                    'formats': formats
                }
    except Exception:
        pass
        
    return None

def resolve_noodle_magazine(url: str, custom_cookies: str = None):
    """Deep resolver for NoodleMagazine URLs to bypass yt-dlp's 403 error."""
    try:
        headers = {
            'User-Agent': DEFAULT_UA,
            'Referer': 'https://noodlemagazine.com/'
        }
        if custom_cookies:
            headers['Cookie'] = custom_cookies

        r = http_get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        
        # Title
        title_match = re.search(r'"name":\s*"([^"]+)"', r.text)
        title = html.unescape(title_match.group(1)) if title_match else "NoodleMagazine_Video"
        
        # Thumbnail
        thumb_match = re.search(r'"thumbnailUrl":\s*"([^"]+)"', r.text)
        thumbnail = thumb_match.group(1) if thumb_match else None
        
        # window.playlist
        pl_match = re.search(r'window\.playlist\s*=\s*(\{.*?\});', r.text, re.DOTALL)
        if pl_match:
            try:
                raw_json = pl_match.group(1).replace(r'\u0026', '&')
                pl_data = json.loads(raw_json)
                sources = pl_data.get('sources', [])
                if sources:
                    formats = []
                    for s in sources:
                        lbl = s.get('label', 'HD')
                        formats.append({
                            'id': s['file'],
                            'resolution': f"{lbl}p",
                            'label': f"{lbl}p (Direct MP4)",
                            'url': s['file']
                        })
                    best_file = sources[0]['file']
                    return {
                        'stream_url': best_file,
                        'referer': 'https://noodlemagazine.com/',
                        'title': title,
                        'thumbnail': thumbnail or pl_data.get('image'),
                        'formats': formats
                    }
            except Exception:
                pass
    except Exception:
        pass
    return None

def resolve_jav_guru(url: str, custom_cookies: str = None):
    """Deep resolver for jav.guru articles with embed gateways."""
    headers = {
        'User-Agent': DEFAULT_UA,
        'Referer': 'https://jav.guru/',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    if custom_cookies:
        headers['Cookie'] = custom_cookies
    
    try:
        r = http_get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return None
        
        # Get and clean title
        title_match = re.search(r'<title>(.*?)</title>', r.text, re.I)
        raw_title = title_match.group(1) if title_match else "JavGuru_Video"
        page_title = html.unescape(raw_title).split('⋆')[0].split('&#8902;')[0].strip()

        # Extract poster image
        poster_match = re.search(r'<meta property="og:image" content="([^"]+)"', r.text)
        thumbnail = poster_match.group(1) if poster_match else None

        # Find all encoded iframe gateways
        encoded_urls = re.findall(r'"iframe_url":\s*"([^"]+)"', r.text)
        encoded_urls.reverse()
        
        for enc in encoded_urls:
            try:
                gateway_url = base64.b64decode(enc).decode('utf-8')
                if not thumbnail and 'bg=' in gateway_url:
                    from urllib.parse import unquote
                    bg_m = re.search(r'bg=([^&]+)', gateway_url)
                    if bg_m:
                        thumbnail = unquote(bg_m.group(1))

                r_gw = http_get(gateway_url, headers={'User-Agent': DEFAULT_UA, 'Referer': url}, timeout=3)
                keys_match = re.search(r"data-[a-z0-9]+=\"([a-z0-9]+)\"[^>]*data-[a-z0-9]+=\"([a-z0-9]+)\"[^>]*data-[a-z0-9]+=\"([a-z0-9]+)\"", r_gw.text)
                rtype_match = re.search(r"rtype:\s*'([^']+)'", r_gw.text)
                
                if keys_match and rtype_match:
                    p1, p2, p3 = keys_match.groups()
                    rtype = rtype_match.group(1)
                    full_token = (p1 + p2 + p3)[::-1]
                    real_gateway = f"https://jav.guru/searcho/?{rtype}r={full_token}"
                    
                    r_target = http_get(
                        real_gateway,
                        headers={'User-Agent': DEFAULT_UA, 'Referer': gateway_url},
                        timeout=4,
                        allow_redirects=True
                    )
                    
                    embed_url = r_target.url
                    parsed_embed = urlparse(embed_url)
                    embed_referer = f"{parsed_embed.scheme}://{parsed_embed.netloc}/"
                    
                    # 1. Unpack Dean Edwards scripts if present
                    if 'eval(function(p,a,c,k' in r_target.text:
                        unpacked = unpack_dean_edwards(r_target.text)
                        m3u8s = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', unpacked)
                        for m in m3u8s:
                            if is_stream_reachable(m, embed_referer):
                                return {
                                    'stream_url': m,
                                    'referer': embed_referer,
                                    'title': page_title,
                                    'thumbnail': thumbnail
                                }

                    # 2. Direct m3u8 or mp4
                    m3u8s = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', r_target.text)
                    for m in m3u8s:
                        if is_stream_reachable(m, embed_referer):
                            return {
                                'stream_url': m,
                                'referer': embed_referer,
                                'title': page_title,
                                'thumbnail': thumbnail
                            }
            except Exception:
                continue

    except Exception:
        pass
    return None

def resolve_redgifs(url: str):
    """Deep resolver for RedGifs using official v2 API."""
    try:
        m = re.search(r'redgifs\.com/(?:watch/|ifr/)?([a-zA-Z0-9]+)', url, re.I)
        if not m:
            return None
        gif_id = m.group(1).lower()
        
        # 1. Get temporary auth token
        auth_r = http_get('https://api.redgifs.com/v2/auth/temporary', timeout=6)
        if auth_r.status_code != 200:
            return None
        token = auth_r.json().get('token')
        if not token:
            return None
            
        # 2. Fetch gif metadata
        headers = {
            'Authorization': f'Bearer {token}',
            'User-Agent': DEFAULT_UA
        }
        api_url = f'https://api.redgifs.com/v2/gifs/{gif_id}'
        r = http_get(api_url, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            gif = data.get('gif', {})
            urls = gif.get('urls', {})
            hd_url = urls.get('hd') or urls.get('sd')
            if hd_url:
                poster = urls.get('poster') or urls.get('thumbnail')
                username = gif.get('userName', 'RedGifs')
                formats = []
                if urls.get('hd'):
                    formats.append({'id': urls['hd'], 'resolution': '1080p', 'label': '🌟 1080p Full HD (Direct MP4)'})
                if urls.get('sd'):
                    formats.append({'id': urls['sd'], 'resolution': '720p', 'label': '📱 720p / SD (Direct MP4)'})
                
                return {
                    'stream_url': hd_url,
                    'referer': 'https://www.redgifs.com/',
                    'title': f"RedGifs - {gif_id} ({username})",
                    'thumbnail': poster,
                    'formats': formats
                }
    except Exception:
        pass
    return None

def resolve_page_stream(url: str, custom_cookies: str = None):
    """Universal stream resolver using TLS fingerprint impersonation."""
    cleaned = clean_url(url)
    
    # 1. Direct stream link
    if '.m3u8' in cleaned or '.mp4' in cleaned:
        parsed = urlparse(cleaned)
        eff_ref = f"{parsed.scheme}://{parsed.netloc}/"
        if any(k in cleaned for k in ['surrit.com', 'cloudwindow-route.com', 'sixyik.com']):
            eff_ref = 'https://missav.ai/'
        return {
            'stream_url': cleaned,
            'referer': eff_ref,
            'title': None,
            'thumbnail': None
        }

    # 2. MissAV & CDN resolver
    if any(k in cleaned.lower() for k in ['missav', 'surrit.com', 'cloudwindow-route.com', 'sixyik.com', '123av.org']):
        res = resolve_missav(cleaned, custom_cookies)
        if res:
            return res

    # 3. RedGifs resolver
    if 'redgifs.com' in cleaned:
        res = resolve_redgifs(cleaned)
        if res:
            return res

    # 3. Specialized portal resolvers
    if 'noodlemagazine.com' in cleaned:
        res = resolve_noodle_magazine(cleaned, custom_cookies)
        if res:
            return res

    if 'jav.guru' in cleaned:
        res = resolve_jav_guru(cleaned, custom_cookies)
        if res:
            return res

    # 3. Generic page search with Chrome TLS impersonation
    try:
        headers = {'User-Agent': DEFAULT_UA, 'Referer': cleaned}
        if custom_cookies:
            headers['Cookie'] = custom_cookies

        r = http_get(cleaned, headers=headers, timeout=8)
        if r.status_code == 200:
            parsed = urlparse(cleaned)
            origin = f"{parsed.scheme}://{parsed.netloc}/"

            # Check poster
            poster_match = re.search(r'<meta property="og:image" content="([^"]+)"', r.text)
            thumbnail = poster_match.group(1) if poster_match else None
            
            # Title
            title_m = re.search(r'<title>(.*?)</title>', r.text, re.I)
            title = title_m.group(1).strip() if title_m else None

            # Check direct m3u8 or mp4
            m3u8s = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', r.text)
            if m3u8s:
                return {
                    'stream_url': m3u8s[0],
                    'referer': origin,
                    'title': title,
                    'thumbnail': thumbnail
                }
            # Check packed scripts
            if 'eval(function(p,a,c,k' in r.text:
                unpacked = unpack_dean_edwards(r.text)
                m3u8s = re.findall(r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*', unpacked)
                if m3u8s:
                    return {
                        'stream_url': m3u8s[0],
                        'referer': origin,
                        'title': title,
                        'thumbnail': thumbnail
                    }
    except Exception:
        pass

    return None
