# -*- coding: utf-8 -*-
"""WorkBuddy Token 消耗看板构建脚本

扫描 ~/.workbuddy/projects/<工作空间>/*.jsonl，抽取请求级 usage 数据，
生成单文件自包含看板 token-dashboard.html（离线可看）。

跨平台：Windows / macOS / Linux 通用，仅依赖 Python 标准库（3.8+）。

用法：
    python gen_dashboard.py                    # 输出到当前目录 token-dashboard.html
    python gen_dashboard.py --out 看板.html     # 自定义输出路径
    python gen_dashboard.py --projects <目录>   # 自定义会话日志目录
"""
import argparse, json, glob, os, sys, datetime, re

# Windows 控制台 GBK 环境下防止中文 print 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ap = argparse.ArgumentParser(description="WorkBuddy Token 消耗看板生成器")
_ap.add_argument("--projects", default=os.path.join(os.path.expanduser("~"), ".workbuddy", "projects"),
                 help="WorkBuddy 会话日志目录（默认 ~/.workbuddy/projects）")
_ap.add_argument("--out", default="token-dashboard.html", help="输出 HTML 路径（默认当前目录 token-dashboard.html）")
_args = _ap.parse_args()

PROJECTS = _args.projects
OUT = _args.out
def parse_ts(v):
    if isinstance(v, (int, float)):
        if v > 1e12: return v / 1000.0
        if v > 1e9: return float(v)
    if isinstance(v, str):
        s = v.strip()
        for fmt_ in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
                     '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                import calendar
                return calendar.timegm(datetime.datetime.strptime(s, fmt_).timetuple())
            except Exception:
                pass
        try: return float(s) / 1000.0
        except Exception: return None
    return None

def grab(o):
    pd = o.get('providerData')
    if not isinstance(pd, dict): return None
    ru = pd.get('rawUsage') or {}; u = pd.get('usage') or {}
    tt = ru.get('total_tokens') or u.get('totalTokens')
    if not tt: return None
    model = pd.get('model') or pd.get('requestModelId') or pd.get('requestModelName') or 'unknown'
    ptd = ru.get('prompt_tokens_details') or {}
    ch = ru.get('prompt_cache_hit_tokens')
    if ch is None: ch = ptd.get('cached_tokens') or 0  # 兜底：DeepSeek 风格顶层字段 → OpenAI 风格嵌套字段（claude/qwen/glm）
    cw = ptd.get('cached_creation_tokens') or ptd.get('cache_write_tokens') or 0  # 缓存写入（Claude 按 1.25× 输入价计）
    return dict(tt=int(tt), it=int(ru.get('prompt_tokens') or u.get('inputTokens') or 0),
                ot=int(ru.get('completion_tokens') or u.get('outputTokens') or 0),
                ch=int(ch or 0), cw=int(cw or 0), model=model,
                credit=(float(ru['credit']) if ru.get('credit') is not None else None))

def session_title(o, fallback):
    """从用户消息提取标题候选（前 42 字）。三级策略：
    ① 去掉 <system-reminder>...</system-reminder> 包装块后取内文；
    ② 标签包装消息（如 teammate-message）取 summary 属性；
    ③ 剥掉首层标签取内文。
    旧逻辑遇 '<' 开头直接放弃，导致大量有标题的会话显示为"无标题会话" """
    if o.get('role') != 'user': return None
    import re as _re
    c = o.get('content')
    texts = []
    if isinstance(c, str): texts = [c]
    elif isinstance(c, list):
        for seg in c:
            if isinstance(seg, dict) and seg.get('type') in ('input_text', 'text'):
                texts.append(seg.get('text') or '')
    for t in texts:
        if not t.strip(): continue
        t = _re.sub(r'<system-reminder[\s\S]*?</system-reminder>', '', t).strip()
        if not t: continue
        if t.startswith('<'):
            m = _re.match(r'<(\w+)([^>]*)>', t)
            if not m: continue
            sm = _re.search(r'summary="([^"]+)"', m.group(2))
            if sm: return sm.group(1).strip()[:42]
            inner = _re.sub(r'^<[^>]*>', '', t).strip()
            if inner and not inner.startswith('<'):
                t = ' '.join(inner.split())
                if t: return t[:42]
            continue
        t = ' '.join(t.split())
        if t: return t[:42]
    return None

MODEL_MERGE={'glm-5.2-x':'glm-5.2'}

def short_ws(name):
    """工作空间目录名是路径 slug（如 c-Users-foo-WorkBuddy-2026-08-01-10-00-00），
    取 WorkBuddy 之后的部分作为短名；非 WorkBuddy 目录（如桌面工作区）原样返回"""
    i = name.rfind('-WorkBuddy-')
    if i >= 0 and name[i + 11:]:
        return name[i + 11:]
    i = name.rfind('-workbuddy-')
    if i >= 0 and name[i + 11:]:
        return name[i + 11:]
    return name

def ws_display(folder, cwd):
    """工作空间显示名：优先用日志 cwd 的真实路径基名；
    ~/WorkBuddy/<时间戳> 实例空间显示为「临时空间 MM-DD HH:MM」，automation-* 为「自动化 …」"""
    base = os.path.basename((cwd or '').rstrip('/')) if cwd else ''
    if base:
        m = re.match(r'(automation-)?(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})$', base)
        if m:
            return ('自动化 ' if m.group(1) else '临时空间 ') + '%s-%s %s:%s' % (m.group(3), m.group(4), m.group(5), m.group(6))
        return base
    return short_ws(folder)

now = datetime.datetime.now()
gen_ms = int(now.timestamp() * 1000)

ws_names = {}      # folder -> short
ws_idx = {}        # folder -> index
cwd_by_folder = {} # folder -> {cwd: count}，用于还原工作空间真实名称
models = [] ; model_idx = {}
sessions = {}      # sid -> dict
files = glob.glob(os.path.join(PROJECTS, '**', '*.jsonl'), recursive=True)
for fp in files:
    rel = os.path.relpath(fp, PROJECTS)
    parts = rel.split(os.sep)
    folder = parts[0]
    # 二级文件 = 会话文件；三级及更深（subagents/agent-*.jsonl）归属父会话
    sid = os.path.splitext(parts[1])[0] if len(parts) == 2 else parts[1]
    if folder not in ws_idx:
        ws_names[folder] = short_ws(folder)
        ws_idx[folder] = len(ws_idx)
    recs = []; evs = []; tks = {}
    title = None; title_ai = None
    with open(fp, encoding='utf-8', errors='ignore') as fh:
        for line in fh:
            try: o = json.loads(line)
            except Exception: continue
            if not isinstance(o, dict): continue
            sid_r = o.get('sessionId')
            if sid_r and len(parts) == 2: sid = sid_r  # 子代理文件保持归属父会话
            cwdo = o.get('cwd')
            if cwdo:
                cc = cwd_by_folder.setdefault(folder, {})
                cc[cwdo] = cc.get(cwdo, 0) + 1
            if o.get('type') == 'ai-title' and o.get('aiTitle'):  # 平台 AI 生成的会话标题，优先级最高
                title_ai = str(o['aiTitle']).strip() or title_ai
            if title is None:
                t = session_title(o, sid)
                if t: title = t
            ts = parse_ts(o.get('timestamp'))
            if ts:
                evs.append(ts)
                if o.get('type') == 'function_call':
                    nm = o.get('name')
                    if nm: tks[nm] = tks.get(nm, 0) + 1
            r = grab(o)
            if not r: continue
            r['model'] = MODEL_MERGE.get(r['model'], r['model'])
            if not ts: continue
            if r['model'] not in model_idx:
                model_idx[r['model']] = len(models); models.append(r['model'])
            recs.append((int(ts // 60), model_idx[r['model']], r['tt'], r['it'], r['ot'], r['ch'], r['cw'], r.get('credit')))
    if not recs and not evs: continue
    recs.sort(key=lambda x: x[0])
    d = sessions.setdefault(sid, {'w': ws_idx[folder], 'id': sid, 't': None, 'tai': None, 'r': [], 'ev': [], 'tk': {}})
    if title and not d['t']: d['t'] = title
    if title_ai and not d['tai']: d['tai'] = title_ai
    d['r'].extend(recs); d['ev'].extend(evs)
    for k, v in tks.items(): d['tk'][k] = d['tk'].get(k, 0) + v

sess_list = []
# 用 cwd 还原工作空间真实显示名（覆盖扫描阶段的 slug 短名）
for folder, cc in cwd_by_folder.items():
    if folder in ws_names and cc:
        ws_names[folder] = ws_display(folder, max(cc, key=cc.get))
for sid, d in sessions.items():
    if not d['r']: continue
    # AI 耗时：会话内事件时间戳（秒）排序，相邻间隔 ≤5 分钟视为连续工作并累计（超 5 分钟视为空闲不计）
    ev = sorted(set(d['ev'])) if d['ev'] else []
    busy_s = sum(ev[i+1]-ev[i] for i in range(len(ev)-1) if ev[i+1]-ev[i] <= 300)
    sess_list.append({'w': d['w'], 'id': sid[:8], 't': d.get('tai') or d.get('t') or '(无标题会话)', 'st': d['r'][0][0], 'r': d['r'],
                      'by': int(busy_s), 'tk': d['tk']})
sess_list.sort(key=lambda s: s['st'])

# ---- 抓取 WorkBuddy 官方模型积分倍率（官方文档《选择模型》页）----
# 两步抓取：文档 HTML 里提取内容 JS 文件名（哈希随构建变化）→ 再从内容 JS 的表格里解析 模型 → 倍率
# 失败时回退到本地缓存 multipliers.json，再回退到内置表
MULT_FALLBACK = {'glm-5.3': 0.79, 'glm-5.3-flash': 0.06, 'glm-5.2': 0.79, 'glm-5.1': 0.79,
                 'glm-5v-turbo': 0.95, 'kimi-k3': 1.62, 'kimi-k2.8': 0.77, 'kimi-k2.7': 0.57,
                 'kimi-k2.6': 0.52, 'minimax-m3': 0.25, 'hy4': 0.29, 'hy3': 0.0,
                 'deepseek-v4.1-flash': 0.03, 'deepseek-v4-flash': 0.06, 'deepseek-v4-pro': 0.16}
MULT_SRC = '内置表（未联网）'

def fetch_multipliers():
    global MULT_SRC
    import re as _re
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'multipliers.json')
    try:
        from urllib.request import urlopen, Request
        hdr = {'User-Agent': 'Mozilla/5.0'}
        base = 'https://www.workbuddy.cn'
        page = urlopen(Request(base + '/docs/workbuddymini/features/Select-Model', headers=hdr), timeout=8).read().decode('utf-8', 'ignore')
        m = _re.search(r'(workbuddymini_features_Select-Model\.md\.[A-Za-z0-9_-]+)((?:\.lean)?\.js)', page)
        if m:
            stem, suffix = m.group(1), m.group(2)
            mult = {}
            # lean.js 是精简版（不含表格正文），优先抓去掉 .lean 的完整内容文件
            for path in (stem + suffix.replace('.lean', ''), stem + suffix):
                try:
                    cj = urlopen(Request(base + '/docs/static/' + path, headers=hdr), timeout=8).read().decode('utf-8', 'ignore')
                except Exception:
                    continue
                pairs = _re.findall(r'<strong>([^<]+)</strong></td><td>[^<]*</td><td>(x[0-9.]+|限时免费)</td>', cj)
                for name, v in pairs:
                    mult[name.lower()] = 0.0 if '免费' in v else float(v.lstrip('x'))
                if len(mult) >= 3:
                    break
            if len(mult) >= 3:
                # 官方文档常滞后于 App 内模型列表，用内置表补齐缺失模型（官方值优先）
                for _k, _v in MULT_FALLBACK.items():
                    mult.setdefault(_k, _v)
                mult['_fetched_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
                try: open(cache, 'w', encoding='utf-8').write(json.dumps(mult, ensure_ascii=False))
                except Exception: pass
                MULT_SRC = '官方文档实时抓取 ' + mult['_fetched_at']
                return mult
    except Exception:
        pass
    if os.path.exists(cache):
        try:
            mult = json.load(open(cache, encoding='utf-8'))
            MULT_SRC = '本地缓存 ' + str(mult.get('_fetched_at', '(时间未知)'))
            return mult
        except Exception:
            pass
    return dict(MULT_FALLBACK)

MULT = fetch_multipliers()
mult_json = json.dumps(MULT, ensure_ascii=False, separators=(',', ':'))

# ---- 本机回归：反推官方积分计费权重（每个用户生成看板时用自己的日志现算）----
# 归因改进：不再用"倍率×全局系数"单公式拟合（R²≈0.47，受倍率表误差/促销时段污染），
# 改为逐模型直接回归 credit = a×未命中输入 + b×输出 + g×命中缓存（单位 1M token）——
# 同模型内无倍率假设误差，R² 显著更高；倍率仅用于旧版全局公式的对照参考
def _solve3(pts):
    """三元线性最小二乘（正规方程 + 高斯消元，纯标准库）。返回 (a,b,g,r2) 或 None"""
    if len(pts) < 30: return None
    M = [[0.0]*3 for _ in range(3)]; v = [0.0]*3
    for x1, x2, x3, y in pts:
        xs = [x1, x2, x3]
        for i in range(3):
            v[i] += xs[i]*y
            for j in range(3): M[i][j] += xs[i]*xs[j]
    for col in range(3):
        p = max(range(col, 3), key=lambda r_: abs(M[r_][col]))
        if abs(M[p][col]) < 1e-12: return None
        M[col], M[p] = M[p], M[col]; v[col], v[p] = v[p], v[col]
        for r_ in range(col+1, 3):
            f = M[r_][col]/M[col][col]
            for k in range(col, 3): M[r_][k] -= f*M[col][k]
            v[r_] -= f*v[col]
    x = [0.0]*3
    for r_ in (2, 1, 0):
        x[r_] = (v[r_] - sum(M[r_][k]*x[k] for k in range(r_+1, 3)))/M[r_][r_]
    a, b, g = x
    if a <= 0 or b <= 0 or g < 0: return None
    mean = sum(p[3] for p in pts)/len(pts)
    ss_tot = sum((p[3]-mean)**2 for p in pts)
    if ss_tot <= 0: return None
    ss_res = sum((p[3]-(a*p[0]+b*p[1]+g*p[2]))**2 for p in pts)
    return a, b, g, 1 - ss_res/ss_tot

def fit_credit_model():
    """逐模型回归官方积分计费：每模型各自求 (输入, 输出, 缓存) 积分权重。
    返回 (全局拟合(倍率折算, 兼容旧注脚), {model: {n,a,b,g,r2}})"""
    mkeys = sorted(MULT.keys(), key=len, reverse=True)
    by_model = {}
    pts_all = []
    for d in sessions.values():
        for rec in d['r']:
            c = rec[7]
            if not c or c <= 0: continue
            it, ot, ch = rec[3], rec[4], rec[5]
            x1, x2, x3 = max(0, it - ch)/1e6, ot/1e6, ch/1e6
            mname = models[rec[1]]
            by_model.setdefault(mname, []).append((x1, x2, x3, float(c)))
            mu = None
            ml = mname.lower()
            for k in mkeys:
                if ml.startswith(k): mu = MULT[k]; break
            if mu and mu > 0:
                pts_all.append((x1, x2, x3, float(c)/mu))
    fit_models = {}
    for mn, pts in by_model.items():
        r = _solve3(pts)
        if r:
            a, b, g, r2 = r
            fit_models[mn] = {'n': len(pts), 'a': round(a, 1), 'b': round(b, 1),
                              'g': round(g, 2), 'r2': round(r2, 2)}
    # 全局（倍率折算口径）仍保留，供对照
    glob = None
    if len(pts_all) >= 100:
        r = _solve3(pts_all)
        if r:
            a, b, g, r2 = r
            glob = {'n': len(pts_all), 'a': round(a, 1), 'b': round(b, 1), 'g': round(g, 2),
                    'r2': round(r2, 2)}
    return glob, fit_models

FIT, FIT_MODELS = fit_credit_model()

# ---- 模型牌价（同用量 API 直购价）：内置国产官方表 + LiteLLM 社区价目表实时抓取 ----
# 口径：元/百万 token，[输入, 输出, 缓存命中]。国产模型用各厂商开放平台官方牌价（2026-09 核实）；
# 第三方自定义模型（GPT/Claude/Gemini/Grok 等）生成时从 LiteLLM 价目表抓取（USD 按 USD_CNY 折算），
# 失败回退本地缓存 prices.json，再回退内置表。每次生成都尝试刷新，官方调价自动跟随。
USD_CNY = 7.2
PRICE_DOM = {'deepseek-v4.1-flash':[2,8,0.04],'deepseek-v4-flash':[2,8,0.04],'deepseek-v4-pro':[9,27,0.3],'deepseek':[9,27,0.3],
             'glm-5.3-flash':[0.8,2.8,0.23],'glm-5.3':[8,28,2],'glm-5.2-x':[8,28,2],'glm-5.2':[8,28,2],'glm-5.1':[8,28,2],
             'glm-5v-turbo':[7,26,1.8],'glm-5-turbo':[7,26,1.8],'glm':[6,22,1.5],
             'kimi-k3':[20,100,2],'kimi-k2.8':[6.5,27,1.1],'kimi-k2.7':[6.5,27,1.3],'kimi-k2.6':[6.5,27,1.1],'kimi':[6.5,27,1.1],'moonshot':[6.5,27,1.1],
             'hy4':[6,18,0.3],'hy3':[1,4,0.25],'hy':[1,4,0.25],'hunyuan':[1,4,0.25],
             'minimax-m3':[2.1,8.4,0.42],'minimax':[2.1,8.4,0.42],'m3':[2.1,8.4,0.42],
             'qwen3.8-max':[12,36,1.2],'qwen3.7':[2,8,0.2],'qwen3.6-flash':[0.37,2.94,0.04],'qwen-max':[12,36,1.2],'qwen-plus':[2,8,0.2],'qwen-turbo':[0.3,0.6,0.03],'qwen':[2,8,0.2],
             'doubao-seed-2.1-pro':[6,30,0.3],'doubao-seed-2.0-pro':[3.2,16,0.64],'doubao-seed-2.0-lite':[1.2,3.6,0.24],'doubao-seed':[1.2,3.6,0.24],'doubao':[6,30,0.3],
             'ernie-5':[4,18,0.4],'ernie':[4,18,0.4],'yi-lightning':[0.99,0.99,0.1],'yi':[0.99,0.99,0.1],
             'claude-opus-5':[150,750,15],'claude-opus-4-8':[110,550,11],
             'claude-opus-4':[36,180,3.6],'claude-sonnet-4':[21.6,108,2.16],'claude-haiku-4':[7.2,36,0.72],'claude':[36,180,3.6],
             'gpt-5.2-pro':[151,1210,15.1],'gpt-5.2':[12.6,100.8,1.26],'gpt-5-mini':[1.8,14.4,0.18],'gpt-5':[9,72,0.9],
             'gpt-4o':[18,72,1.8],'gpt-4.1':[14.4,57.6,1.44],'gpt':[9,72,0.9],
             'o3-pro':[144,576,14.4],'o4-mini':[7.9,31.7,0.79],'o3':[14.4,57.6,1.44],'o1':[108,432,10.8],
             'gemini-3-pro':[14.4,86.4,1.44],'gemini-3-flash':[3.6,21.6,0.36],'gemini-2.5-pro':[9,72,0.9],'gemini-2.5-flash':[1.08,4.32,0.11],'gemini':[9,72,0.9],
             'grok-4':[21.6,108,2.16],'grok':[21.6,108,2.16],
             'mistral-large':[3.6,10.8,0.36],'mistral':[3.6,10.8,0.36],
             'llama-4':[1.94,6.12,0.19],'llama':[1.94,6.12,0.19]}
_DOM_KEYS = sorted(PRICE_DOM.keys(), key=len, reverse=True)
PRICE_INTL_FALLBACK = {'gpt':[9,72,0.9],'claude':[36,180,3.6],'gemini':[9,72,0.9],'grok':[21.6,108,2.16],'mistral':[3.6,10.8,0.36],'llama':[1.94,6.12,0.19]}
PRICE_SRC = '内置表（未联网）'

def fetch_intl_prices(model_names):
    """对国产表未覆盖的模型，从 LiteLLM 社区价目表抓取官方牌价（USD/token → 元/百万），
    成功则写缓存 prices.json；失败读缓存。返回 (intl_map, src_str)"""
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prices.json')
    urlmap = {}
    src = None
    try:
        from urllib.request import urlopen, Request
        hdr = {'User-Agent': 'Mozilla/5.0'}
        for u in ('https://cdn.jsdelivr.net/gh/BerriAI/litellm@main/model_prices_and_context_window.json',
                  'https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json'):
            try:
                data = json.loads(urlopen(Request(u, headers=hdr), timeout=20).read().decode('utf-8', 'ignore'))
                if isinstance(data, dict) and len(data) > 100:
                    urlmap = data; src = ('jsdelivr' if 'jsdelivr' in u else 'github')
                    break
            except Exception:
                continue
    except Exception:
        pass
    intl = {}
    if urlmap:
        # LiteLLM 条目字段为 input_cost_per_token / output_cost_per_token（USD/token）；
        # 不少 key 带提供商前缀（gemini/gemini-3-pro、azure/command-r-plus），先归一化成基名
        base = {}
        for k, e in urlmap.items():
            if not isinstance(e, dict): continue
            if not isinstance(e.get('input_cost_per_token'), (int, float)): continue
            if not isinstance(e.get('output_cost_per_token'), (int, float)): continue
            bk = k.split('/')[-1].split('.')[-1].lower()
            if bk not in base: base[bk] = e
        bkeys = sorted(base.keys(), key=len, reverse=True)
        for mn in model_names:
            n = mn.lower()
            hit = next((k for k in bkeys if n.startswith(k)), None)
            if not hit: continue
            e = base[hit]
            pc = e.get('cache_read_input_token_cost') or 0
            intl[mn] = [round(e['input_cost_per_token']*1e6*USD_CNY, 2),
                        round(e['output_cost_per_token']*1e6*USD_CNY, 2),
                        round(pc*1e6*USD_CNY, 2)]
        if urlmap and not intl:
            # 抓取成功但本次所有模型都被内置表覆盖，未用到第三方价目
            return {}, 'LiteLLM 价目表已连通（本次模型均在国产官方表内）'
        if intl:
            intl['_fetched_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            intl['_src'] = 'LiteLLM 价目表实时抓取（%s，USD 按 %.1f 折算）' % (src, USD_CNY)
            try: open(cache, 'w', encoding='utf-8').write(json.dumps(intl, ensure_ascii=False))
            except Exception: pass
            return {k: v for k, v in intl.items() if not k.startswith('_')}, intl['_src']
    # 抓取失败 → 读缓存
    try:
        cached = json.loads(open(cache, encoding='utf-8').read())
        return {k: v for k, v in cached.items() if not k.startswith('_')}, 'LiteLLM 本地缓存 %s' % cached.get('_fetched_at', '?')
    except Exception:
        return {}, '内置表（未联网）'

MEMB_FALLBACK = {'hi': 0.05, 'mo': 0.035, 'yr': 0.028, 'src': '内置默认锚点',
                 'bases': {'standard': 2000, 'advanced': 4000, 'flagship': 20000}}

def fetch_membership():
    """实时抓取官方会员套餐，折算积分单价（元/积分）。
    链路：workbuddy.cn 首页 HTML → 版本号与主 JS 文件名 → personal-plans chunk → 解析套餐价格与基础积分。
    单价口径：月付原价 hi=price/基础积分、连续包月 mo=monthly-auto/基础积分、连续包年 yr=yearly-auto/12/基础积分，
    多档取中位数。三级回退：实时抓取 → membership.json 缓存 → 内置默认。"""
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'membership.json')
    try:
        from urllib.request import urlopen, Request
        import re as _re
        hdr = {'User-Agent': 'Mozilla/5.0'}
        page = urlopen(Request('https://workbuddy.cn/', headers=hdr), timeout=15).read().decode('utf-8', 'ignore')
        mver = _re.search(r'web/workbuddy/([a-f0-9]{32,40})/assets/(index-[A-Za-z0-9_-]+\.js)', page)
        if mver:
            base = 'https://download.codebuddy.cn/web/workbuddy/%s/assets/' % mver.group(1)
            idx_js = urlopen(Request(base + mver.group(2), headers=hdr), timeout=15).read().decode('utf-8', 'ignore')
            m2 = _re.search(r'["\'](?:\./)?(personal-plans-[A-Za-z0-9_-]+\.js)["\']', idx_js)
            if m2:
                plans_js = urlopen(Request(base + m2.group(1), headers=hdr), timeout=15).read().decode('utf-8', 'ignore')
                plans = {}
                for pm in _re.finditer(r'id:"(standard|advanced|flagship)",title:"([^"]+)".*?prices:\{(.*?)\},highlights:\[(.*?)\]', plans_js, _re.S):
                    pid, title, prices_raw, hl_raw = pm.groups()
                    def _num(s):
                        nm = _re.search(r'([\d.]+)', s or '')
                        return float(nm.group(1)) if nm else None
                    base_c = None
                    hm = _re.search(r'每月基础(\d+)积分', hl_raw)
                    if hm: base_c = float(hm.group(1))
                    if not base_c: continue
                    hi = _num(_re.search(r'monthly:\{price:"([^"]+)"', prices_raw).group(1)) if _re.search(r'monthly:\{price:"([^"]+)"', prices_raw) else None
                    moa = _re.search(r'"monthly-auto":\{price:"([^"]+)"', prices_raw)
                    yra = _re.search(r'"yearly-auto":\{price:"([^"]+)"', prices_raw)
                    if not hi: continue
                    plans[pid] = {'title': title, 'base': base_c, 'hi': hi,
                                  'mo': _num(moa.group(1)) if moa else None,
                                  'yr': _num(yra.group(1)) if yra else None}
                bases = {pid: int(p['base']) for pid, p in plans.items()}
                if len(plans) >= 2:
                    def _med(vals):
                        vs = sorted(v for v in vals if v)
                        return vs[len(vs)//2] if vs else None
                    hi = _med([p['hi']/p['base'] for p in plans.values()])
                    mo = _med([p['mo']/p['base'] for p in plans.values() if p['mo']])
                    yr = _med([p['yr']/12/p['base'] for p in plans.values() if p['yr']])
                    if hi and mo and yr and hi*0.7 <= mo <= hi and mo*0.7 <= yr <= mo:
                        out = {'hi': round(hi, 4), 'mo': round(mo, 4), 'yr': round(yr, 4),
                               'src': '官方定价页实时抓取 %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                               'bases': bases}
                        try: open(cache, 'w', encoding='utf-8').write(json.dumps(out, ensure_ascii=False))
                        except Exception: pass
                        return out
    except Exception:
        pass
    try:
        cached = json.loads(open(cache, encoding='utf-8').read())
        if cached.get('hi'):
            cached['src'] = '本地缓存（%s 抓取）' % cached.get('src', '?')
            return cached
    except Exception:
        pass
    return dict(MEMB_FALLBACK)

def resolve_prices(model_names):
    """逐模型解析单价：内置国产/国际表（长前缀优先）→ LiteLLM 抓取 → 兜底 [2,8,0.4]"""
    intl, psrc = fetch_intl_prices(model_names)
    res = {}
    used_intl = 0
    for mn in model_names:
        n = mn.lower()
        hit = next((k for k in _DOM_KEYS if n.startswith(k)), None)
        if hit: res[mn] = PRICE_DOM[hit]; continue
        if mn in intl: res[mn] = intl[mn]; used_intl += 1; continue
        hit = next((k for k in sorted(PRICE_INTL_FALLBACK, key=len, reverse=True) if n.startswith(k)), None)
        res[mn] = PRICE_INTL_FALLBACK[hit] if hit else [2, 8, 0.4]
    if model_names and used_intl == 0:
        psrc = '国产官方牌价（LiteLLM 备用）'
    return res, psrc

PRICES, PRICE_SRC = resolve_prices(models)
MEMB = fetch_membership()

# ---- 日/月聚合（环比趋势与套餐判定用）：从会话请求现算，无需历史快照 ----
daily_agg = {}
month_agg = {}
for sid, d in sessions.items():
    days_seen = set()
    for rec in d['r']:
        ts = rec[0]*60
        dt = datetime.datetime.fromtimestamp(ts)
        dk = dt.strftime('%Y-%m-%d')
        da = daily_agg.setdefault(dk, {'m': int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())*1000,
                                       'ocred': 0.0, 'tot': 0, 'turns': 0})
        da['tot'] += rec[2]; da['turns'] += 1
        if rec[7] is not None: da['ocred'] += rec[7]
        days_seen.add(dk)
        month_agg[dt.strftime('%Y-%m')] = month_agg.get(dt.strftime('%Y-%m'), 0.0) + (rec[7] or 0)
    for dk in days_seen:
        daily_agg[dk]['nsess'] = daily_agg[dk].get('nsess', 0) + 1
DAILY_AGG = sorted(daily_agg.values(), key=lambda x: x['m'])

DATA = {'genMs': gen_ms, 'gen': now.strftime('%Y-%m-%d %H:%M'), 'ws': list(ws_names.values()), 'models': models, 'sess': sess_list, '_fit': FIT, '_fitModels': FIT_MODELS, '_prices': PRICES, '_priceSrc': PRICE_SRC, '_memb': MEMB, '_daily': DAILY_AGG}
js = json.dumps(DATA, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')

TEMPLATE = r'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WorkBuddy Token消耗看板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#fafaf9;--card:#fff;--border:#e8e5df;--border2:#e4e1da;--border3:#efede8;--t1:#1f2328;--t2:#6f6c64;--t3:#9a978f;--t4:#b5b2a9;--t5:#a3a099;--t6:#55524b;--wcnm:#374151;--tdc:#3d3b36;--row:#f2f0ea;--empty:#f4f2ed;--hover:#fbfaf7;--focus:#c9c6be;--sep:#d4d1c9;--selbg:#e9f0f7;--selfg:#3f6b99;--selbd:#aec9e0;--red:#d64545;--blue:#5f8cb6;--tipbg:#1f2328;--tiptc:#fff}
body.dark{--bg:#1c1b19;--card:#262521;--border:#3a3833;--border2:#3a3833;--border3:#35332e;--t1:#f0eee9;--t2:#b5b2a9;--t3:#8a877f;--t4:#6f6d66;--t5:#8a877f;--t6:#d5d2ca;--wcnm:#c5c2ba;--tdc:#c5c2ba;--row:#323029;--empty:#2e2c29;--hover:#2a2925;--focus:#55524b;--sep:#4a4842;--selbg:#2c3a47;--selfg:#a9c9e4;--selbd:#4a6a85;--red:#e06666;--blue:#8fb8d9;--tipbg:#f0eee9;--tiptc:#1c1b19;color-scheme:dark}
body{font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--t1);padding:22px 18px 60px;max-width:1600px;margin:0 auto}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;align-items:start}
.colL,.colR{min-width:0}
h1{font-size:20px;font-weight:700;letter-spacing:.3px}
.sub{font-size:11px;color:var(--t3);margin-top:3px;display:flex;align-items:center;gap:8px}
.tbtn{border:1px solid var(--border2);background:var(--card);color:var(--t2);font-size:11px;padding:1px 10px;border-radius:999px;cursor:pointer}
.tbtn:hover{border-color:var(--focus);color:var(--t1)}
header{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px}
.pill{border:1px solid var(--border2);background:var(--card);color:var(--t2);font-size:12px;padding:5px 14px;border-radius:6px;cursor:pointer;margin-left:6px}
.pill.on{background:var(--selbg);color:var(--selfg);border-color:var(--selbd)}
.datepick{border:1px solid var(--border2);background:var(--card);color:var(--t2);font-size:12px;padding:4px 8px;border-radius:6px;outline:none;vertical-align:middle}
.datepick:focus{border-color:var(--focus)}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin-bottom:14px}
.sec-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:6px}
.sec-h h2{font-size:14px;font-weight:400}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:14px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 15px;min-width:0}
.kpi .k{font-size:11px;color:var(--t3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi .v{font-size:20px;font-weight:700;color:var(--t1);margin-top:6px;white-space:nowrap}
.kpi .v small{font-size:12px;color:var(--t3);font-weight:600;margin-left:3px}
.t5{display:flex;align-items:center;justify-content:space-between;gap:6px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:5px 9px;min-width:0;cursor:pointer;transition:background .15s,border-color .15s}
.t5:hover{border-color:var(--focus)}
.t5.on{background:var(--selbg);border-color:var(--selbd)}
.t5.on .k,.t5.on .v{color:var(--selfg)}
.t5 .k{font-size:9px;color:var(--t5);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t5 .k i{width:6px;height:6px;margin-right:3px;vertical-align:0}
.t5 .v{font-size:11px;font-weight:600;color:var(--t6);white-space:nowrap;flex:none}
.heat-wrap{max-height:640px;overflow-y:auto;border:1px solid var(--border3);border-radius:8px;padding:6px 8px}
.hrow{display:flex;align-items:center;gap:2px;margin-bottom:-1px}
.hlab{width:42px;flex:none;font-size:10px;color:var(--t3)}
.hcell{flex:1;height:7.3px;border-radius:0;background:var(--empty);cursor:default}
.hhead{display:flex;gap:2px;margin:0 1px 4px;padding-left:8px;padding-right:8px}
.hhead span{flex:1;min-width:0;text-align:center;font-size:9px;color:var(--t4)}
.legend{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--t3)}
.legend i{width:14px;height:10px;border-radius:2px;display:inline-block}
.caption{font-size:11px;color:var(--t3);margin-bottom:6px}
.bandbar{display:flex;height:14px;border-radius:5px;overflow:hidden;margin:8px 0 10px}
.bandbar div{height:100%}
.bandrow{display:flex;justify-content:space-between;font-size:11px;color:var(--t2);margin-bottom:3px}
.tools{display:flex;gap:6px;align-items:center}
.tools input{border:1px solid var(--border2);border-radius:6px;padding:5px 10px;font-size:12px;width:110px;background:var(--card);color:var(--t1);outline:none}
.tools input:focus{border-color:var(--focus)}
.chip{border:1px solid var(--border2);background:var(--card);color:var(--t2);font-size:11px;padding:4px 10px;border-radius:6px;cursor:pointer}
.chip.on{background:var(--selbg);color:var(--selfg);border-color:var(--selbd)}
.crumb{font-size:12px;color:var(--t3);margin-bottom:10px;display:none;align-items:center;gap:10px}
.crumb a{color:var(--t2);text-decoration:none;cursor:pointer}
.crumb a:hover{color:var(--selfg)}
.crumb .cur{color:var(--selfg)}
.crumb .sep{margin:0 6px;color:var(--sep)}
.lrow{display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid var(--row);cursor:pointer}
.lrow:hover{background:var(--hover)}
.lrow .nm{flex:1.4;min-width:0;font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lrow .meta{flex:.8;font-size:11px;color:var(--t3);white-space:nowrap}
.lrow .bar{flex:1.2;height:7px;background:var(--empty);border-radius:4px;overflow:hidden}
.lrow .bar i{display:block;height:100%;border-radius:4px;background:var(--blue)}
.lrow .num{flex:.9;text-align:right;font-size:12px;font-weight:700;white-space:nowrap}
.lrow .num em{font-style:normal;color:var(--t4);font-weight:600;font-size:10px;margin-left:4px}
.empty{font-size:12px;color:var(--t3);padding:20px 0;text-align:center}
.subgrid{display:grid;grid-template-columns:minmax(0,1fr);gap:0}
.wcard{display:flex;align-items:center;gap:10px;padding:3px 2px;border-bottom:1px solid var(--row);cursor:pointer;background:transparent;min-width:0}
.wcard:hover{background:var(--hover)}
.wc-nm{flex:none;width:16%;min-width:0;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--wcnm)}
.wc-tt{flex:none;width:72px;text-align:right;font-size:13px;font-weight:400;color:var(--t1);white-space:nowrap}
.wc-meta{flex:none;width:88px;text-align:right;font-size:10px;color:var(--t3);white-space:nowrap}
.wviz{flex:1.5;display:flex;gap:1px;min-width:0;overflow:hidden}
.wcell{flex:1 1 0;min-width:0;height:10.4px;border-radius:0;background:var(--empty)}
.panel-scroll{max-height:290px;overflow-y:auto;border:1px solid var(--border3);border-radius:8px;padding:2px 10px}
.wsn{flex:none;font-size:10px;border:1px solid var(--border2);border-radius:5px;padding:1px 3px;background:var(--card);color:var(--t2);outline:none}
.band4{display:flex;gap:6px;margin-bottom:12px}
.b4{flex:1;display:flex;justify-content:space-between;align-items:baseline;gap:6px;border:1px solid var(--border3);border-radius:6px;padding:5px 9px;background:var(--card);min-width:0}
.b4k{font-size:10px;color:var(--t3);white-space:nowrap}
.b4v{font-size:12.5px;font-weight:700;color:var(--t1);white-space:nowrap}
.kpi .s{font-size:10px;color:var(--t4);margin-top:2px;white-space:nowrap}
.view-h{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.view-t{font-size:13px;font-weight:700}
.view-m{font-size:11px;color:var(--t3)}
svg text{font-family:inherit}
table.rt{width:100%;border-collapse:collapse;font-size:11px}
table.rt th{position:sticky;top:0;background:var(--card);text-align:right;color:var(--t3);font-weight:600;padding:5px 6px;border-bottom:1px solid var(--border)}
table.rt th:first-child,table.rt td:first-child{text-align:left}
table.rt td{text-align:right;padding:4px 6px;border-bottom:1px solid var(--row);color:var(--tdc);white-space:nowrap}
.rt-wrap{max-height:320px;overflow:auto;border:1px solid var(--border3);border-radius:8px}
#tip{position:fixed;background:var(--tipbg);color:var(--tiptc);padding:7px 10px;border-radius:7px;font-size:11px;pointer-events:none;opacity:0;transition:opacity .08s;white-space:pre;z-index:99;line-height:1.55}
</style></head><body>

<header style="align-items:flex-end">
  <div>
    <h1>WorkBuddy Token消耗看板</h1><div class="sub"><span id="genAt"></span><button class="tbtn" id="themeBtn" type="button">深色</button></div>
  </div>
  <div style="flex:none;white-space:nowrap">
    <button class="pill" data-w="1">1 天</button><button class="pill" data-w="3">3 天</button><button class="pill" data-w="7">近 7 天</button><button class="pill on" data-w="30">近 30 天</button><input type="date" id="dStart" class="datepick" title="起始日期"><span style="color:var(--t4);font-size:11px;margin:0 2px">至</span><input type="date" id="dEnd" class="datepick" title="结束日期">
  </div>
</header>

<section class="kpis" id="kpis"></section>

<div class="wrap"><div class="colL">

<section class="card">
  <div class="sec-h"><h2>工作空间活跃分布</h2>
    <div class="tools" id="wsTools"><button class="chip" data-m="ses">按会话</button><button class="chip on" data-m="ws">按工作空间</button><input id="wsSearch" placeholder="搜索…"><span id="wsSorts" style="display:none"><button class="chip on" data-s="tt">按 Token</button><button class="chip" data-s="n">按会话数</button></span><select id="wsMult" class="wsn" title="可视化倍率：方块总数=窗口天数×倍率"><option value="1">1X</option><option value="2">2X</option><option value="3" selected>3X</option><option value="4">4X</option><option value="5">5X</option></select></div>
  </div>
  <div class="crumb" id="crumb"></div>
  <div class="panel-scroll"><div id="panel"></div></div>
</section>

<section class="card">
  <div class="sec-h"><h2>单次请求大小分布</h2><span class="caption" style="margin:0" id="mdCap"></span></div>
  <svg id="mdSvg" width="100%" height="240"></svg>
  <div style="font-size:10px;color:var(--t3);margin-top:6px">每模型一条分布形状（各自归一）· 竖线=该模型 50% · 灰点=90% · 虚线=全局 50% · 数值下条形=两列共享刻度的大小对比（深=50% 浅=90%）</div>
</section>
</div>

<div class="colR">
<section class="card" id="heatCard">
  <div class="sec-h"><h2>日活分布</h2><div class="legend" id="heatLegend"></div></div>
  <div class="band4" id="band4"></div>
  <div class="hhead" id="hhead"></div>
  <div class="heat-wrap" id="heatWrap"></div>
</section>

<section class="card">
  <div class="sec-h"><h2>0–24 时分布 · 按模型</h2><div class="legend" id="rdLegend" style="flex-wrap:wrap;justify-content:flex-end"></div></div>
  <svg id="rdSvg" width="100%" height="112"></svg>
</section>

<section class="card">
  <div class="sec-h"><h2>每日模型用量比例</h2></div>
  <svg id="dmSvg" width="100%" height="102"></svg>
  <div style="font-size:10px;color:var(--t3);margin-top:4px">x=日期（最新在右）· y=当日 Token · 颜色=模型 · 点击下方模型卡片高亮单模型</div>
  <section class="kpis" id="top5" style="margin:12px 0 0;gap:6px;grid-template-columns:repeat(5,1fr)"></section>
</section>

<section class="card">
  <div class="sec-h"><h2>积分消耗 TOP 榜</h2><span class="caption" style="margin:0" id="tcCap"></span></div>
  <div id="topCred" style="display:grid;grid-template-columns:1fr 1fr;gap:16px"></div>
</section>
</div></div>

<div id="tip"></div>

<script>const D=__DATA__;</script>
<script>
const $=s=>document.querySelector(s);
const MCL=['#4e7fbc','#c8894a','#59a5a0','#b05a5a','#8172b3','#7a9a4e','#c98ba6','#9aa8bd'];
const MCD=['#7aa5d4','#d8a76b','#6cc0ba','#d48080','#a798cf','#9dba6e','#d8a3b8','#aab6c8'];
const RMPL=['#f4f2ed','#e8eef5','#cfddeb','#aec9e0','#88abd0','#5f8cb6','#3f6b99','#2b4f73'];
const RMPD=['#2e2c29','#33404d','#3a4f61','#42607a','#4b6f8f','#5682a6','#6b9abf','#8fb8d9'];
const CJL={grid:'#f0ede7',axis:'#b5b2a9',axis2:'#c9c6be',blue:'#5f8cb6',dash:'#c9c6be',base:'#d4d1c9',dim:'#c9c6be',dimBar:'#e4e1da',other:'#8C8C8C',red:'#d64545',muted:'#9a978f',t1:'#1f2328'};
const CJD={grid:'#323029',axis:'#6f6d66',axis2:'#6f6d66',blue:'#8fb8d9',dash:'#55524b',base:'#4a4842',dim:'#55524b',dimBar:'#3a3833',other:'#7d8b96',red:'#e06666',muted:'#8a877f',t1:'#f0eee9'};
let DARK=false,CJ=CJL,MC=MCL,RAMP=RMPL;
try{if(localStorage.getItem('td-dark')==='1'){DARK=true;CJ=CJD;MC=MCD;RAMP=RMPD;}}catch(e){}
const modelColor=i=>MC[i%MC.length];
// 同用量 API 单价（元 / 百万 token）：[输入, 输出, 缓存命中]。脚本生成时解析：
// 国产模型用内置官方牌价，第三方/国际模型从 LiteLLM 社区价目表实时抓取（USD 按汇率折算），失败回退缓存/内置表
const PRICES=D._prices||{};
const PRICE_SRC=D._priceSrc||'';
const MP=D.models.map(m=>PRICES[m]||[2,8,0.4]);
// WorkBuddy 官方积分倍率（脚本生成时从官方文档实时抓取，见 MULT._src）与模型映射
const MULT=__MULT__;
const MULT_SRC='__MULT_SRC__';
const multOf=name=>{const n=(name||'').toLowerCase();for(const k in MULT){if(k==='_fetched_at')continue;if(n.startsWith(k))return MULT[k];}return 1.0;};
const MPM=D.models.map(m=>multOf(m));
let win=30, customStart=0, customEnd=0, wsSort='tt', q='', state={ws:null,ses:null};
let wsMult=3,PCTX=null,wsMode='ws',SHD=new Map();
function vizCells(m){
  const days=PCTX?PCTX.days:[],avgD=PCTX?PCTX.avgD:1;
  let h='';
  for(const dk of days){
    const arr=(m&&(m[dk]||m.get&&m.get(dk)))||new Array(24).fill(0);
    for(let k=0;k<wsMult;k++){
      const a=Math.floor(24*k/wsMult),b=Math.floor(24*(k+1)/wsMult);
      let v=0;for(let hh=a;hh<b;hh++)v+=arr[hh]||0;
      const l=v==0?0:Math.min(7,Math.max(1,1+Math.floor(Math.log(v/avgD/0.001)/Math.log(350)*7)));
      h+='<div class="wcell" style="background:'+RAMP[l]+'" title="'+dk+' '+a+':00–'+b+':00 · '+fmt(v)+' token"></div>';
    }
  }
  return h;
}
function buildViz(w){
  const pc=PCTX||{whd:{},days:[],avgD:1};
  const m=pc.whd[w]||{};
  let h='';
  for(const dk of pc.days){
    const arr=m[dk]||new Array(24).fill(0);
    for(let k=0;k<wsMult;k++){
      const a=Math.floor(24*k/wsMult),b=Math.floor(24*(k+1)/wsMult);
      let v=0;for(let hh=a;hh<b;hh++)v+=arr[hh]||0;
      const lv=v==0?0:Math.min(7,Math.max(1,1+Math.floor(Math.log(v/pc.avgD/0.001)/Math.log(350)*7)));
      h+='<div class="wcell" style="background:'+RAMP[lv]+'" title="'+dk+' '+a+':00–'+b+':00 · '+fmt(v)+' token"></div>';
    }
  }
  return h||'<div class="wcell"></div>';
}
$('#genAt').textContent='数据截至 '+D.gen+' · 请求级真实 usage · 单文件离线可看';
const genMs=D.genMs;
const cut=()=>win?genMs-win*86400e3:(customStart?customStart*60000:0);
const cutHi=()=>(!win&&customEnd)?customEnd*60000:Infinity;
function fmt(n){if(n>=1e8)return (n/1e8).toFixed(2)+' 亿';if(n>=1e4)return (n/1e4).toFixed(1)+' 万';return String(Math.round(n));}
const nf=n=>n.toLocaleString('en-US');
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
function hm(n){return new Date(n*60000);}
function fdate(d){const p=x=>String(x).padStart(2,'0');return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());}
function ftime(d){const p=x=>String(x).padStart(2,'0');return p(d.getHours())+':'+p(d.getMinutes());}

function iterReq(cb){
  const c=cut();
  for(const s of D.sess){
    let tt=0,tn=0;
    const reqs=[];
    for(const r of s.r){const ts=r[0]*60000;if(ts<c)continue;reqs.push(r);tt+=r[2];tn++;}
    if(tn)cb(s,reqs,tt,tn);
  }
}
function aggregate(){
  const c=cut(),hiC=cutHi();
  const daily={},ws={},band=[0,0,0,0];
  const hr=new Array(24).fill(0);
  const mTot=new Array(D.models.length).fill(0);
  const dm={};
  let tot=0,inp=0,out=0,ch=0,turns=0,nsess=0,cost=0,creds=0,ocred=0,ocredN=0,turnsN=0,busy=0,saved=0;
  const tools={};
  const sessAgg=[];
  const mCred=new Array(D.models.length).fill(0);
  const mApi=new Array(D.models.length).fill(0);
  for(const s of D.sess){
    let tt=0,tn=0,first=0,last=0;
    for(const r of s.r){const ts=r[0]*60000;if(ts<c||ts>=hiC)continue;
      const d=hm(r[0]);const dk=fdate(d);const h=d.getHours();
      (daily[dk]=daily[dk]||{h:new Array(24).fill(0),hh:new Array(72).fill(0),tt:0});
      daily[dk].h[h]+=r[2];daily[dk].tt+=r[2];
      daily[dk].hh[h*3+Math.floor(d.getMinutes()/20)]+=r[2];
      hr[h]+=r[2];mTot[r[1]]+=r[2];
      (dm[dk]=dm[dk]||{});dm[dk][r[1]]=(dm[dk][r[1]]||0)+r[2];
      const wn=s.w;ws[wn]=ws[wn]||{tt:0,n:0,cred:0,api:0};ws[wn].tt+=r[2];if(r[7]!=null)ws[wn].cred+=r[7];
      const bi=Math.floor(h/6);band[bi]+=r[2];
      tot+=r[2];inp+=r[3];out+=r[4];ch+=r[5];turns++;tn++;tt+=r[2];if(r[7]!=null){ocred+=r[7];ocredN++;}
      const pp=MP[r[1]];const cht=Math.max(0,Math.min(r[5],r[3]));const cwt=Math.max(0,Math.min(r[6]||0,r[3]-cht));const chunk=(cht*pp[2]+cwt*1.25*pp[0]+(r[3]-cht-cwt)*pp[0]+r[4]*pp[1])/1e6;cost+=chunk;
      saved+=cht*(pp[0]-pp[2])/1e6;  // 缓存省钱：命中部分若按输入价买 API 需多付的差价
      if(r[7]!=null){mCred[r[1]]+=r[7];mApi[r[1]]+=chunk;ws[wn].api+=chunk;}
      creds+=r[2]/1e6*MPM[r[1]];
      if(!first)first=r[0];last=r[0];}
    if(tn){nsess++;(ws[s.w]=ws[s.w]||{tt:0,n:0,cred:0}).n+=1;sessAgg.push({s,tt,tn,first,last});busy+=s.by||0;const tk=s.tk||{};for(const k in tk)tools[k]=(tools[k]||0)+tk[k];}
    else s._=0;
  }
  const wsArr=D.ws.map((nm,i)=>({i,n:nm,tt:(ws[i]||{tt:0}).tt,cnt:(ws[i]||{n:0}).n,cred:(ws[i]||{cred:0}).cred,api:(ws[i]||{api:0}).api})).filter(x=>x.tt>0);
  const tkArr=Object.entries(tools).sort((a,b)=>b[1]-a[1]);
  return {daily,wsArr,band,tot,inp,out,ch,turns,nsess,sessAgg,hr,mTot,dm,cost,creds,ocred,ocredN,busy,tkArr,saved,mCred,mApi};
}

function renderKpis(A){
  const days=Object.keys(A.daily).length||1;
  const ce=A.inp?A.ch/A.inp*100:0;
  const ocredS=A.ocred>=100?Math.round(A.ocred).toLocaleString():A.ocred.toFixed(2);
  const cover=A.turns?Math.round(A.ocredN/A.turns*100):0;
  const money=A.cost>=1e4?'¥'+(A.cost/1e4).toFixed(1)+' 万':'¥'+A.cost.toFixed(1);
  const MB=D._memb||{hi:0.05,mo:0.035,yr:0.028,src:'',bases:{}};
  const yuanLo=A.ocred*MB.yr,yuanHi=A.ocred*MB.hi;
  const fy=v=>v>=1e4?'¥'+(v/1e4).toFixed(1)+' 万':v>=100?'¥'+Math.round(v).toLocaleString():'¥'+v.toFixed(1);
  const yuanS=yuanHi>=100?fy(yuanLo)+' ~ '+fy(yuanHi):'¥'+yuanLo.toFixed(2)+' ~ ¥'+yuanHi.toFixed(2);
  const busyH=A.busy/3600;
  const busyS=busyH>=1?busyH.toFixed(1)+' 小时':Math.round(busyH*60)+' 分钟';
  const sub=t=>'<span style="display:block;font-size:11px;color:var(--t3);font-weight:400;margin-top:1px">'+t+'</span>';
  const toolS=nf(A.tkArr.length)+' 个'+(A.tkArr.length?sub('主力 '+esc(A.tkArr[0][0])):'');
  // ---- 环比：本期 vs 上一期同等长度窗口（从按日聚合现算，无需历史快照）----
  let prev={ocred:null,tot:null,turns:null};
  try{
    const c=cut(),hi=Math.min(cutHi(),genMs),len=hi-c;
    if(len>0&&D._daily&&D._daily.length){
      const sum=a=>a.reduce((x,y)=>x+y,0);
      const inWin=(x,lo,hi_)=>x.m>=lo&&x.m<hi_;
      const cur=D._daily.filter(x=>inWin(x,c,hi)),pre=D._daily.filter(x=>inWin(x,c-len,c));
      const acc=a=>({ocred:sum(a.map(x=>x.ocred)),tot:sum(a.map(x=>x.tot)),turns:sum(a.map(x=>x.turns))});
      prev=acc(pre);prev.cur=acc(cur);
    }
  }catch(e){}
  const dlt=(cur,pre)=>{
    if(pre==null||!(pre>0)||cur==null)return '';
    const d=(cur-pre)/pre*100;
    if(!isFinite(d)||Math.abs(d)<0.5)return sub('环比持平');
    const up=d>0;
    return sub('<span style="color:var(--'+(up?'red':'blue')+')">'+(up?'↑':'↓')+Math.abs(d).toFixed(0)+'%</span> 上期 '+ (pre>=1e4?fmt(pre):fmt(pre)));
  };
  // ---- 逐模型实测积分率（归因改进后的回归结果）----
  const FM=D._fitModels||{};
  const fmKeys=Object.keys(FM).sort((a,b)=>FM[b].n-FM[a].n).slice(0,3);
  const fitTxt=fmKeys.length
    ?'本机逐模型实测计费（回归 R² 按模型内计算）：'+fmKeys.map(m=>esc(m)+' 每1M输入 '+FM[m].a+' / 每1M输出 '+FM[m].b+' 积分（R²='+FM[m].r2+'）').join('；')+'。'
    :'本机样本不足，未做计费公式回归。';
  $('#kpis').innerHTML=[
    ['合计 Token',fmt(A.tot)+dlt(A.tot,prev.tot)],
    ['日均（有数据天）',fmt(A.tot/days)],
    ['会话',nf(A.nsess)],
    ['轮次',nf(A.turns)+dlt(A.turns,prev.turns)],
    ['输入 / 输出比',(A.out?Math.round(A.inp/A.out):0)+':1'],
    ['缓存命中率',ce.toFixed(1)+'%'+(A.saved>0?sub('为你省 '+fy(A.saved)+'（API 口径）'):'')],
    ['使用工具',toolS],
    ['AI 耗时',busyS+sub('事件间隔≤5 分钟合并')],
    ['官方实际积分',ocredS+dlt(A.ocred,prev.ocred)],
    ['消耗金额估算',yuanS],
    ['同用量 API 直购价',money]
  ].map(x=>'<div class="kpi"><div class="k">'+x[0]+'</div><div class="v">'+x[1]+'</div></div>').join('')
  +'<div style="grid-column:1/-1;font-size:11px;opacity:.55;line-height:1.5;padding-top:2px">「官方实际积分」= 日志逐请求 credit 汇总（覆盖率 '+cover+'%），与 App 账单同源。'+fitTxt+'1 积分 ≈ '+MB.hi+' 元（月付原价）／'+MB.mo+'（连续包月）／'+MB.yr+'（连续包年）（积分单价源：'+esc(MB.src)+'）。「同用量 API 直购价」为按厂商牌价的理论费用，非实际支出（价格源：'+esc(PRICE_SRC)+'）；「缓存省的钱」为缓存命中部分按输入价计的差价，同一口径。</div>';
}

function renderTopCred(A){
  const wrap=$('#topCred');if(!wrap)return;
  const totC=A.ocred;
  const fy=v=>v>=1e4?'¥'+(v/1e4).toFixed(1)+' 万':v>=100?'¥'+Math.round(v).toLocaleString():'¥'+v.toFixed(1);
  const rows=(arr,names,colors)=>arr.length?arr.map((x,i)=>{
    const pct=totC>0?(x.cred/totC*100):0;
    return '<div style="display:flex;align-items:center;gap:8px;margin:4px 0" data-tip="'+esc(names[x.i])+'\n'+Math.round(x.cred).toLocaleString()+' 积分 · 占 '+pct.toFixed(1)+'% · API 直购 '+fy(x.api)+'">'
      +'<span style="width:14px;flex:none;font-size:11px;color:var(--t3)">'+(i+1)+'</span>'
      +'<span style="flex:1;min-width:0"><span style="display:block;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+esc(names[x.i])+'</span>'
      +'<span style="display:block;height:5px;border-radius:3px;background:var(--empty);margin-top:3px"><span style="display:block;height:100%;width:'+Math.max(2,pct)+'%;border-radius:3px;background:'+colors(x.i)+'"></span></span></span>'
      +'<span style="flex:none;font-size:12px;font-weight:600">'+Math.round(x.cred).toLocaleString()+'</span></div>';
  }).join(''):'<div class="empty">无积分数据</div>';
  const mArr=D.models.map((m,i)=>({i,cred:A.mCred[i]||0,api:A.mApi[i]||0}))
    .filter(x=>x.cred>0).sort((a,b)=>b.cred-a.cred).slice(0,6);
  const wArr2=A.wsArr.map(x=>({i:x.i,cred:x.cred||0,api:x.api||0})).filter(x=>x.cred>0).sort((a,b)=>b.cred-a.cred).slice(0,6);
  wrap.innerHTML='<div><div style="font-size:12px;font-weight:600;color:var(--t2);margin-bottom:6px">按模型</div>'+rows(mArr,D.models,i=>modelColor(i))+'</div>'
    +'<div><div style="font-size:12px;font-weight:600;color:var(--t2);margin-bottom:6px">按工作空间</div>'+rows(wArr2,D.ws,i=>'var(--blue)')+'</div>';
  $('#tcCap').textContent=totC>0?('窗口内合计 '+Math.round(totC).toLocaleString()+' 积分'):'';
}

function renderHeat(A){
  const dates=Object.keys(A.daily).sort().reverse();
  const head=$('#hhead');
  head.innerHTML='<span style="width:42px;flex:none"></span>'+Array.from({length:72},(_,i)=>'<span style="text-align:left">'+(i%3==0?(i/3):'')+'</span>').join('');
  // 每小时 3 格（20 分钟粒度）；色阶：最深 = 窗口日均×0.35，最浅 = 窗口日均×0.001（对数插值）
  const avg=A.tot/(dates.length||1);
  const wrap=$('#heatWrap');let html='';
  for(const d of dates){
    html+='<div class="hrow"><div class="hlab">'+d.slice(5)+'</div>';
    for(let i=0;i<72;i++){
      const v=A.daily[d].hh?A.daily[d].hh[i]:0;
      const lv=v==0?0:Math.min(7,Math.max(1,1+Math.floor(Math.log(v/avg/0.001)/Math.log(350)*7)));
      const h0=Math.floor(i/3),m0=(i%3)*20,m1=m0+20,pm=x=>String(x).padStart(2,'0');
      const tip=d+' '+h0+':'+pm(m0)+'–'+(m1==60?h0+1:h0)+':'+(m1==60?'00':pm(m1))+'\n'+nf(v)+' token（占当日 '+(A.daily[d].tt?(v/A.daily[d].tt*100).toFixed(1):0)+'%）';
      html+='<div class="hcell" style="background:'+RAMP[lv]+'" data-tip="'+tip.replace(/\n/g,'&#10;')+'"></div>';
    }
    html+='</div>';
  }
  wrap.innerHTML=html||'<div class="empty">该时间范围内无数据</div>';
  let lg='<span>低</span>';for(let i=1;i<8;i++)lg+='<i style="background:'+RAMP[i]+'"></i>';lg+='<span>高</span>';
  $('#heatLegend').innerHTML=lg;
}
document.addEventListener('mousemove',e=>{
  const t=e.target;
  const tip=$('#tip');
  if(t.classList&&t.classList.contains('hcell')&&t.dataset.tip){
    tip.textContent=t.dataset.tip;tip.style.opacity=1;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
  }else if(t.tagName==='circle'&&t.dataset.tip){
    tip.textContent=t.dataset.tip;tip.style.opacity=1;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
  }else if(t.tagName==='path'&&t.dataset.tip){
    tip.textContent=t.dataset.tip;tip.style.opacity=1;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
  }else if(t.tagName==='td'&&t.closest('tr')&&t.closest('tr').dataset.tip){
    tip.textContent=t.closest('tr').dataset.tip;tip.style.opacity=1;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
  }else if(t.tagName==='rect'&&t.dataset.tip){
    tip.textContent=t.dataset.tip;tip.style.opacity=1;tip.style.left=(e.clientX+12)+'px';tip.style.top=(e.clientY+12)+'px';
  }else{tip.style.opacity=0;}
});

function smoothLine(pts){
  // 单调三次插值（Fritsch-Carlson）：平滑且不过冲
  const n=pts.length;
  if(n<3)return 'M'+pts.map(p=>p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' L');
  const dx=[],m=[],t=new Array(n);
  for(let i=0;i<n-1;i++)dx.push(pts[i+1][0]-pts[i][0]);
  for(let i=0;i<n-1;i++)m.push((pts[i+1][1]-pts[i][1])/dx[i]);
  t[0]=m[0];t[n-1]=m[n-2];
  for(let i=1;i<n-1;i++)t[i]=(m[i-1]*m[i]<=0)?0:(m[i-1]+m[i])/2;
  for(let i=0;i<n-1;i++){
    if(m[i]===0){t[i]=0;t[i+1]=0;continue;}
    const a=t[i]/m[i],b=t[i+1]/m[i],s=a*a+b*b;
    if(s>9){const f=3/Math.sqrt(s);t[i]=f*a*m[i];t[i+1]=f*b*m[i];}
  }
  let d='M'+pts[0][0].toFixed(1)+' '+pts[0][1].toFixed(1);
  for(let i=0;i<n-1;i++){
    const h=dx[i];
    d+=' C'+(pts[i][0]+h/3).toFixed(1)+' '+(pts[i][1]+t[i]*h/3).toFixed(1)+' '+(pts[i+1][0]-h/3).toFixed(1)+' '+(pts[i+1][1]-t[i+1]*h/3).toFixed(1)+' '+pts[i+1][0].toFixed(1)+' '+pts[i+1][1].toFixed(1);
  }
  return d;
}
function renderBand4(A){
  const names=['凌晨 0-6','上午 6-12','下午 12-18','晚间 18-24'];
  const tot=A.band.reduce((a,b)=>a+b,0)||1;
  $('#band4').innerHTML=A.band.map((v,i)=>'<div class="b4"><div class="b4k">'+names[i]+'</div><div class="b4v">'+(v/tot*100).toFixed(1)+'%</div></div>').join('');
}

let selModel=null,LASTA=null;
function topModels(A){
  const idx=D.models.map((m,i)=>i).filter(i=>A.mTot[i]>0).sort((a,b)=>A.mTot[b]-A.mTot[a]);
  return {top:idx.slice(0,8)};
}
// 按当前窗口用量排名取色：用量越大越靠前，使用 MC 中的蓝色系
function rankColor(A){
  const order=D.models.map((m,i)=>i).sort((a,b)=>(A.mTot[b]||0)-(A.mTot[a]||0));
  const map={};order.forEach((mi,r)=>map[mi]=MC[r%MC.length]);return map;
}
let hrSel=null;
function hourData(){
  const c=cut(),hiC=cutHi();
  const vals=Array.from({length:24},()=>[]),hrTot=new Array(24).fill(0),dom=new Array(24).fill(-1);
  const mH=Array.from({length:24},()=>({})),mC=Array.from({length:24},()=>({}));
  let gmax=0;
  for(const s of D.sess)for(const r of s.r){const ts=r[0]*60000;if(ts<c||ts>=hiC)continue;const h=hm(r[0]).getHours();vals[h].push(r[2]);hrTot[h]+=r[2];if(r[2]>gmax)gmax=r[2];const mm=mH[h];mm[r[1]]=(mm[r[1]]||0)+r[2];const cc=mC[h];cc[r[1]]=(cc[r[1]]||0)+1;}
  for(let h=0;h<24;h++){let bm=-1,bv=-1;for(const k in mH[h])if(mH[h][k]>bv){bv=mH[h][k];bm=+k;}dom[h]=bm;}
  return {vals,hrTot,dom,gmax,mH,mC};
}
function renderRidge(A){
  const svg=$('#rdSvg');
  const {mH}=hourData();
  const W=svg.clientWidth||800,H=112,pL=44,pR=8,pT=8,pB=18,plotH=H-pT-pB,base=pT+plotH;
  svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('height',H);
  const pitch=(W-pL-pR)/24;
  const mh={};
  for(let h=0;h<24;h++)for(const k in mH[h]){const mi=+k;(mh[mi]=mh[mi]||new Array(24).fill(0))[h]=mH[h][k];}
  let vmax=0,vmin=Infinity;
  for(const mi in mh)for(const v of mh[mi])if(v>0){if(v>vmax)vmax=v;if(v<vmin)vmin=v;}
  if(!vmax){svg.innerHTML='<text x="'+pL+'" y="40" font-size="12" fill="'+CJ.muted+'">该时间范围内无数据</text>';$('#rdLegend').innerHTML='';return;}
  const lo=Math.log10(vmin),hi=Math.log10(vmax),span=Math.max(1e-9,hi-lo);
  const yv=v=>pT+plotH*(1-(Math.log10(v)-lo)/span);
  let yt='';
  for(let k=Math.ceil(lo);k<=Math.floor(hi);k++){const y=yv(Math.pow(10,k)).toFixed(1);yt+='<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+CJ.grid+'" stroke-width="0.5"/><text x="'+(pL-5)+'" y="'+(+y+3).toFixed(1)+'" font-size="8.5" fill="'+CJ.axis2+'" text-anchor="end">'+fmt(Math.pow(10,k))+'</text>';}
  const mis=Object.keys(mh).map(Number).sort((a,b)=>{const ta=mh[a].reduce((x,y)=>x+y,0),tb=mh[b].reduce((x,y)=>x+y,0);return ta-tb;});
  const RC=rankColor(A);
  let defs='',curves='';
  for(const mi of mis){
    const a=mh[mi];
    const dim=hrSel!==null&&hrSel!==mi;
    const col=dim?CJ.dim:RC[mi];
    const runs=[];let cur=[];
    for(let h=0;h<24;h++){if(a[h])cur.push([pL+pitch*(h+0.5),yv(a[h])]);else{if(cur.length)runs.push(cur);cur=[];}}
    if(cur.length)runs.push(cur);
    const tt=a.reduce((x,y)=>x+y,0);
    let pk=0;for(let h=1;h<24;h++)if(a[h]>a[pk])pk=h;
    const tip=D.models[mi]+'\n窗口合计 '+fmt(tt)+' token\n时段峰值 '+pk+' 时 · '+fmt(a[pk]);
    defs+='<linearGradient id="rg'+mi+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="'+col+'" stop-opacity="0.30"/><stop offset="100%" stop-color="'+col+'" stop-opacity="0.02"/></linearGradient>';
    let body='',hitD='';
    for(const run of runs){
      // 两端补零轴点，使山脊平滑落回基线
      let pts;
      if(run.length===1){
        const x=run[0][0];
        pts=[[Math.max(pL,x-pitch*0.5),base],[x,run[0][1]],[Math.min(W-pR,x+pitch*0.5),base]];
      }else{
        pts=[[Math.max(pL,run[0][0]-pitch*0.6),base]].concat(run,[[Math.min(W-pR,run[run.length-1][0]+pitch*0.6),base]]);
      }
      const line=smoothLine(pts);
      hitD+=(hitD?' ':'')+line;
      const area=line+' L'+pts[pts.length-1][0].toFixed(1)+' '+base+' L'+pts[0][0].toFixed(1)+' '+base+' Z';
      body+='<path d="'+area+'" fill="url(#rg'+mi+')"/><path d="'+line+'" fill="none" stroke="'+col+'" stroke-width="0.8" stroke-opacity="0.95" pointer-events="none"/>';
    }
    const hit=hitD?'<path d="'+hitD+'" fill="none" stroke="rgba(0,0,0,0)" stroke-width="8" pointer-events="stroke" data-tip="'+tip+'"/>':'';
    curves+='<g opacity="'+(dim?0.22:1)+'">'+body+hit+'</g>';
  }
  let xt='';
  for(let h=0;h<24;h+=2)xt+='<text x="'+(pL+pitch*(h+0.5))+'" y="'+(H-6)+'" font-size="9" fill="'+CJ.axis+'" text-anchor="middle">'+h+'时</text>';
  const idx=D.models.map((m,i)=>i).filter(i=>A.mTot[i]>0).sort((a,b)=>A.mTot[b]-A.mTot[a]).slice(0,8);
  const RC2=rankColor(A);
  $('#rdLegend').innerHTML=idx.map(mi=>'<span data-hrm="'+mi+'" style="cursor:pointer;'+(hrSel!==null&&hrSel!==mi?'opacity:.4':'')+'"><i style="display:inline-block;width:8px;height:8px;border-radius:2px;background:'+RC2[mi]+';margin-right:4px"></i>'+D.models[mi]+'</span>').join('');
  svg.innerHTML='<defs>'+defs+'</defs>'+yt+curves+xt+'<line x1="'+pL+'" y1="'+base+'" x2="'+(W-pR)+'" y2="'+base+'" stroke="'+CJ.base+'"/>';
}
document.addEventListener('click',e=>{
  const hg=e.target.closest('[data-hrm]');
  if(hg){const v=+hg.dataset.hrm;hrSel=(hrSel===v?null:v);if(LASTA)renderRidge(LASTA);}
});
function renderTop5(A){
  const order=D.models.map((m,i)=>i).filter(i=>A.mTot[i]>0).sort((a,b)=>A.mTot[b]-A.mTot[a]).slice(0,10);
  const RC=rankColor(A);
  const card=mi=>'<div class="t5'+(selModel===mi?' on':'')+'" data-dm="'+mi+'"><div class="k"><i style="display:inline-block;border-radius:2px;background:'+RC[mi]+'"></i>'+esc(D.models[mi])+'</div><div class="v">'+fmt(A.mTot[mi])+'</div></div>';
  $('#top5').innerHTML=order.map(card).join('');
}
function renderDailyModel(A){
  LASTA=A;
  const svg=$('#dmSvg');const {top}=topModels(A);
  const dates=Object.keys(A.dm).sort();
  const mx=Math.max(...dates.map(d=>Object.values(A.dm[d]).reduce((a,b)=>a+b,0)),1);
  const W=svg.clientWidth||800,H=102,pL=44,pR=8,pT=8,pB=18;
  const n=dates.length||1;const pitch=(W-pL-pR)/n;const bw=Math.max(2,pitch*0.62);
  const segs=top.concat(selModel!==null&&selModel!==-1&&!top.includes(selModel)?[selModel]:[]);
  const RC=rankColor(A);
  const colorOf=mi=>mi===-1?CJ.other:RC[mi];
  let g='',bars='',xlabels='';
  for(let i=1;i<8;i++){const y=pT+(H-pT-pB)*i/8;g+='<line x1="'+pL+'" y1="'+y+'" x2="'+(W-pR)+'" y2="'+y+'" stroke="'+CJ.grid+'" stroke-width="0.5"/>';}
  dates.forEach((d,i)=>{
    const dtt=Object.values(A.dm[d]).reduce((a,b)=>a+b,0);
    let cum=0;
    segs.forEach(mi=>{
      const v=(A.dm[d][mi]||0);if(!v)return;
      const bh=v/mx*(H-pT-pB);
      const x=pL+pitch*i+(pitch-bw)/2,y=H-pB-cum-bh;
      const dimmed=selModel!==null&&selModel!==mi;
      bars+='<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+bw.toFixed(1)+'" height="'+Math.max(0.5,bh).toFixed(1)+'" fill="'+(dimmed?CJ.dimBar:colorOf(mi))+'" fill-opacity="'+(dimmed?'0.5':'0.85')+'"/>';
      cum+=bh;
    });
    const dayList=segs.map(mi=>({mi:mi,v:(A.dm[d][mi]||0)})).filter(o=>o.v>0).sort((a,b)=>b.v-a.v);
    const dayTip=d+' · 合计 '+nf(dtt)+'\n'+dayList.map(o=>'· '+(o.mi===-1?'其他':D.models[o.mi])+'  '+nf(o.v)+'（'+(dtt?(o.v/dtt*100).toFixed(1):0)+'%）').join('\n');
    bars+='<rect x="'+(pL+pitch*i).toFixed(1)+'" y="'+pT+'" width="'+pitch.toFixed(1)+'" height="'+(H-pT-pB)+'" fill="#fff" fill-opacity="0" pointer-events="all" data-tip="'+dayTip+'"/>';
    const step=Math.ceil(n/7);
    if(i%step===0||i===n-1)xlabels+='<text x="'+(pL+pitch*i+pitch/2)+'" y="'+(H-4)+'" font-size="9" fill="'+CJ.axis+'" text-anchor="middle">'+d.slice(5)+'</text>';
  });
  svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('height',H);
  svg.innerHTML=g+bars+xlabels+
    '<text x="'+(pL-5)+'" y="'+(pT+7)+'" font-size="9" fill="'+CJ.axis+'" text-anchor="end">'+fmt(mx)+'</text>'+
    '<text x="'+(pL-5)+'" y="'+(H-pB)+'" font-size="9" fill="'+CJ.axis+'" text-anchor="end">0</text>';
}
document.addEventListener('click',e=>{
  const lg=e.target.closest('[data-dm]');
  if(lg){const v=+lg.dataset.dm;selModel=(selModel===v?null:v);if(LASTA){renderDailyModel(LASTA);renderTop5(LASTA);}}
});

function renderModelDist(){
  const svg=$('#mdSvg');const W=svg.clientWidth||300;
  const c=cut(),hiC=cutHi();
  const bym={};
  for(const s of D.sess)for(const r of s.r){const ts=r[0]*60000;if(ts<c||ts>=hiC)continue;(bym[r[1]]=bym[r[1]]||[]).push(r[2]);}
  const rows=Object.keys(bym).map(Number).sort((a,b)=>bym[b].length-bym[a].length).slice(0,8);
  if(!rows.length){svg.setAttribute('viewBox','0 0 '+W+' 60');svg.setAttribute('height',60);svg.innerHTML='<text x="16" y="30" font-size="11" fill="'+CJ.muted+'">该时间范围内无数据</text>';$('#mdCap').textContent='';return;}
  const st=rows.map(m=>{const a=bym[m].slice().sort((x,y)=>x-y);const n=a.length;
    const p=q=>a[Math.min(n-1,Math.ceil(q*n)-1)];return{m,n,p50:p(.5),p90:p(.9)};});
  const all=[];rows.forEach(m=>all.push(...bym[m]));
  const sorted=all.slice().sort((a,b)=>a-b);
  const gpct=q=>sorted[Math.min(sorted.length-1,Math.ceil(q*sorted.length)-1)];
  const gp50=gpct(.5);
  const cap=Math.max(1e5,Math.ceil(gpct(.995)*1.06/5e4)*5e4);
  const nameW=Math.min(118,Math.max(...rows.map(m=>D.models[m].length))*6.1+6);
  const cntX=nameW+8;
  const x50=cntX+48,x90=x50+56,lw=x90+76;
  const barW=52;
  const X=v=>lw+(W-lw-6)*Math.min(1,v/cap);
  const pScale=Math.max(st.reduce((a,s)=>Math.max(a,s.p90),0),1);
  const rh=44,bh=24,pT=14;
  const axY=pT+st.length*rh+2;
  const K=36,cntM=st.map(()=>new Array(K).fill(0));
  for(let ri=0;ri<st.length;ri++)for(const v of bym[st[ri].m]){const i=Math.min(K-1,Math.floor(v/cap*K));cntM[ri][i]++;}
  const RC=LASTA?rankColor(LASTA):null;
  let out='<line x1="'+X(gp50).toFixed(1)+'" y1="'+(pT-6)+'" x2="'+X(gp50).toFixed(1)+'" y2="'+axY+'" stroke="'+CJ.dash+'" stroke-width="0.8" stroke-dasharray="3 2"/>'+
    '<text x="'+(X(gp50)+3).toFixed(1)+'" y="'+(pT-1)+'" font-size="9" fill="'+CJ.axis+'">全局 50% '+fmt(gp50)+'</text>'+
    '<text x="0" y="'+(pT-1)+'" font-size="13" fill="'+CJ.axis+'">模型</text>'+
    '<text x="'+cntX+'" y="'+(pT-1)+'" font-size="13" fill="'+CJ.axis+'">次数</text>'+
    '<text x="'+x50+'" y="'+(pT-1)+'" font-size="13" fill="'+CJ.axis+'">50%</text>'+
    '<text x="'+x90+'" y="'+(pT-1)+'" font-size="13" fill="'+CJ.axis+'">90%</text>';
  st.forEach((s,ri)=>{
    const col=RC?RC[s.m]:modelColor(s.m);
    const baseY=pT+ri*rh+24;
    const raw=cntM[ri],sm=raw.map((v,i)=>((raw[i-1]||0)+2*v+(raw[i+1]||0))/4);
    const mx=Math.max(...sm,1e-9);
    let d='M'+lw.toFixed(1)+' '+baseY;
    for(let i=0;i<K;i++){const x=lw+(W-lw-6)*(i+0.5)/K;d+=' L'+x.toFixed(1)+' '+(baseY-sm[i]/mx*bh).toFixed(1);}
    d+=' L'+(W-6).toFixed(1)+' '+baseY+' Z';
    const tip=esc(D.models[s.m])+'&#10;'+nf(s.n)+' 次 · 50% '+fmt(s.p50)+' · 90% '+fmt(s.p90);
    out+='<path d="'+d+'" fill="'+col+'" fill-opacity="0.3" stroke="'+col+'" stroke-width="0.8" data-tip="'+tip+'"/>'+
      '<line x1="'+X(s.p50).toFixed(1)+'" y1="'+(baseY-bh-1)+'" x2="'+X(s.p50).toFixed(1)+'" y2="'+baseY+'" stroke="'+col+'" stroke-width="1.6" data-tip="'+tip+'"/>'+
      '<circle cx="'+X(s.p90).toFixed(1)+'" cy="'+(baseY-4)+'" r="2" fill="'+CJ.axis+'" data-tip="'+tip+'"/>'+
      '<text x="0" y="'+(baseY-5.5)+'" font-size="11" fill="'+CJ.t1+'">'+esc(D.models[s.m])+'</text>'+
      '<text x="'+cntX+'" y="'+(baseY-5.5)+'" font-size="11" fill="'+CJ.t1+'">'+nf(s.n)+'</text>'+
      '<text x="'+x50+'" y="'+(baseY-9)+'" font-size="12" fill="'+CJ.t1+'">'+fmt(s.p50)+'</text>'+
      '<rect x="'+x50+'" y="'+(baseY-5)+'" width="'+barW+'" height="5" fill="'+CJ.dimBar+'" fill-opacity="0.7" data-tip="'+tip+'"/>'+
      '<rect x="'+x50+'" y="'+(baseY-5)+'" width="'+Math.max(1.2,s.p50/pScale*barW).toFixed(1)+'" height="5" fill="'+col+'" fill-opacity="0.9" data-tip="'+tip+'"/>'+
      '<text x="'+x90+'" y="'+(baseY-9)+'" font-size="12" fill="'+CJ.t1+'">'+fmt(s.p90)+'</text>'+
      '<rect x="'+x90+'" y="'+(baseY-5)+'" width="'+barW+'" height="5" fill="'+CJ.dimBar+'" fill-opacity="0.7" data-tip="'+tip+'"/>'+
      '<rect x="'+x90+'" y="'+(baseY-5)+'" width="'+Math.max(1.2,s.p90/pScale*barW).toFixed(1)+'" height="5" fill="'+col+'" fill-opacity="0.45" data-tip="'+tip+'"/>';
  });
  let tks='<line x1="'+lw+'" y1="'+axY+'" x2="'+(W-6)+'" y2="'+axY+'" stroke="'+CJ.base+'"/>';
  const stepT=1e5;
  for(let v=0;v<=cap;v+=stepT){const x=X(v);
    tks+='<line x1="'+x.toFixed(1)+'" y1="'+axY+'" x2="'+x.toFixed(1)+'" y2="'+(axY+2)+'" stroke="'+CJ.axis+'"/>'+
      '<text x="'+x.toFixed(1)+'" y="'+(axY+12)+'" font-size="8" fill="'+CJ.axis+'" text-anchor="middle">'+(v===0?'0':(v/1e4)+'万')+'</text>';}
  const H=axY+16;
  svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('height',H);
  svg.innerHTML=out+tks;
  $('#mdCap').textContent='TOP '+st.length+' 模型 · 共 '+nf(all.length)+' 次';
}
function renderCrumb(){
  const c=$('#crumb');
  if(state.ws===null){c.style.display='none';return;}
  const wn=D.ws[state.ws];
  let path,back;
  if(state.ses!==null){back='<button class="chip on" data-a="up">← 返回</button>';path='<a data-a="root">'+esc(wn)+'</a><span class="sep">›</span><span class="cur">'+esc(state.ses.t)+'</span>';}
  else{back='<button class="chip on" data-a="root">← 返回列表</button>';path='<span class="cur">'+esc(wn)+'</span>';}
  c.innerHTML=back+'<span class="path">'+path+'</span>';
  c.style.display='flex';
}
document.addEventListener('click',e=>{
  const a=e.target.closest('[data-a]');
  if(!a)return;
  if(a.dataset.a==='root'){state={ws:null,ses:null};render();}
  else if(a.dataset.a==='up'&&state.ws!==null){state={ws:state.ws,ses:null};render();}
});

function renderPanel(A){
  const p=$('#panel');
  const tools=$('#wsTools');
  const c=cut(),hiC=cutHi();
  const WHD={},dset={};SHD=new Map();
  for(const s of D.sess){
    let sm=null;
    for(const r of s.r){const ts=r[0]*60000;if(ts<c||ts>=hiC)continue;const d=hm(r[0]);const dk=fdate(d);
      dset[dk]=1;
      const w=s.w;(WHD[w]=WHD[w]||{});(WHD[w][dk]=WHD[w][dk]||new Array(24).fill(0));WHD[w][dk][d.getHours()]+=r[2];
      if(!sm){sm={};SHD.set(s,sm);}
      (sm[dk]=sm[dk]||new Array(24).fill(0));sm[dk][d.getHours()]+=r[2];}
  }
  PCTX={whd:WHD,days:Object.keys(dset).sort(),avgD:A.tot/(Object.keys(A.daily).length||1)};
  if(state.ws===null){
    tools.style.display='flex';
    if(wsMode==='ws'){
      $('#wsSorts').style.display='flex';
      let arr=A.wsArr.filter(x=>!q||x.n.toLowerCase().includes(q));
      if(wsSort==='tt')arr.sort((a,b)=>b.tt-a.tt);else arr.sort((a,b)=>b.n-a.n);
      if(!arr.length){p.innerHTML='<div class="empty">无匹配的工作空间</div>';return;}
      p.innerHTML='<div class="subgrid">'+arr.map(x=>'<div class="wcard" data-ws="'+x.i+'"><div class="wc-nm" title="'+esc(x.n)+'">'+esc(x.n)+'</div><div class="wc-tt">'+fmt(x.tt)+'</div><div class="wc-meta">'+(x.tt/A.tot*100).toFixed(1)+'% · '+x.cnt+' 会话</div><div class="wviz">'+buildViz(x.i)+'</div></div>').join('')+'</div>';
      return;
    }
    $('#wsSorts').style.display='none';
    let arr=A.sessAgg.filter(x=>!q||(x.s.t||'').toLowerCase().includes(q)).sort((a,b)=>b.tt-a.tt);
    if(!arr.length){p.innerHTML='<div class="empty">无匹配的会话</div>';return;}
    p.innerHTML='<div class="subgrid">'+arr.map(x=>{const i=D.sess.indexOf(x.s);const t=esc(x.s.t||'');
      return '<div class="wcard" data-si="'+i+'"><div class="wc-nm" title="'+t+'">'+t+'</div><div class="wc-tt">'+fmt(x.tt)+'</div><div class="wc-meta">'+(x.tt/A.tot*100).toFixed(1)+'% · '+x.tn+' 轮</div><div class="wviz">'+vizCells(SHD.get(x.s))+'</div></div>';}).join('');
  }else if(state.ses===null){
    tools.style.display='none';
    const arr=A.sessAgg.filter(x=>x.s.w===state.ws).sort((a,b)=>b.tt-a.tt);
    const wsTot=arr.reduce((a,x)=>a+x.tt,0);
    if(!arr.length){p.innerHTML='<div class="empty">该窗口内此工作空间无数据</div>';return;}
    p.innerHTML=arr.map(x=>{const i=D.sess.indexOf(x.s);const t=esc(x.s.t||'');
      return '<div class="wcard" data-ses="'+i+'"><div class="wc-nm" title="'+t+'">'+t+'</div><div class="wc-tt">'+fmt(x.tt)+'</div><div class="wc-meta">'+(wsTot?(x.tt/wsTot*100).toFixed(1):0)+'% · '+x.tn+' 轮</div><div class="wviz">'+vizCells(SHD.get(x.s))+'</div></div>';}).join('');
  }else{
    tools.style.display='none';
    const s=state.ses;const c=cut(),hiC=cutHi();
    const reqs=s.r.filter(r=>{const ts=r[0]*60000;return ts>=c&&ts<hiC;});
    const tt=reqs.reduce((a,r)=>a+r[2],0);
    const t0=reqs[0][0],t1=reqs[reqs.length-1][0];
    p.innerHTML='<div class="view-h"><div><div class="view-t">'+esc(s.t)+'</div><div class="view-m">'+fdate(hm(t0))+' '+ftime(hm(t0))+' → '+fdate(hm(t1))+' '+ftime(hm(t1))+' · '+reqs.length+' 次请求 · 合计 '+fmt(tt)+'</div></div><div><button class="chip on" data-v="sc">散点</button> <button class="chip" data-v="tb">明细表</button></div></div><div id="reqView"></div>';
    drawScatter(reqs);
    p.querySelector('[data-v="sc"]').onclick=()=>{drawScatter(reqs);setV('sc');};
    p.querySelector('[data-v="tb"]').onclick=()=>{drawTable(reqs);setV('tb');};
    function setV(v){p.querySelectorAll('[data-v]').forEach(b=>b.classList.toggle('on',b.dataset.v===v));}
  }
}
function drawScatter(reqs){
  const el=$('#reqView');const RC=LASTA?rankColor(LASTA):null;const mcol=i=>RC?RC[i]:modelColor(i);
  const W=el.clientWidth||560,H=240,pL=42,pR=12,pT=12,pB=26;
  const t0=reqs[0][0],t1=reqs[reqs.length-1][0],span=Math.max(1,t1-t0);
  const mx=Math.max(...reqs.map(r=>r[2]),1);
  const x=v=>pL+(W-pL-pR)*(v-t0)/span;
  const y=v=>pT+(H-pT-pB)*(1-Math.log(v+1)/Math.log(mx+1));
  let g='';for(let i=0;i<4;i++){const yy=pT+(H-pT-pB)*i/3;g+='<line x1="'+pL+'" y1="'+yy+'" x2="'+(W-pR)+'" y2="'+yy+'" stroke="'+CJ.grid+'"/>';}
  const d0=hm(t0);
  let pts='';
  for(const r of reqs){
    const cx=x(r[0]),cy=y(r[2]);
    const d=hm(r[0]);
    const tip=fdate(d)+' '+ftime(d)+':'+String(d.getSeconds()).padStart(2,'0')+'\n'+D.models[r[1]]+'\n输入 '+nf(r[3])+' · 输出 '+nf(r[4])+'\n缓存 '+nf(r[5])+' · 合计 '+nf(r[2]);
    pts+='<circle cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="3.2" fill="'+mcol(r[1])+'" fill-opacity=".82" data-tip="'+tip.replace(/\n/g,'&#10;')+'"/>';
  }
  const lbl='<text x="'+pL+'" y="'+(pT+8)+'" font-size="9" fill="'+CJ.axis+'">'+fmt(mx)+'</text><text x="'+pL+'" y="'+(H-pB)+'" font-size="9" fill="'+CJ.axis+'">1</text>';
  const xm='max';
  el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" width="100%"><g>'+g+pts+lbl+
    '<text x="'+pL+'" y="'+(H-6)+'" font-size="9" fill="'+CJ.axis+'">'+fdate(d0)+' '+ftime(d0)+'</text>'+
    '<text x="'+(W-pR)+'" y="'+(H-6)+'" font-size="9" fill="'+CJ.axis+'" text-anchor="end">'+fdate(hm(t1))+' '+ftime(hm(t1))+'</text>'+
    '<text x="'+(pL-8)+'" y="'+(H/2)+'" font-size="9" fill="'+CJ.axis+'" text-anchor="end" transform="rotate(-90 '+(pL-8)+' '+(H/2)+')">token（对数轴）</text></g></svg>'+
    '<div style="margin-top:6px;font-size:10px;color:var(--t3)">x=时间 · y=单次请求 token（对数） · 颜色=模型：<span style="color:#9a978f">'+modelLegend(reqs)+'</span></div>';
}
function modelLegend(reqs){
  const set={};reqs.forEach(r=>set[r[1]]=1);
  const RC=LASTA?rankColor(LASTA):null;return Object.keys(set).map(i=>'<span style="color:'+(RC?RC[i]:modelColor(i))+'">●</span>'+D.models[i]).join('　');
}
function drawTable(reqs){
  const el=$('#reqView');
  let rows='';
  for(const r of reqs.slice().reverse()){
    const d=hm(r[0]);
    const tip='输入 '+nf(r[3])+' · 输出 '+nf(r[4])+' · 缓存 '+nf(r[5])+' · 合计 '+nf(r[2]);
    rows+='<tr data-tip="'+tip+'"><td>'+fdate(d)+' '+ftime(d)+'</td><td style="text-align:left">'+D.models[r[1]]+'</td><td>'+nf(r[3])+'</td><td>'+nf(r[4])+'</td><td>'+nf(r[5])+'</td><td>'+nf(r[2])+'</td></tr>';
  }
  el.innerHTML='<div class="rt-wrap"><table class="rt"><thead><tr><th>时间</th><th style="text-align:left">模型</th><th>输入</th><th>输出</th><th>缓存命中</th><th>合计</th></tr></thead><tbody>'+rows+'</tbody></table></div>';
}

document.addEventListener('click',e=>{
  const r=e.target.closest('[data-ws]');
  if(r){state={ws:+r.dataset.ws,ses:null};render();return;}
  const s0=e.target.closest('[data-si]');
  if(s0){const ss=D.sess[+s0.dataset.si];state={ws:ss.w,ses:ss};render();return;}
  const s2=e.target.closest('[data-ses]');
  if(s2){state.ses=D.sess[+s2.dataset.ses];render();return;}
  const p2=e.target.closest('.pill');
  if(p2){win=+p2.dataset.w;customStart=0;customEnd=0;$('#dStart').value='';$('#dEnd').value='';selModel=null;hrSel=null;document.querySelectorAll('.pill').forEach(b=>b.classList.toggle('on',b===p2));render();return;}
  const mch=e.target.closest('[data-m]');
  if(mch){wsMode=mch.dataset.m;document.querySelectorAll('#wsTools [data-m]').forEach(b=>b.classList.toggle('on',b===mch));renderPanel(aggregate());return;}
  const ch2=e.target.closest('#wsSorts .chip');
  if(ch2){wsSort=ch2.dataset.s;document.querySelectorAll('#wsSorts .chip').forEach(b=>b.classList.toggle('on',b===ch2));renderPanel(aggregate());}
});
$('#wsSearch').addEventListener('input',e=>{q=e.target.value.trim().toLowerCase();renderPanel(aggregate());});
$('#wsMult').addEventListener('change',e=>{wsMult=+e.target.value;renderPanel(aggregate());});
function applyCustom(){
  const sv=$('#dStart').value,ev=$('#dEnd').value;
  if(!sv&&!ev)return;
  customStart=sv?new Date(sv+'T00:00:00').getTime()/60000:0;
  customEnd=ev?(new Date(ev+'T00:00:00').getTime()+86400e3)/60000:0;
  win=0;selModel=null;
  document.querySelectorAll('.pill').forEach(b=>b.classList.remove('on'));
  render();
}
$('#dStart').addEventListener('change',applyCustom);
$('#dEnd').addEventListener('change',applyCustom);

function renderEmpty(){
  document.querySelector('.wrap').style.display='none';
  $('#kpis').innerHTML='';
  const el=document.createElement('div');
  el.style.cssText='max-width:520px;margin:60px auto;text-align:center;padding:40px 30px;background:var(--card);border:1px solid var(--border);border-radius:14px';
  el.innerHTML='<div style="font-size:44px;margin-bottom:12px">📭</div>'
    +'<div style="font-size:17px;font-weight:600;margin-bottom:8px">还没有可统计的使用数据</div>'
    +'<div style="font-size:13px;color:var(--t2);line-height:1.9;text-align:left;margin:16px 0">'
    +'看板数据来自本机会话日志（<code>~/.workbuddy/projects/**/*.jsonl</code>）。首次使用请：<br>'
    +'1. 正常使用 WorkBuddy 跑几个任务（产生对话与工具调用）<br>'
    +'2. 重新生成看板（对本 skill 说"看看我的 token 用量"）<br>'
    +'3. 数据积累越久，趋势与续航推算越准</div>'
    +'<div style="font-size:11px;color:var(--t3)">检测时间 '+esc(D.gen)+' · 本页不包含任何数据，不会外发</div>';
  document.body.appendChild(el);
}
function render(){
  const A=aggregate();LASTA=A;
  renderKpis(A);renderHeat(A);renderBand4(A);renderCrumb();renderPanel(A);
  renderRidge(A);renderTop5(A);renderDailyModel(A);
  renderModelDist();renderTopCred(A);
  const ps=document.querySelector('.panel-scroll'),hc=document.querySelector('#heatCard');
  if(ps&&hc){const card=ps.closest('.card');
    const delta=card.getBoundingClientRect().height-ps.getBoundingClientRect().height;
    ps.style.maxHeight=Math.max(180,hc.getBoundingClientRect().height-delta)+'px';}
}
if(!D.sess.length){renderEmpty();}
else{render();
new ResizeObserver(()=>{clearTimeout(window._rzT);window._rzT=setTimeout(render,120)}).observe(document.querySelector('.wrap'));}
const themeBtn=$('#themeBtn');themeBtn.textContent=DARK?'浅色':'深色';if(DARK)document.body.classList.add('dark');
themeBtn.onclick=()=>{DARK=!DARK;CJ=DARK?CJD:CJL;MC=DARK?MCD:MCL;RAMP=DARK?RMPD:RMPL;try{localStorage.setItem('td-dark',DARK?'1':'0');}catch(e){}document.body.classList.toggle('dark',DARK);themeBtn.textContent=DARK?'浅色':'深色';render();};
</script>
</body></html>'''

html = TEMPLATE.replace('__DATA__', js).replace('__MULT__', mult_json).replace('__MULT_SRC__', MULT_SRC)
open(OUT, 'w', encoding='utf-8').write(html)
print('written:', OUT, f'({os.path.getsize(OUT)/1e6:.1f} MB)')
print('sessions:', len(sess_list), '| models:', len(models), '| workspaces:', len(ws_names))
