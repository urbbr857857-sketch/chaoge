import os
import re
import json
import secrets
import gzip
import string
import hashlib
import time
import sqlite3
import requests
import libsql_client
from datetime import datetime, timedelta
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, send_from_directory, request, session, redirect, make_response, render_template, Response, url_for
from flask_compress import Compress

# ============================================================
#  配置区
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'))
app.secret_key = 'chaoge-secret-key-2026-xk9pq3-please-change-me'

# ⭐️ 开启 gzip 压缩：HTML/JSON 响应自动压缩，可省 80% 流量
Compress(app)

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "https://jianquan-db-urbbr.aws-us-west-2.turso.io")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODk0Mjg0MDEsImlkIjoiMDFhMGEyM2MtMzQwMS03NWUyLWJmMDUtYjA4YzI0MWQ3YmFmIiwia2lkIjoic2VQbmU5QjdmOUhWYldGT1Z2VkN3X1NLMktXaWlnMnNOZGRSYmRoY3ZwdyIsInJpZCI6ImU4Njg1OGMyLWNhYWQtNGQyMy1hNjk5LWMzMmJjOGU1ZDM1NiJ9.yQ7QNl6y8gCzzrKlvUwQ8X-DpBcKosQcTaEFJadoKVG9LxklmBPNezj_E69UvDd66ea5qTbeuVtoR2qn3kb4Aw")

PUBLIC_DOMAIN = 'https://chaoge.onrender.com'
ADMIN_ACCESS_KEY = 'MyAdmin_2024_Xk9pQ3'
QQ_GROUP_URL = 'https://qun.qq.com/universal-share/share?ac=1&authKey=NoPRNDnAKthjxReAbRW%2FmXJRuifzVUiYJvyBZd7X0CkT1p%2F6XGxCtDRPokzlpP%2Fi&busi_data=eyJncm91cENvZGUiOiIxMzU5NDA4NTMiLCJ0b2tlbiI6IlJRVzNoOXdhZEYraHp2d3JnMVlOK2xDRVFyeStMWDNwSzRlbkJLVTZwZjg5d25zMGpFVzl1MWdsbTJqOUJmNXciLCJ1aW4iOiIxMzU3NjY0NzI0In0%3D&data=oN17F6DcQd5jsQPJo48b50CiB0zQbQBo3lEOmOeVHnY_UdKgxX7L2wErwiwHe3iAQet3fuuYxEEGLHoy3Qf4qg&svctype=4&tempid=h5_group_info'
LOCAL_DB_PATH = os.path.join(BASE_DIR, 'local_users.db')
BRAND_NAME = '超哥影视'
app.permanent_session_lifetime = timedelta(days=30)

# ============================================================
#  多接口源
# ============================================================
API_SOURCES = {
    "okzyw": {"name": "超哥专线", "base": "https://api.okzyw.net/api.php/provide/vod/"}
}
DEFAULT_SOURCE = "okzyw"

def load_api_sources():
    global API_SOURCES
    url = "https://mylazily.github.io/ziyuanzhan/data/latest.json"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        resources = data.get("resources", [])
        new_sources = {
            "okzyw": {"name": "超哥专线", "base": "https://api.okzyw.net/api.php/provide/vod/"}
        }
        for idx, item in enumerate(resources):
            name = item.get("name", f"线路{idx+1}")
            api = item.get("api", "").strip()
            if api and item.get("status") == "ok":
                new_sources[f"src_{idx}"] = {"name": name, "base": api}
        API_SOURCES = new_sources
        print(f"✅ 成功加载 {len(new_sources)} 个接口源")
    except Exception as e:
        print(f"⚠️ 加载远程接口列表失败，使用默认: {e}")

load_api_sources()

# ============================================================
#  Turso & 工具函数
# ============================================================
def get_db():
    return libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)

def hash_password(password: str) -> str:
    return hashlib.sha256((password + 'ledger-salt-v1').encode('utf-8')).hexdigest()

def get_local_db():
    conn = sqlite3.connect(LOCAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_local_db():
    conn = get_local_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS web_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS web_history (
        user_id INTEGER NOT NULL,
        vod_id TEXT NOT NULL,
        vod_name TEXT,
        vod_pic TEXT,
        type_name TEXT,
        remarks TEXT,
        updated_at TEXT,
        PRIMARY KEY (user_id, vod_id)
    )''')
    conn.commit()
    conn.close()

def init_cloud_tables():
    try:
        with get_db() as client:
            client.execute('''CREATE TABLE IF NOT EXISTS web_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT)''')
            client.execute('''CREATE TABLE IF NOT EXISTS web_history (
                user_id INTEGER NOT NULL,
                vod_id TEXT NOT NULL,
                vod_name TEXT,
                vod_pic TEXT,
                type_name TEXT,
                remarks TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, vod_id))''')
        print("✅ 云端账号/播放记录表就绪")
    except Exception as e:
        print(f"⚠️ 云表初始化失败（账号/历史将回落本地存储）: {e}")

init_local_db()
init_cloud_tables()

def _find_user(username):
    try:
        with get_db() as client:
            res = client.execute("SELECT id, username, password_hash FROM web_users WHERE username=?", (username,))
            if res.rows:
                return {'id': res.rows[0][0], 'username': res.rows[0][1],
                        'password_hash': res.rows[0][2], 'src': 'cloud'}
    except Exception as e:
        print(f"云库查询用户失败，回落本地: {e}")
    conn = get_local_db()
    row = conn.execute('SELECT id, username, password_hash FROM web_users WHERE username=?', (username,)).fetchone()
    conn.close()
    if row:
        return {'id': row['id'], 'username': row['username'],
                'password_hash': row['password_hash'], 'src': 'local'}
    return None

def create_web_user(username, pw_hash):
    try:
        with get_db() as client:
            client.execute("INSERT INTO web_users (username, password_hash, created_at) VALUES (?, ?, ?)",
                           (username, pw_hash, datetime.now().isoformat()))
        return
    except Exception as e:
        print(f"云库写用户失败，回落本地: {e}")
    conn = get_local_db()
    conn.execute("INSERT INTO web_users (username, password_hash, created_at) VALUES (?, ?, ?)",
                 (username, pw_hash, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def current_web_user():
    username = session.get('web_username')
    if not username:
        return None
    row = _find_user(username)
    return {'id': row['id'], 'username': row['username']} if row else None

def save_web_history(user_id, item):
    now = datetime.now().isoformat()
    try:
        with get_db() as client:
            client.execute('''INSERT INTO web_history (user_id, vod_id, vod_name, vod_pic, type_name, remarks, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, vod_id) DO UPDATE SET
                    vod_name=excluded.vod_name, vod_pic=excluded.vod_pic,
                    remarks=excluded.remarks, updated_at=excluded.updated_at''',
                           (user_id, item['vod_id'], item['vod_name'], item['vod_pic'],
                            item['type_name'], item['remarks'], now))
        return
    except Exception as e:
        print(f"云库写历史失败，回落本地: {e}")
    conn = get_local_db()
    conn.execute('''INSERT OR REPLACE INTO web_history
        (user_id, vod_id, vod_name, vod_pic, type_name, remarks, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (user_id, item['vod_id'], item['vod_name'], item['vod_pic'],
                   item['type_name'], item['remarks'], now))
    conn.commit()
    conn.close()

def list_web_history(user_id, limit=100):
    try:
        with get_db() as client:
            res = client.execute(
                "SELECT vod_id, vod_name, vod_pic, updated_at FROM web_history WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit))
            return [{'id': r[0], 'name': r[1], 'pic': r[2], 'ts': r[3]} for r in res.rows]
    except Exception as e:
        print(f"云库读历史失败，回落本地: {e}")
    conn = get_local_db()
    rows = conn.execute(
        "SELECT vod_id, vod_name, vod_pic, updated_at FROM web_history WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit)).fetchall()
    conn.close()
    return [{'id': r['vod_id'], 'name': r['vod_name'], 'pic': r['vod_pic'], 'ts': r['updated_at']} for r in rows]

def clear_web_history(user_id):
    try:
        with get_db() as client:
            client.execute("DELETE FROM web_history WHERE user_id=?", (user_id,))
        return
    except Exception as e:
        print(f"云库清历史失败，回落本地: {e}")
    conn = get_local_db()
    conn.execute("DELETE FROM web_history WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

@app.context_processor
def inject_globals():
    return {
        'brand_name': BRAND_NAME,
        'qq_group_url': QQ_GROUP_URL,
        'web_user': current_web_user(),
    }

NAV_CATEGORIES = [
    {'id': '', 'name': '首页', 'path': '/'},
    {'id': '1', 'name': '电影', 'path': '/?type_id=1'},
    {'id': '2', 'name': '电视剧', 'path': '/?type_id=2'},
    {'id': '3', 'name': '综艺', 'path': '/?type_id=3'},
    {'id': '4', 'name': '动漫', 'path': '/?type_id=4'},
]
def clean_html(raw_html):
    if not raw_html:
        return ''
    text = str(raw_html)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&amp;quot;', '"')
    text = text.replace('&amp;nbsp;', ' ')
    text = text.replace('&amp;lt;', '<')
    text = text.replace('&amp;gt;', '>')
    text = text.replace('&amp;amp;', '&')
    text = text.replace('&quot;', '"')
    text = text.replace('&nbsp;', ' ')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&amp;', '&')
    text = text.replace('&#39;', "'")
    text = text.replace('&apos;', "'")
    text = text.replace('&ldquo;', '“').replace('&rdquo;', '”')
    text = text.replace('"', '')
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()

# ============================================================
#  动态分类解析
# ============================================================
def parse_categories(class_list):
    if not isinstance(class_list, list):
        return [], {}, {}
    primary = []
    secondary_map = {}
    sub_to_parent = {}
    for c in class_list:
        tid = c.get('type_id')
        pid = c.get('type_pid', 0)
        name = c.get('type_name', '未命名')
        if tid is None:
            continue
        if pid == 0:
            primary.append({"id": tid, "name": name})
        else:
            secondary_map.setdefault(str(pid), []).append({"id": tid, "name": name})
            sub_to_parent[str(tid)] = str(pid)
    return primary, secondary_map, sub_to_parent

# ============================================================
#  API 请求 & 封面补全（带进程级缓存，避免每个访客都打上游）
# ============================================================
API_CACHE = {}
API_CACHE_TTL = 1800
API_CACHE_LOCK = __import__('threading').Lock()

SUB_CATEGORY_LOCK = __import__('threading').Lock()
SUB_CATEGORY_CACHE = {}

def get_sub_type_ids(api_base, type_id):
    # 360zy 类源：父分类直接查询返回 0，需要展开子分类合并查询
    with SUB_CATEGORY_LOCK:
        if api_base in SUB_CATEGORY_CACHE:
            child_map = SUB_CATEGORY_CACHE[api_base]
        else:
            child_map = {}
            try:
                cats = api_get({'ac': 'class'}, api_base).get('class', []) or []
                for c in cats:
                    pid = str(c.get('type_pid', 0))
                    if pid != '0':
                        child_map.setdefault(pid, []).append(str(c['type_id']))
            except Exception:
                pass
            SUB_CATEGORY_CACHE[api_base] = child_map
    return child_map.get(str(type_id), [])

def api_get(params, api_base):
    key = (api_base, tuple(sorted(params.items())))
    now = time.time()
    with API_CACHE_LOCK:
        hit = API_CACHE.get(key)
        if hit and now - hit[0] < API_CACHE_TTL:
            return hit[1]
    try:
        resp = requests.get(api_base, params=params, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"API请求失败: {e}")
        if hit:
            return hit[1]
        return {"code": 0, "list": [], "pagecount": 1, "class": []}
    with API_CACHE_LOCK:
        API_CACHE[key] = (now, data)
    return data

def fill_covers(videos, api_base):
    ids_to_fetch = [str(v['vod_id']) for v in videos if not v.get('vod_pic')]
    if not ids_to_fetch:
        return videos
    ids_str = ','.join(ids_to_fetch)
    params = {'ac': 'detail', 'ids': ids_str}
    data = api_get(params, api_base)
    details = data.get('list', [])
    detail_map = {d['vod_id']: d['vod_pic'] for d in details if d.get('vod_pic')}
    for v in videos:
        if not v.get('vod_pic') and v['vod_id'] in detail_map:
            v['vod_pic'] = detail_map[v['vod_id']]
    return videos

# ============================================================
#  全局源管理（存 Turso）
# ============================================================
def get_current_source():
    try:
        with get_db() as client:
            res = client.execute("SELECT value FROM app_settings WHERE key='current_source'")
            row = res.rows[0] if res.rows else None
        if row and row[0] in API_SOURCES:
            return row[0]
    except Exception as e:
        print(f"读取全局源失败: {e}")
    return DEFAULT_SOURCE

def set_current_source(source_key):
    if source_key not in API_SOURCES:
        return False
    with get_db() as client:
        client.execute("UPDATE app_settings SET value=? WHERE key='current_source'", (source_key,))
    return True

def fetch_list(api_base, type_id='', page=1, wd=''):
    params = {'ac': 'list', 'pg': page}
    if type_id:
        sub_ids = get_sub_type_ids(api_base, type_id)
        params['t'] = ','.join(sub_ids) if sub_ids else type_id
    if wd:
        params['wd'] = wd
    data = api_get(params, api_base)
    videos = fill_covers(data.get('list', []) or [], api_base)
    return {
        'videos': videos,
        'pagecount': data.get('pagecount', 1) or 1,
        'class': data.get('class', []) or [],
    }

def match_nav_type(primary_categories):
    mapping = {}
    keywords = {
        '1': ['电影', '影片'],
        '2': ['电视', '连续剧', '剧集'],
        '3': ['综艺'],
        '4': ['动漫', '动画'],
        '5': ['短剧'],
    }
    for cat in primary_categories:
        name = cat.get('name', '')
        for key, words in keywords.items():
            if any(w in name for w in words) and key not in mapping:
                mapping[key] = str(cat['id'])
    return mapping

# ============================================================
#  启动时预热首页缓存，避免第一个访客等上游接口
# ============================================================
def _warm_cache():
    try:
        base = API_SOURCES.get(DEFAULT_SOURCE, {}).get('base')
        if base:
            first = fetch_list(base, '', 1, '')
            cats, _, _ = parse_categories(first['class'])
            nav_map = match_nav_type(cats)
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(fetch_list, base, tid, 1, '') for tid in nav_map.values() if tid]
                for f in futs:
                    try: f.result()
                    except Exception: pass
        print("🔥 首页缓存预热完成")
    except Exception as e:
        print(f"预热失败(不影响启动): {e}")

import threading as _threading
_threading.Thread(target=_warm_cache, daemon=True).start()

# ============================================================
#  网页版：首页
# ============================================================
@app.route('/')
def web_index():
    source_key = get_current_source()
    api_base = API_SOURCES[source_key]['base']
    is_admin_view = (request.args.get('key', '') == ADMIN_ACCESS_KEY)

    page = request.args.get('page', 1, type=int)
    type_id = request.args.get('type_id', '', type=str)
    wd = request.args.get('wd', '', type=str)

    first = fetch_list(api_base, type_id=type_id, page=page, wd=wd)
    videos = first['videos']
    pagecount = first['pagecount']
    primary_categories, secondary_map, sub_to_parent = parse_categories(first['class'])

    if not primary_categories:
        fallback = api_get({'ac': 'detail', 'pg': 1}, api_base)
        primary_categories, secondary_map, sub_to_parent = parse_categories(fallback.get('class', []))

    current_primary_id = ''
    if type_id:
        for cat in primary_categories:
            if str(cat['id']) == str(type_id):
                current_primary_id = str(type_id)
                break
        if not current_primary_id:
            current_primary_id = sub_to_parent.get(str(type_id), '')

    current_secondary_list = secondary_map.get(current_primary_id, []) if current_primary_id else []
    page_range = list(range(max(1, page - 2), min(pagecount, page + 2) + 1))

    nav_map = match_nav_type(primary_categories)
    nav_items = [
        {'id': '', 'name': '首页', 'path': '/'},
        {'id': nav_map.get('1', '1'), 'name': '电影', 'path': '/?type_id=' + nav_map.get('1', '1')},
        {'id': nav_map.get('2', '2'), 'name': '电视剧', 'path': '/?type_id=' + nav_map.get('2', '2')},
        {'id': nav_map.get('3', '3'), 'name': '综艺', 'path': '/?type_id=' + nav_map.get('3', '3')},
        {'id': nav_map.get('4', '4'), 'name': '动漫', 'path': '/?type_id=' + nav_map.get('4', '4')},
    ]
    if '5' in nav_map:
        nav_items.append({'id': nav_map['5'], 'name': '短剧', 'path': '/?type_id=' + nav_map['5']})

    sections = []
    ranks = []
    is_home = (not type_id and not wd)
    if is_home:
        section_defs = [
            ('movie', '最新电影', nav_map.get('1')),
            ('tv', '最新电视剧', nav_map.get('2')),
            ('show', '最新综艺', nav_map.get('3')),
            ('anime', '最新动漫', nav_map.get('4')),
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = []
            for key, title, tid in section_defs:
                if not tid:
                    continue
                futures.append((key, title, tid, pool.submit(fetch_list, api_base, tid, 1, '')))
            for key, title, tid, fut in futures:
                try:
                    items = fut.result().get('videos', [])[:12]
                except Exception:
                    items = []
                if items:
                    sections.append({'key': key, 'title': title, 'type_id': tid, 'videos': items})
                    ranks.append({'title': title.replace('最新', '') + '热榜', 'videos': items[:10]})
        if not sections and videos:
            sections.append({'key': 'latest', 'title': '最新更新', 'type_id': '', 'videos': videos[:12]})
            ranks.append({'title': '热播榜', 'videos': videos[:10]})

    banners = videos[:8] if videos else []

    return render_template('index.html',
                           videos=videos,
                           banners=banners,
                           sections=sections,
                           ranks=ranks,
                           nav_items=nav_items,
                           is_home=is_home,
                           primary_categories=primary_categories,
                           secondary_categories=current_secondary_list,
                           secondary_map=secondary_map,
                           current_primary_id=current_primary_id,
                           current_page=page,
                           pagecount=pagecount,
                           page_range=page_range,
                           current_type=type_id,
                           current_wd=wd,
                           api_sources=API_SOURCES,
                           current_source=source_key,
                           is_admin_view=is_admin_view,
                           admin_key=ADMIN_ACCESS_KEY)

# ============================================================
#  网页版：播放页
# ============================================================
@app.route('/play/<int:vod_id>')
def web_play(vod_id):
    source_key = get_current_source()
    api_base = API_SOURCES[source_key]['base']
    is_admin_view = (request.args.get('key', '') == ADMIN_ACCESS_KEY)
    
    params = {'ac': 'detail', 'ids': vod_id}
    data = api_get(params, api_base)
    videos = data.get('list', [])
    if not videos:
        return "视频不存在", 404
    video = videos[0]
    
    video['vod_blurb'] = clean_html(video.get('vod_blurb', ''))
    video['vod_content'] = clean_html(video.get('vod_content', ''))
    
    play_from = video.get('vod_play_from', '')
    play_url_raw = video.get('vod_play_url', '')
    
    episodes = []
    if play_url_raw:
        # 第一步：按 $$$ 拆分播放源组
        source_groups = play_url_raw.split('$$$')
        
        # 第二步：从所有源组中挑出最合适的一组
        best_group = None
        if len(source_groups) > 1:
            # 多源时，优先选整组里带 .m3u8/.mp4 的
            for group in source_groups:
                if '.m3u8' in group.lower() or '.mp4' in group.lower():
                    best_group = group
                    break
            if best_group is None:
                best_group = source_groups[0]
        else:
            best_group = source_groups[0]
        
        # 第三步：对选中的源组，按 # 拆分剧集
        for part in best_group.split('#'):
            part = part.strip()
            if not part:
                continue
            if '$' in part:
                title, url = part.split('$', 1)
                title = title.strip()
                url = url.strip()
                # 第四步：如果单集内还有 $$$（罕见），再挑一次
                if '$$$' in url:
                    sub_urls = url.split('$$$')
                    picked = sub_urls[0]
                    for su in sub_urls:
                        if '.m3u8' in su.lower() or '.mp4' in su.lower():
                            picked = su
                            break
                    url = picked
                episodes.append({'title': title, 'url': url})
            else:
                episodes.append({'title': part, 'url': part})
    
    default_url = episodes[0]['url'] if episodes else ''
    
    return render_template('play.html', video=video, episodes=episodes,
                           play_from=play_from, default_url=default_url,
                           current_source=source_key,
                           is_admin_view=is_admin_view,
                           admin_key=ADMIN_ACCESS_KEY,
                           nav_items=NAV_CATEGORIES)

# ============================================================
#  网页版：本地登录 / 注册（不写入云数据库）
# ============================================================
@app.route('/login', methods=['GET', 'POST'])
def web_login():
    error = ''
    next_url = request.args.get('next') or request.form.get('next') or '/'
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not username or not password:
            error = '请输入用户名和密码'
        else:
            row = _find_user(username)
            if not row or row['password_hash'] != hash_password(password):
                error = '用户名或密码错误'
            else:
                session.permanent = True
                session['web_user_id'] = row['id']
                session['web_username'] = row['username']
                return redirect(next_url)
    return render_template('login.html', mode='login', error=error, next_url=next_url)

@app.route('/register', methods=['GET', 'POST'])
def web_register():
    error = ''
    next_url = request.args.get('next') or request.form.get('next') or '/'
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        password2 = request.form.get('password2') or ''
        if len(username) < 3 or len(username) > 20:
            error = '用户名需 3-20 个字符'
        elif not re.match(r'^[\w\u4e00-\u9fa5]+$', username):
            error = '用户名只能包含中文、字母、数字和下划线'
        elif len(password) < 6:
            error = '密码至少 6 位'
        elif password != password2:
            error = '两次输入的密码不一致'
        else:
            if _find_user(username):
                error = '用户名已被占用'
            else:
                create_web_user(username, hash_password(password))
                row = _find_user(username)
                session.permanent = True
                session['web_user_id'] = row['id']
                session['web_username'] = row['username']
                return redirect(next_url)
    return render_template('login.html', mode='register', error=error, next_url=next_url)

@app.route('/logout')
def web_logout():
    session.pop('web_user_id', None)
    session.pop('web_username', None)
    return redirect('/')

@app.route('/history')
def web_history():
    return render_template('history.html')

@app.route('/api/history', methods=['GET', 'POST', 'DELETE'])
def api_history():
    user = current_web_user()
    if request.method == 'POST':
        if not user:
            return jsonify({"error": "未登录"}), 401
        d = request.get_json(silent=True) or {}
        if not d.get('vod_id'):
            return jsonify({"error": "缺少 vod_id"}), 400
        save_web_history(user['id'], {
            'vod_id': str(d['vod_id']),
            'vod_name': d.get('vod_name', ''),
            'vod_pic': d.get('vod_pic', ''),
            'type_name': d.get('type_name', ''),
            'remarks': d.get('remarks', ''),
        })
        return jsonify({"ok": True})
    if request.method == 'DELETE':
        if not user:
            return jsonify({"error": "未登录"}), 401
        clear_web_history(user['id'])
        return jsonify({"ok": True})
    if not user:
        return jsonify({"items": [], "guest": True})
    return jsonify({"items": list_web_history(user['id']), "guest": False})

# ============================================================
#  接口版：TK 鉴权 + 防分享
# ============================================================
TK_CHARS = string.ascii_letters + string.digits
TK_BODY_LEN = 10
MAX_IPS_PER_10MIN = 5
MAX_DEVICES_PER_60MIN = 6
DEVICE_COOKIE_NAME = 'tk_device'

def _tk_checksum(body: str) -> str:
    total = 0
    for i, ch in enumerate(body):
        total += (TK_CHARS.index(ch) + 1) * (i + 7)
    return TK_CHARS[total % len(TK_CHARS)]

def generate_tk() -> str:
    while True:
        body = ''.join(secrets.choice(TK_CHARS) for _ in range(TK_BODY_LEN))
        tk = body + _tk_checksum(body)
        with get_db() as client:
            res = client.execute("SELECT 1 FROM users WHERE token=?", (tk,))
            exists = res.rows[0] if res.rows else None
        if not exists:
            return tk

def is_valid_tk_format(tk: str) -> bool:
    if not tk or len(tk) != TK_BODY_LEN + 1:
        return False
    body, checksum = tk[:-1], tk[-1]
    if any(ch not in TK_CHARS for ch in tk):
        return False
    return _tk_checksum(body) == checksum

def init_db():
    with get_db() as client:
        client.execute('''CREATE TABLE IF NOT EXISTS users
                     (token TEXT PRIMARY KEY, name TEXT, status TEXT DEFAULT 'active',
                      created_at TEXT, last_ip TEXT, last_seen TEXT)''')
        client.execute('''CREATE TABLE IF NOT EXISTS request_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, ip TEXT,
                      ua TEXT, timestamp REAL)''')
        client.execute('''CREATE TABLE IF NOT EXISTS bans
                     (token TEXT PRIMARY KEY, reason TEXT, banned_at TEXT)''')
        client.execute('''CREATE TABLE IF NOT EXISTS admins
                     (id INTEGER PRIMARY KEY CHECK (id = 1),
                      username TEXT NOT NULL,
                      password_hash TEXT NOT NULL,
                      updated_at TEXT)''')
        client.execute('''CREATE TABLE IF NOT EXISTS device_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT, device_id TEXT,
                      ip TEXT, ua TEXT, timestamp REAL)''')
        client.execute('''CREATE TABLE IF NOT EXISTS app_settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        res = client.execute("SELECT COUNT(*) FROM admins")
        if res.rows[0][0] == 0:
            client.execute("INSERT INTO admins (id, username, password_hash, updated_at) VALUES (1, ?, ?, ?)",
                      ('admin', hash_password('admin123456'), datetime.now().isoformat()))
        res = client.execute("SELECT value FROM app_settings WHERE key='current_source'")
        if not res.rows:
            client.execute("INSERT INTO app_settings (key, value) VALUES ('current_source', ?)", (DEFAULT_SOURCE,))

init_db()

def get_device_fingerprint():
    device_id = request.cookies.get(DEVICE_COOKIE_NAME)
    if device_id and len(device_id) >= 16:
        return device_id
    ip = request.headers.get('CF-Connecting-IP', request.remote_addr) or '未知'
    ua = request.headers.get('User-Agent', '')
    return hashlib.md5(f"{ip}|{ua}".encode('utf-8')).hexdigest()

def attach_device_cookie(resp, device_id):
    resp.set_cookie(DEVICE_COOKIE_NAME, device_id, max_age=365*24*3600, httponly=True, samesite='Lax')
    return resp

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.headers.get('CF-Connecting-IP', request.remote_addr) or '未知'
        token = request.args.get('token') or request.headers.get('X-Token')
        if not token:
            return jsonify({"error": "缺少 token 参数"}), 401
        if not is_valid_tk_format(token) and len(token) < 20:
            return jsonify({"error": "无效的 TK"}), 401
        device_id = get_device_fingerprint()
        with get_db() as client:
            res = client.execute("SELECT reason FROM bans WHERE token=?", (token,))
            ban = res.rows[0] if res.rows else None
            if ban:
                return jsonify({"error": f"此 TK 已被封禁，原因：{ban[0]}"}), 403
            res = client.execute("SELECT status FROM users WHERE token=?", (token,))
            user = res.rows[0] if res.rows else None
            if not user:
                return jsonify({"error": "无效的 TK"}), 401
            if user[0] != 'active':
                return jsonify({"error": "TK 已被禁用"}), 403
            now = time.time()
            ten_min_ago = now - 600
            client.execute("DELETE FROM request_logs WHERE timestamp < ?", (ten_min_ago,))
            client.execute("INSERT INTO request_logs (token, ip, ua, timestamp) VALUES (?, ?, ?, ?)",
                      (token, ip, request.headers.get('User-Agent', '')[:200], now))
            res = client.execute("SELECT COUNT(DISTINCT ip) FROM request_logs WHERE token=? AND timestamp>=?",
                      (token, ten_min_ago))
            ip_count = res.rows[0][0]
            if ip_count > MAX_IPS_PER_10MIN:
                client.execute("INSERT OR REPLACE INTO bans (token, reason, banned_at) VALUES (?, ?, ?)",
                          (token, f"检测到分享行为（10分钟内来自 {ip_count} 个不同IP）", datetime.now().isoformat()))
                client.execute("UPDATE users SET status='banned' WHERE token=?", (token,))
                return jsonify({"error": "检测到分享行为，TK 已被自动封禁"}), 403
            sixty_min_ago = now - 3600
            client.execute("DELETE FROM device_logs WHERE timestamp < ?", (sixty_min_ago,))
            client.execute("INSERT INTO device_logs (token, device_id, ip, ua, timestamp) VALUES (?, ?, ?, ?, ?)",
                      (token, device_id, ip, request.headers.get('User-Agent', '')[:200], now))
            res = client.execute("SELECT COUNT(DISTINCT device_id) FROM device_logs WHERE token=? AND timestamp>=?",
                      (token, sixty_min_ago))
            device_count = res.rows[0][0]
            if device_count > MAX_DEVICES_PER_60MIN:
                client.execute("INSERT OR REPLACE INTO bans (token, reason, banned_at) VALUES (?, ?, ?)",
                          (token, f"检测到多设备分享（60分钟内来自 {device_count} 个不同设备）", datetime.now().isoformat()))
                client.execute("UPDATE users SET status='banned' WHERE token=?", (token,))
                return jsonify({"error": "检测到多设备分享，TK 已被自动封禁"}), 403
            client.execute("UPDATE users SET last_ip=?, last_seen=? WHERE token=?",
                      (ip, datetime.now().isoformat(), token))
        result = f(*args, **kwargs)
        if isinstance(result, tuple):
            resp = make_response(*result)
        else:
            resp = make_response(result)
        return attach_device_cookie(resp, device_id)
    return decorated

@app.route('/time_sort')
@app.route('/time_sort.json')
@token_required
def api_get_time_sort():
    file_path = os.path.join(BASE_DIR, 'time_sort.json')
    if not os.path.exists(file_path):
        return jsonify({"error": "文件不存在"}), 404
    return send_from_directory(BASE_DIR, 'time_sort.json')

# ============================================================
#  管理员：登录检查 & 装饰器
# ============================================================
def is_admin_logged_in() -> bool:
    return bool(session.get('admin_logged_in'))

def admin_required_page(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin_logged_in():
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return wrapper

def admin_required_api(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin_logged_in():
            return jsonify({"error": "未登录，请先登录"}), 401
        return f(*args, **kwargs)
    return wrapper

def check_admin_access():
    ip = request.headers.get('CF-Connecting-IP', request.remote_addr)
    if ip in ('127.0.0.1', '::1', 'localhost', None):
        return True
    if session.get('admin_remote_ok'):
        return True
    key = request.args.get('key', '')
    if key and key == ADMIN_ACCESS_KEY:
        session.permanent = True
        session['admin_remote_ok'] = True
        return True
    return False

# ============================================================
#  管理员：登录 / 登出 / 修改密码
# ============================================================
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if not check_admin_access():
        return f"禁止访问。请使用带密钥的链接打开，例如：<br>{PUBLIC_DOMAIN}/admin/login?key=你的密钥", 403
    error = ''
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as client:
            res = client.execute("SELECT username, password_hash FROM admins WHERE id=1")
            row = res.rows[0] if res.rows else None
        if row and username == row[0] and hash_password(password) == row[1]:
            session.permanent = True
            session['admin_logged_in'] = True
            return redirect('/admin')
        else:
            error = '用户名或密码错误'
    return f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>管理员登录</title>
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}}
body{{background:linear-gradient(135deg,#4361ee,#3a0ca3);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
.card{{background:white;border-radius:20px;padding:28px 22px;max-width:390px;width:100%;box-shadow:0 20px 50px rgba(0,0,0,0.25)}}
h1{{text-align:center;font-size:20px;color:#3a0ca3;margin-bottom:6px}}
p.tip{{text-align:center;color:#64748b;font-size:13px;margin-bottom:20px}}
label{{display:block;font-size:13px;color:#475569;margin-bottom:5px;font-weight:500}}
input{{width:100%;padding:11px 12px;border:1px solid #dbe0e8;border-radius:10px;font-size:15px;background:#f8fafc;margin-bottom:14px}}
input:focus{{outline:none;border-color:#4361ee;background:white}}
button{{width:100%;padding:12px;border:none;border-radius:12px;background:#4361ee;color:white;font-size:16px;font-weight:600;cursor:pointer}}
.error{{color:#dc2626;font-size:13px;text-align:center;margin-bottom:10px;min-height:18px}}</style></head><body>
<form class="card" method="POST"><h1>🎬 管理员登录</h1><p class="tip">影视接口 TK 管理后台</p>
<div class="error">{error}</div><label>用户名</label><input type="text" name="username" required>
<label>密码</label><input type="password" name="password" required><button type="submit">登录</button></form></body></html>'''

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect('/admin/login')

@app.route('/admin/change_password', methods=['GET', 'POST'])
@admin_required_page
def admin_change_password():
    if not check_admin_access():
        return "禁止访问", 403
    error = ''
    success = ''
    if request.method == 'POST':
        old_pwd = request.form.get('old_password', '')
        new_pwd = request.form.get('new_password', '')
        confirm_pwd = request.form.get('confirm_password', '')
        with get_db() as client:
            res = client.execute("SELECT password_hash FROM admins WHERE id=1")
            row = res.rows[0] if res.rows else None
            if not row or hash_password(old_pwd) != row[0]:
                error = '原密码错误'
            elif len(new_pwd) < 6:
                error = '新密码至少 6 位'
            elif new_pwd != confirm_pwd:
                error = '两次输入的新密码不一致'
            else:
                client.execute("UPDATE admins SET password_hash=?, updated_at=? WHERE id=1",
                          (hash_password(new_pwd), datetime.now().isoformat()))
                success = '密码修改成功'
    return f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>修改密码</title>
<style>*{{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}}
body{{background:#f0f2f5;padding:20px}}
.card{{background:white;border-radius:16px;padding:22px;max-width:420px;margin:0 auto;box-shadow:0 4px 16px rgba(0,0,0,0.08)}}
h1{{font-size:18px;color:#3a0ca3;margin-bottom:16px}}
label{{display:block;font-size:13px;color:#475569;margin-bottom:5px;font-weight:500}}
input{{width:100%;padding:11px 12px;border:1px solid #dbe0e8;border-radius:10px;font-size:15px;background:#f8fafc;margin-bottom:14px}}
input:focus{{outline:none;border-color:#4361ee;background:white}}
button{{width:100%;padding:12px;border:none;border-radius:12px;background:#4361ee;color:white;font-size:15px;font-weight:600;cursor:pointer}}
.error{{color:#dc2626;font-size:13px;margin-bottom:10px}}.success{{color:#10b981;font-size:13px;margin-bottom:10px}}
.back{{display:block;text-align:center;margin-top:14px;color:#4361ee;text-decoration:none;font-size:14px}}</style></head><body>
<form class="card" method="POST"><h1>🔐 修改管理员密码</h1><div class="error">{error}</div><div class="success">{success}</div>
<label>原密码</label><input type="password" name="old_password" required>
<label>新密码</label><input type="password" name="new_password" minlength="6" required>
<label>确认新密码</label><input type="password" name="confirm_password" minlength="6" required>
<button type="submit">保存修改</button><a class="back" href="/admin">← 返回管理面板</a></form></body></html>'''

# ============================================================
#  管理员：数据汇总
# ============================================================
def get_all_users_data():
    with get_db() as client:
        res = client.execute("""SELECT u.token, u.name, u.status, u.last_ip, u.last_seen, u.created_at,
                   b.reason AS ban_reason FROM users u LEFT JOIN bans b ON u.token = b.token ORDER BY u.last_seen DESC""")
        rows = res.rows
        now = time.time()
        ten_min_ago = now - 600
        sixty_min_ago = now - 3600
        result = []
        for r in rows:
            token, name, status, last_ip, last_seen, created_at, ban_reason = r
            res_ip = client.execute("SELECT COUNT(DISTINCT ip) FROM request_logs WHERE token=? AND timestamp>=?", (token, ten_min_ago))
            recent_ip_count = res_ip.rows[0][0] or 0
            res_req = client.execute("SELECT COUNT(*) FROM request_logs WHERE token=? AND timestamp>=?", (token, ten_min_ago))
            recent_req_count = res_req.rows[0][0] or 0
            res_dev = client.execute("SELECT COUNT(DISTINCT device_id) FROM device_logs WHERE token=? AND timestamp>=?", (token, sixty_min_ago))
            recent_device_count = res_dev.rows[0][0] or 0
            if status != 'active': risk = 'banned'
            elif recent_ip_count >= 3 or recent_device_count >= 4: risk = 'high'
            elif recent_ip_count >= 2 or recent_device_count >= 3: risk = 'medium'
            else: risk = 'low'
            is_active = False
            if last_seen:
                try:
                    last_dt = datetime.fromisoformat(last_seen)
                    if (datetime.now() - last_dt).total_seconds() < 3600: is_active = True
                except Exception: pass
            result.append({"token": token, "name": name, "status": status,
                "last_ip": last_ip or '无', "last_seen": last_seen or '无', "created_at": created_at or '无',
                "ban_reason": ban_reason or '', "recent_ip_count": recent_ip_count,
                "recent_req_count": recent_req_count, "recent_device_count": recent_device_count,
                "risk": risk, "is_active": is_active,
                "full_url": f"{PUBLIC_DOMAIN}/time_sort.json?token={token}",
                "access_url": f"{PUBLIC_DOMAIN}/time_sort.json?token={token}"})
    return result

# ============================================================
#  管理员：主面板
# ============================================================
@app.route('/admin')
@admin_required_page
def admin_page():
    if not check_admin_access():
        return f"禁止访问，请使用带密钥的链接", 403
    html = '''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>影视接口 TK 管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,sans-serif}
body{background:#f0f2f5;padding:16px;color:#1e293b}
h1{font-size:20px;margin-bottom:16px;color:#3a0ca3}
.card{background:white;border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 2px 10px rgba(0,0,0,0.06)}
.card h2{font-size:15px;margin-bottom:12px;color:#4361ee}
input{padding:10px 12px;border:1px solid #dbe0e8;border-radius:10px;font-size:15px;width:100%;background:#f8fafc}
input:focus{outline:none;border-color:#4361ee;background:white}
button{padding:9px 16px;border-radius:10px;border:none;font-size:14px;font-weight:600;cursor:pointer}
.btn-primary{background:#4361ee;color:white}.btn-danger{background:#f72585;color:white}
.btn-success{background:#10b981;color:white}.btn-warn{background:#f8961e;color:white}
.row{display:flex;gap:8px}.row input{flex:1}
.user-item{border:1px solid #eef2f7;border-radius:12px;padding:12px;margin-bottom:10px;font-size:13px}
.user-item .name{font-weight:700;font-size:15px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.user-item .line{margin-top:6px;color:#475569;word-break:break-all;line-height:1.6}
.user-item .token{font-family:monospace;font-size:12px;background:#f1f5f9;padding:2px 6px;border-radius:6px}
.badge{font-size:10px;padding:2px 8px;border-radius:20px;font-weight:500}
.badge-active{background:#dcfce7;color:#15803d}.badge-offline{background:#e2e8f0;color:#475569}
.badge-banned{background:#fee2e2;color:#b91c1c}.badge-high{background:#fee2e2;color:#b91c1c}
.badge-medium{background:#fef3c7;color:#b45309}.badge-low{background:#dcfce7;color:#15803d}
.actions{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
.empty{text-align:center;color:#94a3b8;font-size:13px;padding:20px 0}
.tab-row{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.tab-row button{flex:1;background:#e9edf5;color:#1e293b;font-size:13px;padding:8px;min-width:70px}
.tab-row button.active{background:#4361ee;color:white}
.result-box{background:#f0fdf4;border-radius:10px;padding:12px;font-size:13px;line-height:1.7}
.top-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px}
.top-bar .link{color:#4361ee;text-decoration:none;font-size:13px}
.search-wrap{margin-bottom:12px}
</style></head><body>
<div class="top-bar"><h1>🎬 影视接口 TK 管理中心</h1><div><a class="link" href="/admin/change_password">修改密码</a>&nbsp;|&nbsp;<a class="link" href="/admin/logout">退出登录</a></div></div>
<div class="card"><h2>➕ 生成新 TK</h2><div class="row"><input type="text" id="nameInput" placeholder="输入使用者姓名" maxlength="30"><button class="btn-primary" onclick="createUser()">生成</button></div><div id="createResult" style="margin-top:10px;"></div></div>
<div class="card"><div class="search-wrap"><input type="text" id="searchInput" placeholder="🔍 搜索用户名..." oninput="renderUsers()"></div>
<div class="tab-row"><button id="tabAll" class="active" onclick="switchTab('all')">全部用户</button><button id="tabActive" onclick="switchTab('active')">🟢 活跃</button><button id="tabRisk" onclick="switchTab('risk')">⚠️ 高风险</button><button id="tabBanned" onclick="switchTab('banned')">🔴 已封禁</button></div>
<button class="btn-success" style="width:100%;margin-bottom:12px;" onclick="loadUsers()">🔄 刷新数据</button><div id="userList"><div class="empty">加载中...</div></div></div>
<script>
let currentTab='all',cachedUsers=[];
function switchTab(t){currentTab=t;['All','Active','Risk','Banned'].forEach(x=>document.getElementById('tab'+x).classList.toggle('active',t===x.toLowerCase()));renderUsers();}
async function createUser(){const n=document.getElementById('nameInput').value.trim();if(!n){alert('请输入名字');return;}const r=await fetch('/admin/create?name='+encodeURIComponent(n));if(r.status===401){location.href='/admin/login';return;}const d=await r.json();if(d.error){alert(d.error);return;}document.getElementById('createResult').innerHTML='<div class="result-box"><b style="color:#10b981;">✅ 生成成功</b><br><b>姓名：</b>'+d.name+'<br><b>TK：</b><span class="token">'+d.token+'</span><br><b>访问链接：</b><br><span style="word-break:break-all;color:#4361ee;">'+d.access_url+'</span><br><button class="btn-primary" style="margin-top:8px;" onclick="copyText(\\''+d.access_url+'\\')">📋 复制链接</button></div>';document.getElementById('nameInput').value='';loadUsers();}
function copyText(t){navigator.clipboard.writeText(t).then(()=>alert('已复制'));}
async function loadUsers(){const r=await fetch('/admin/list');if(r.status===401){location.href='/admin/login';return;}cachedUsers=await r.json();if(cachedUsers.error){document.getElementById('userList').innerHTML='<div class="empty">'+cachedUsers.error+'</div>';return;}renderUsers();}
function renderUsers(){let l=cachedUsers.slice();const k=document.getElementById('searchInput').value.trim().toLowerCase();
if(currentTab==='active')l=l.filter(u=>u.is_active&&u.status==='active');
else if(currentTab==='risk')l=l.filter(u=>u.risk==='high');
else if(currentTab==='banned')l=l.filter(u=>u.status==='banned'||u.risk==='banned');
if(k)l=l.filter(u=>u.name.toLowerCase().includes(k));
const c=document.getElementById('userList');if(l.length===0){c.innerHTML='<div class="empty">暂无匹配数据</div>';return;}
c.innerHTML=l.map(u=>{let sb=u.status!=='active'?'<span class="badge badge-banned">已封禁</span>':(u.is_active?'<span class="badge badge-active">活跃</span>':'<span class="badge badge-offline">离线</span>');
let rb=u.risk==='high'?'<span class="badge badge-high">⚠️ 高风险</span>':(u.risk==='medium'?'<span class="badge badge-medium">中风险</span>':'<span class="badge badge-low">正常</span>');
let a=u.status==='active'?'<button class="btn-danger" onclick="banUser(\\''+u.token+'\\')">🔒 封禁</button><button class="btn-warn" onclick="resetLogs(\\''+u.token+'\\')">🧹 清日志</button>':'<button class="btn-success" onclick="unbanUser(\\''+u.token+'\\')">🔓 解封</button>';
return '<div class="user-item"><div class="name">'+u.name+' '+sb+' '+rb+'</div><div class="line">TK：<span class="token">'+u.token+'</span></div><div class="line">最后 IP：'+u.last_ip+' ｜ 最后活跃：'+(u.last_seen==='无'?'无':u.last_seen.replace('T',' ').substring(0,19))+'</div><div class="line">近10分钟：'+u.recent_ip_count+' 个IP ｜ '+u.recent_req_count+' 次请求 ｜ 近60分钟：'+u.recent_device_count+' 个设备</div>'+(u.ban_reason?'<div class="line" style="color:#b91c1c;">封禁原因：'+u.ban_reason+'</div>':'')+'<div class="actions">'+a+'</div></div>';}).join('');}
async function banUser(t){if(!confirm('确认封禁该 TK？'))return;const r=await fetch('/admin/ban?token='+t);if(r.status===401){location.href='/admin/login';return;}loadUsers();}
async function unbanUser(t){const r=await fetch('/admin/unban?token='+t);if(r.status===401){location.href='/admin/login';return;}loadUsers();}
async function resetLogs(t){if(!confirm('确认清空该 TK 的请求日志？'))return;const r=await fetch('/admin/reset_logs?token='+t);if(r.status===401){location.href='/admin/login';return;}loadUsers();}
loadUsers();setInterval(loadUsers,30000);
</script></body></html>'''
    return html

# ============================================================
#  管理员：API 接口
# ============================================================
@app.route('/admin/list')
@admin_required_api
def admin_list():
    if not check_admin_access(): return jsonify({"error": "禁止远程访问"}), 403
    return jsonify(get_all_users_data())

@app.route('/admin/create')
@admin_required_api
def create_token():
    if not check_admin_access(): return jsonify({"error": "禁止远程访问"}), 403
    name = request.args.get('name', '').strip()
    if not name: return jsonify({"error": "请提供 name 参数"}), 400
    if len(name) > 30: return jsonify({"error": "姓名过长"}), 400
    token = generate_tk()
    with get_db() as client:
        client.execute("INSERT INTO users (token, name, created_at) VALUES (?, ?, ?)",
                  (token, name, datetime.now().isoformat()))
    return jsonify({"message": "创建成功", "name": name, "token": token,
        "access_url": f"{PUBLIC_DOMAIN}/time_sort.json?token={token}"})

@app.route('/admin/ban')
@admin_required_api
def ban_user():
    if not check_admin_access(): return jsonify({"error": "禁止远程访问"}), 403
    token = request.args.get('token')
    if not token: return jsonify({"error": "请提供 token"}), 400
    with get_db() as client:
        client.execute("INSERT OR REPLACE INTO bans (token, reason, banned_at) VALUES (?, ?, ?)",
                  (token, "管理员手动封禁", datetime.now().isoformat()))
        client.execute("UPDATE users SET status='banned' WHERE token=?", (token,))
    return jsonify({"message": f"已封禁 {token}"})

@app.route('/admin/unban')
@admin_required_api
def unban():
    if not check_admin_access(): return jsonify({"error": "禁止远程访问"}), 403
    token = request.args.get('token')
    if not token: return jsonify({"error": "请提供 token"}), 400
    with get_db() as client:
        client.execute("DELETE FROM bans WHERE token=?", (token,))
        client.execute("UPDATE users SET status='active' WHERE token=?", (token,))
    return jsonify({"message": f"已解封 {token}"})

@app.route('/admin/reset_logs')
@admin_required_api
def reset_logs():
    if not check_admin_access(): return jsonify({"error": "禁止远程访问"}), 403
    token = request.args.get('token')
    if not token: return jsonify({"error": "请提供 token"}), 400
    with get_db() as client:
        client.execute("DELETE FROM request_logs WHERE token=?", (token,))
        client.execute("DELETE FROM device_logs WHERE token=?", (token,))
    return jsonify({"message": f"已清空 {token} 的日志"})

# ⭐️ 切换全局源
@app.route('/admin/switch_source')
def admin_switch_source():
    if request.args.get('key', '') != ADMIN_ACCESS_KEY:
        return jsonify({"error": "无权操作"}), 403
    source_key = request.args.get('source', '')
    if set_current_source(source_key):
        return jsonify({"ok": True, "source": source_key, "name": API_SOURCES[source_key]['name']})
    return jsonify({"error": "无效的源"}), 400

# ============================================================
#  通配路由（必须放最后！）
# ============================================================
@app.route('/<path:filepath>')
def serve_static(filepath):
    target_path = os.path.abspath(os.path.join(BASE_DIR, filepath))
    if not target_path.startswith(os.path.abspath(BASE_DIR)):
        return jsonify({"error": "禁止访问"}), 403
    if not os.path.exists(target_path):
        return jsonify({"error": f"文件不存在: {filepath}"}), 404
    return send_from_directory(BASE_DIR, filepath)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
