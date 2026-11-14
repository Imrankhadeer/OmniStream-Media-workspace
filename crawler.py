import re
import os
import html
import json
import uuid
import time
import urllib.parse
from typing import List, Dict, Any, Optional
import requests as std_requests

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None
    HAS_CURL_CFFI = False

import cookies_mgr

MEDIA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.redgifs.com/'
}

# Dedicated persistent sessions
_redgifs_session = std_requests.Session()
_redgifs_session.headers.update(MEDIA_HEADERS)

def _http_get(url: str, **kwargs):
    """Execute GET request with Chrome TLS impersonation if available to bypass edge blocks."""
    if HAS_CURL_CFFI and curl_requests is not None:
        kwargs.setdefault('impersonate', 'chrome120')
        return curl_requests.get(url, **kwargs)
    return std_requests.get(url, **kwargs)

# ── RedGifs API Engine (Extracted from D:\Programing\Downloader-app) ─────────────

_cached_redgifs_token: str = ""
_redgifs_token_expires: float = 0.0

def get_redgifs_token() -> str:
    """Fetch temporary bearer token from RedGifs v2 API with in-memory caching."""
    global _cached_redgifs_token, _redgifs_token_expires
    now = time.time()
    if _cached_redgifs_token and now < _redgifs_token_expires:
        return _cached_redgifs_token
    try:
        r = _redgifs_session.get('https://api.redgifs.com/v2/auth/temporary', timeout=10)
        r.raise_for_status()
        tok = r.json().get('token', '')
        if tok:
            _cached_redgifs_token = tok
            _redgifs_token_expires = now + 7200 # Cache for 2 hours
            _redgifs_session.headers['Authorization'] = f'Bearer {tok}'
        return tok
    except Exception as e:
        print(f"[RedGifs] Token error: {e}")
        return _cached_redgifs_token or ""

def redgifs_auth_headers() -> dict:
    tok = get_redgifs_token()
    return {**MEDIA_HEADERS, 'Authorization': f'Bearer {tok}'} if tok else MEDIA_HEADERS

def parse_redgifs_url(raw: str):
    """Returns (kind, username, collection_id)
       kind = 'collection' | 'user' | 'collections_list' | 'search' | 'watch'
    """
    raw = (raw or "").strip().rstrip('/')
    # 1. Watch single GIF URL
    m = re.search(r'redgifs\.com/(?:watch|ifr)/([a-zA-Z0-9]+)', raw, re.I)
    if m:
        return 'watch', m.group(1).lower(), None

    # 2. Collection URL
    m = re.match(r'https?://(?:www\.)?redgifs\.com/users/([^/?#]+)/collections/([^/?#]+)', raw, re.I)
    if m:
        return 'collection', m.group(1), m.group(2)

    # 3. Collections list URL
    m = re.match(r'https?://(?:www\.)?redgifs\.com/users/([^/?#]+)/collections$', raw, re.I)
    if m:
        return 'collections_list', m.group(1), None

    # 4. User URL or @username
    m = re.match(r'https?://(?:www\.)?redgifs\.com/users/([^/?#]+)', raw, re.I)
    if m:
        return 'user', m.group(1), None
    if raw.startswith('@') or raw.startswith('u/'):
        return 'user', raw.lstrip('@u/').split('/')[0], None

    # Default: search tag / query
    return 'search', raw, None

def redgif_to_dict(g: dict) -> dict:
    """Map raw RedGifs JSON to normalized OmniStream media item."""
    urls = g.get('urls', {})
    hd = urls.get('hd') or urls.get('sd') or ''
    gid = g.get('id', '')
    poster = urls.get('poster') or urls.get('thumbnail') or ''
    dur_sec = int(g.get('duration', 0)) if g.get('duration') else 0
    dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec >= 60 else f"{dur_sec}s" if dur_sec else "GIF"

    return {
        'id': gid,
        'title': f"RedGifs - {gid} ({g.get('userName', 'RedGifs')})",
        'url': f"https://www.redgifs.com/watch/{gid}",
        'direct_stream': hd,
        'hd': hd,
        'sd': urls.get('sd', ''),
        'thumbnail': poster,
        'duration': dur_str,
        'source': 'redgifs',
        'type': 'video' if g.get('hasAudio') else 'gif',
        'has_audio': g.get('hasAudio', False),
        'views': g.get('views', 0),
        'likes': g.get('likes', 0),
        'tags': g.get('tags', [])[:4],
        'author': g.get('userName', ''),
        'is_reddit': False
    }

def crawl_redgifs(query: str, count: int = 24, order: str = "trending") -> Dict[str, Any]:
    """Crawl RedGifs by user profile, collection, single watch link, or tag search."""
    q = (query or "").strip()
    if not q:
        return {"items": [], "error": "Search tag, creator username, or RedGIFs link is required."}

    kind, target, collection_id = parse_redgifs_url(q)
    headers = redgifs_auth_headers()

    # 1. Single GIF watch URL
    if kind == 'watch':
        try:
            r = _redgifs_session.get(f'https://api.redgifs.com/v2/gifs/{target}', headers=headers, timeout=10)
            if r.status_code == 200:
                g = r.json().get('gif', {})
                it = redgif_to_dict(g)
                return {"items": [it], "count": 1, "query": q}
        except Exception as e:
            return {"items": [], "error": f"Error fetching RedGIFs item: {e}"}
        return {"items": [], "error": "RedGIFs item not found."}

    # 2. Collections list for a user
    if kind == 'collections_list':
        try:
            r = _redgifs_session.get(f'https://api.redgifs.com/v2/users/{target}/collections', headers=headers, timeout=15)
            r.raise_for_status()
            cols = r.json().get('collections', [])
            all_items = []
            for col in cols[:4]:
                col_id = col.get('folderId')
                sub_r = _redgifs_session.get(f'https://api.redgifs.com/v2/users/{target}/collections/{col_id}/gifs',
                                     params={'count': min(count, 40), 'page': 1}, headers=headers, timeout=15)
                if sub_r.ok:
                    for g in sub_r.json().get('gifs', []):
                        all_items.append(redgif_to_dict(g))
            return {"items": all_items, "count": len(all_items), "query": q, "collections": cols}
        except Exception as e:
            return {"items": [], "error": f"Error fetching user collections: {e}"}

    # 3. Specific Collection or User or Tag Search
    try:
        all_gifs = []
        page = 1
        valid_orders = {"trending", "latest", "top", "top7", "top28", "score"}
        order_param = "latest" if order in ["new", "latest"] else order if order in valid_orders else "trending"

        while len(all_gifs) < count:
            if kind == 'collection':
                api_url = f'https://api.redgifs.com/v2/users/{target}/collections/{collection_id}/gifs'
                params = {'count': min(80, count - len(all_gifs)), 'page': page}
            elif kind == 'user':
                api_url = f'https://api.redgifs.com/v2/users/{target}/gifs'
                params = {'count': min(80, count - len(all_gifs)), 'page': page, 'order': order_param}
            else:
                api_url = 'https://api.redgifs.com/v2/gifs/search'
                params = {'search_text': target, 'count': min(80, count - len(all_gifs)), 'page': page, 'order': order_param}

            r = _redgifs_session.get(api_url, params=params, headers=headers, timeout=15)
            if not r.ok:
                if len(all_gifs) > 0:
                    break
                return {"items": [], "error": f"RedGifs returned status {r.status_code}."}

            data = r.json()
            batch = data.get('gifs', [])
            if not batch:
                break
            all_gifs.extend(batch)
            if page >= data.get('pages', 1):
                break
            page += 1

        items = [redgif_to_dict(g) for g in all_gifs[:count]]
        return {"items": items, "count": len(items), "query": q}
    except Exception as e:
        return {"items": [], "error": f"RedGifs query failed: {str(e)}"}

# ── Reddit Media Engine (Extracted from D:\Programing\Downloader-app) ───────────

def proxy_thumb(raw: str) -> str:
    """Format URL through local proxy if target CDN blocks direct browser hotlinking."""
    if not raw or not isinstance(raw, str) or not raw.startswith('http'):
        return raw or ""
    # Domain check for hotlink-protected CDNs
    if any(d in raw for d in ('preview.redd.it', 'redditmedia.com', 'thumbs2.redgifs.com', 'thumbs.redgifs.com')):
        return f"/proxy?url={urllib.parse.quote(raw, safe='')}"
    return raw

def clean_reddit_thumbnail(data: dict, default_fallback: str = "") -> str:
    """Extract clean, high-resolution thumbnail from Reddit post metadata."""
    thumb = data.get('thumbnail', '')
    if thumb and isinstance(thumb, str) and thumb.startswith('http'):
        clean = html.unescape(thumb).replace('&amp;', '&')
        return proxy_thumb(clean)

    preview_images = data.get('preview', {}).get('images', [])
    if preview_images and isinstance(preview_images, list) and len(preview_images) > 0:
        first_img = preview_images[0]
        resolutions = first_img.get('resolutions', [])
        if resolutions and len(resolutions) > 0:
            res_url = resolutions[-1].get('url', '')
            if res_url:
                clean = html.unescape(res_url).replace('&amp;', '&')
                return proxy_thumb(clean)
        source_url = first_img.get('source', {}).get('url', '')
        if source_url:
            clean = html.unescape(source_url).replace('&amp;', '&')
            return proxy_thumb(clean)

    return proxy_thumb(default_fallback)

def parse_reddit_children(children: list) -> List[dict]:
    """Extract videos, galleries, RedGifs embeds, and image submissions from Reddit posts.
    Battle-tested parser from D:\\Programing\\Downloader-app."""
    items = []
    for child in children:
        data = child.get('data', {})
        if not data:
            continue

        post_id = data.get('id', str(uuid.uuid4())[:8])
        title = data.get('title') or data.get('link_title') or data.get('body') or 'Reddit Media'
        if len(title) > 130:
            title = title[:127] + '...'
        subreddit = data.get('subreddit_name_prefixed', 'r/reddit')
        permalink = 'https://www.reddit.com' + data.get('permalink', '') if data.get('permalink') else data.get('url', '')
        author = data.get('author', '')
        ups = data.get('ups', 0)
        over_18 = data.get('over_18', False)
        created_utc = data.get('created_utc', 0)

        thumb = clean_reddit_thumbnail(data)

        # 1. Multi-Image Gallery
        if data.get('is_gallery') and 'media_metadata' in data:
            meta = data.get('media_metadata', {})
            gallery_items = data.get('gallery_data', {}).get('items', [])
            images = []
            idx = 1
            for gitem in gallery_items:
                media_id = gitem.get('media_id')
                mdata = meta.get(media_id, {})
                if mdata.get('status') == 'valid':
                    s = mdata.get('s', {})
                    img_url = html.unescape(s.get('u', '')).replace('&amp;', '&')
                    if img_url:
                        images.append({
                            'idx': idx,
                            'url': img_url,
                            'id': f"{post_id}_{idx}"
                        })
                        idx += 1
            if images:
                cover_url = images[0]['url']
                items.append({
                    'id': post_id,
                    'title': title,
                    'subreddit': subreddit,
                    'type': 'gallery',
                    'is_gallery': True,
                    'gallery_id': post_id,
                    'gallery_title': title,
                    'images': images,
                    'count': len(images),
                    'duration': f"Gallery ({len(images)})",
                    'url': permalink,
                    'hd': cover_url,
                    'thumbnail': thumb or cover_url,
                    'ups': ups,
                    'author': author,
                    'over_18': over_18,
                    'created_utc': created_utc,
                    'source': 'reddit',
                    'is_reddit': True
                })
            continue

        # Check crosspost parent list
        media = data.get('secure_media') or data.get('media')
        raw_url = data.get('url_overridden_by_dest') or data.get('url') or permalink
        url = html.unescape(raw_url).replace('&amp;', '&')
        domain = data.get('domain', '')

        if not media and data.get('crosspost_parent_list'):
            parent = data['crosspost_parent_list'][0]
            media = parent.get('secure_media') or parent.get('media')
            if not url or url == permalink:
                url = parent.get('url_overridden_by_dest') or parent.get('url') or permalink

        # 2. Reddit Native Video (v.redd.it)
        is_video = data.get('is_video', False)
        if (is_video and media and 'reddit_video' in media) or (media and 'reddit_video' in media):
            rv = media['reddit_video']
            fallback_url = rv.get('fallback_url', '') or rv.get('hls_url', '')
            dur_sec = rv.get('duration', 0)
            dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec >= 60 else f"{dur_sec}s" if dur_sec else "Video"
            items.append({
                'id': post_id,
                'title': title,
                'subreddit': subreddit,
                'type': 'video',
                'url': permalink,
                'hd': permalink,
                'stream_url': fallback_url,
                'fallback_url': fallback_url,
                'thumbnail': thumb or fallback_url,
                'duration': dur_str,
                'ups': ups,
                'author': author,
                'over_18': over_18,
                'created_utc': created_utc,
                'source': 'reddit',
                'is_reddit': True,
                'use_ytdlp': True
            })
            continue

        # 3. RedGifs Link inside Reddit
        if 'redgifs.com' in domain or 'redgifs.com' in url:
            items.append({
                'id': post_id,
                'title': title,
                'subreddit': subreddit,
                'type': 'video',
                'url': url,
                'hd': url,
                'redgifs_url': url,
                'thumbnail': thumb,
                'duration': 'RedGifs',
                'ups': ups,
                'author': author,
                'over_18': over_18,
                'created_utc': created_utc,
                'source': 'redgifs',
                'is_reddit': True
            })
            continue

        # 4. Direct Video or GIF link (.mp4, .gif, .webm, .gifv)
        if any(url.lower().endswith(ext) for ext in ('.mp4', '.gif', '.webm', '.gifv')):
            items.append({
                'id': post_id,
                'title': title,
                'subreddit': subreddit,
                'type': 'gif' if '.gif' in url.lower() else 'video',
                'url': url,
                'hd': url,
                'thumbnail': thumb or url,
                'duration': 'Direct',
                'ups': ups,
                'author': author,
                'over_18': over_18,
                'created_utc': created_utc,
                'source': 'reddit',
                'is_reddit': True
            })
            continue

        # 5. Direct Images (.jpg, .png, .webp, i.redd.it, i.imgur.com)
        if url.endswith(('.jpg', '.jpeg', '.png', '.webp')) or 'i.redd.it' in url or 'i.imgur.com' in url:
            items.append({
                'id': post_id,
                'title': title,
                'subreddit': subreddit,
                'type': 'image',
                'url': url,
                'hd': url,
                'thumbnail': thumb or url,
                'duration': 'Image',
                'ups': ups,
                'author': author,
                'over_18': over_18,
                'created_utc': created_utc,
                'source': 'reddit',
                'is_reddit': True
            })
            continue

        # 6. Fallback Video Preview (GIFs converted to mp4)
        if data.get('preview', {}).get('reddit_video_preview'):
            rvp = data['preview']['reddit_video_preview']
            fallback_url = rvp.get('fallback_url', '')
            items.append({
                'id': post_id,
                'title': title,
                'subreddit': subreddit,
                'type': 'video',
                'url': permalink,
                'hd': permalink,
                'stream_url': fallback_url,
                'fallback_url': fallback_url,
                'thumbnail': thumb or fallback_url,
                'duration': 'Preview',
                'ups': ups,
                'author': author,
                'over_18': over_18,
                'created_utc': created_utc,
                'source': 'reddit',
                'is_reddit': True,
                'use_ytdlp': True
            })
            continue

        # 7. Fallback item with valid thumbnail
        if thumb and isinstance(thumb, str) and thumb.startswith('http'):
            items.append({
                'id': post_id,
                'title': title,
                'subreddit': subreddit,
                'type': 'image',
                'url': permalink,
                'hd': url or thumb,
                'thumbnail': thumb,
                'duration': 'Post',
                'ups': ups,
                'author': author,
                'over_18': over_18,
                'created_utc': created_utc,
                'source': 'reddit',
                'is_reddit': True
            })
            continue

    return items

def crawl_reddit(target: str, limit: int = 25, sort: str = "hot", custom_cookies: str = None) -> Dict[str, Any]:
    """Fetch video, gallery, and media submissions from Reddit (subreddits, posts, or user saved/upvoted/submitted).
    Adopts the robust architecture from D:\\Programing\\Downloader-app."""
    t = (target or "").strip()
    if not t:
        return {"items": [], "error": "Subreddit name, username, or post URL is required."}

    # Extract subreddit, user profile, or post link
    sub = None
    user = None
    user_category = "submitted"
    is_comments = False

    if "reddit.com/r/" in t or t.startswith("r/"):
        sub_part = t.split("r/")[1]
        if "/comments/" in sub_part:
            is_comments = True
        else:
            sub = sub_part.split("/")[0].strip()
    elif "reddit.com/user/" in t or "reddit.com/u/" in t or t.startswith("u/"):
        user_part = re.sub(r'https?://(?:www\.)?reddit\.com/(?:user|u)/', '', t)
        parts = [p.strip() for p in user_part.lstrip("u/").split("/") if p.strip()]
        if parts:
            user = parts[0]
            if len(parts) > 1 and parts[1].lower() in ["saved", "upvoted", "hidden", "submitted", "comments", "overview", "history"]:
                user_category = parts[1].lower()
    elif not t.startswith("http") and "/" not in t:
        sub = t.strip()

    # Determine endpoint sequence
    urls_to_try = []
    if is_comments or "/comments/" in t:
        clean = t.rstrip("/")
        if not clean.endswith(".json"):
            clean += "/.json"
        sep = "&" if "?" in clean else "?"
        urls_to_try.append(f"{clean}{sep}raw_json=1")
        if "www.reddit.com" in clean:
            urls_to_try.append(clean.replace("www.reddit.com", "old.reddit.com") + f"{sep}raw_json=1")
    elif user:
        target_cat = 'upvoted' if user_category == 'history' else user_category
        urls_to_try.append(f"https://www.reddit.com/user/{user}/{target_cat}.json")
        urls_to_try.append(f"https://old.reddit.com/user/{user}/{target_cat}.json")
        urls_to_try.append(f"https://oauth.reddit.com/user/{user}/{target_cat}")
    elif sub:
        urls_to_try.append(f"https://www.reddit.com/r/{sub}/{sort}.json")
        urls_to_try.append(f"https://old.reddit.com/r/{sub}/{sort}.json")
    else:
        clean = t.rstrip("/")
        if not clean.endswith(".json"):
            clean += "/.json"
        urls_to_try.append(clean)

    headers = {
        'User-Agent': MEDIA_HEADERS['User-Agent'],
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Referer': 'https://www.reddit.com/'
    }

    # Resolve cookies
    cookie_str = ""
    if custom_cookies:
        cookie_str = cookies_mgr.parse_cookie_string(custom_cookies)
    if not cookie_str:
        cookie_str = cookies_mgr.get_session_cookies()

    cookie_dict = {}
    if cookie_str:
        headers['Cookie'] = cookie_str
        for pair in cookie_str.split(';'):
            if '=' in pair:
                k, v = pair.strip().split('=', 1)
                cookie_dict[k.strip()] = v.strip()

    auth_token = cookies_mgr.get_auth_token()

    all_items = []
    after = None
    limit = max(1, min(500, limit))

    for api_url in urls_to_try:
        try:
            req_headers = dict(headers)
            if auth_token and 'oauth.reddit.com' in api_url:
                req_headers['Authorization'] = f"Bearer {auth_token}"
            elif 'Authorization' in req_headers:
                req_headers.pop('Authorization', None)

            sep = "&" if "?" in api_url else "?"
            base_url = f"{api_url}{sep}raw_json=1&include_over_18=on&sort={sort or 'new'}"

            while len(all_items) < limit:
                params = {'limit': min(100, limit - len(all_items))}
                if after:
                    params['after'] = after

                r = _http_get(base_url, headers=req_headers, cookies=cookie_dict, params=params, timeout=15)
                
                # Check for rate-limiting, CAPTCHA, or blocked
                text_trim = r.text.strip() if r.text else ""
                is_captcha = "prove your humanity" in text_trim.lower() or "g-recaptcha" in text_trim.lower() or "blocked by network security" in text_trim.lower()
                if is_captcha:
                    return {
                        "items": [],
                        "error": (
                            "Reddit triggered a network security verification challenge ('Prove your humanity'). "
                            "Please open reddit.com in your browser to clear the challenge, then copy your full Cookie header "
                            "from DevTools (F12 → Network → click any reddit request → copy Cookie header) and paste it into Session Cookies."
                        ),
                        "needs_cookies": True
                    }

                if r.status_code in (401, 403):
                    if not cookie_str:
                        return {
                            "items": [],
                            "error": (
                                f"Reddit '{user_category.title() if user else 'Media'}' requires authentication (HTTP 403 / Login Wall). "
                                "Paste your active browser session cookie in the Session Cookies box above to authenticate safely."
                            ),
                            "needs_cookies": True
                        }
                    else:
                        return {
                            "items": [],
                            "error": (
                                f"Reddit returned HTTP 403 for '{user_category.title() if user else 'Media'}'. "
                                "Your session cookie may be expired or missing 'reddit_session'. "
                                "Make sure to copy the full Cookie header from an active reddit.com browser session."
                            ),
                            "needs_cookies": True
                        }

                if not r.ok:
                    break

                # Verify valid JSON
                try:
                    data = r.json()
                except Exception:
                    break

                children = []
                if isinstance(data, list):
                    for block in data:
                        if isinstance(block, dict) and 'data' in block:
                            children.extend(block.get('data', {}).get('children', []))
                elif isinstance(data, dict) and 'data' in data:
                    children = data.get('data', {}).get('children', [])

                if not children:
                    break

                parsed_batch = parse_reddit_children(children)
                all_items.extend(parsed_batch)

                after = data.get('data', {}).get('after') if isinstance(data, dict) else None
                if not after:
                    break

            if all_items:
                break
        except Exception as e:
            continue

    if not all_items:
        return {"items": [], "error": f"No video or GIF media found for '{t}'. Try another query or verify your session cookie."}

    # Sort recent first
    all_items.sort(key=lambda x: x.get('created_utc', 0), reverse=True)
    if limit and len(all_items) > limit:
        all_items = all_items[:limit]

    return {
        "items": all_items,
        "count": len(all_items),
        "target": sub or user or t
    }
