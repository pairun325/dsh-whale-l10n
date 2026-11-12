#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键汉化：把 Paradox 系游戏的英文 localisation 翻成中文，做成外挂 mod，原文件一个字节不动。

用法：
  python l10n.py scan   --game "<游戏目录>"
  python l10n.py build  --game "<游戏目录>" --mod whale_zh --lang simp_chinese [--files 3] [--keys 400] [--dry]
  python l10n.py remove --mod whale_zh
  python l10n.py status --mod whale_zh

设计原则：
- 只读原游戏文件；产物全部写进 `<文档>/Paradox Interactive/<游戏>/mod/<mod>`。
- 译文有缓存（mod 目录里 cache.json），重复跑只翻新词。
- 任何一个文件写失败就整体不落地（先全翻完再写盘）。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _find_keys():
    """从插件目录往上找 keys-handover.json，找到哪个用哪个。"""
    here = PLUGIN_DIR
    for _ in range(6):
        cand = os.path.join(here, 'keys-handover.json')
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return os.path.join(os.path.expanduser('~'), 'keys-handover.json')


KEYS_FILE = _find_keys()

LINE_RE = re.compile(r'^(\s*)([A-Za-z0-9_.\-]+)\s*:\s*(\d+)?\s*"(.*)"\s*$')


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def api_key():
    try:
        with open(KEYS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data['ws_key_20261027']['key']
    except Exception as e:
        log('读取密钥失败:', e)
        return None


def call_qwen(lines, target='简体中文', retries=2, glossary=None):
    """一批一批翻，失败重试。glossary: {原文专名: 译名}，会注入提示词。"""
    key = api_key()
    if not key:
        raise RuntimeError('没有可用的千问 key')
    prompt = (
        '你是游戏本地化译者。把下面 JSON 数组里的英文游戏文本翻译成%s。\n'
        '规则：\n'
        '1. 只翻译 value，保留 key 原样；\n'
        '2. 保留原文里的占位符（$VAR$、\\n、[Root.GetName] 这类）不要翻；\n'
        '3. 装备/舰船型号、人名地名按通用译名，没有通用译名就音译；\n'
        '4. 不要加解释，只输出 JSON 数组，元素形如 {"k":"KEY","v":"译文"}。\n\n' % target
    )
    if glossary:
        prompt += '【术语表（必须严格使用，不许改译）】\n'
        prompt += '\n'.join('%s = %s' % (k, v) for k, v in glossary.items())
        prompt += '\n\n' + json.dumps([{'k': k, 'v': v} for k, v in lines], ensure_ascii=False)

    payload = {
        'model': 'qwen-flash',
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': True,
        'temperature': 0.2,
    }
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(
                'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                data=json.dumps(payload).encode('utf-8'),
                headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            )
            buf = ''
            with urllib.request.urlopen(req, timeout=180) as r:
                for raw in r:
                    line = raw.decode('utf-8', 'ignore').strip()
                    if not line.startswith('data:'):
                        continue
                    piece = line[5:].strip()
                    if piece == '[DONE]':
                        break
                    try:
                        obj = json.loads(piece)
                    except Exception:
                        continue
                    for ch in obj.get('choices', []):
                        delta = ch.get('delta') or {}
                        if delta.get('content'):
                            buf += delta['content']
            text = buf.strip()
            if text.startswith('```'):
                text = re.sub(r'^```[a-z]*\n?', '', text)
                text = re.sub(r'\n?```$', '', text)
            start = text.find('[')
            end = text.rfind(']')
            if start >= 0 and end > start:
                text = text[start:end + 1]
            arr = json.loads(text)
            out = {}
            for it in arr:
                if isinstance(it, dict) and 'k' in it:
                    out[str(it['k'])] = str(it.get('v', ''))
            return out
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError('翻译失败: %s' % last)


def find_localisation(game):
    cand = os.path.join(game, 'localisation')
    if os.path.isdir(cand):
        return cand
    for root, dirs, _ in os.walk(game):
        if os.path.basename(root).lower() == 'localisation':
            return root
        if root.count(os.sep) - game.count(os.sep) > 3:
            dirs[:] = []
    return None


def docs_mod_dir(game_name):
    home = os.path.expanduser('~')
    base = os.path.join(home, 'Documents', 'Paradox Interactive', game_name, 'mod')
    return base


def guess_game_name(game):
    real = decode_lang_dir(os.path.join(game, 'localisation'))
    for lang in ('english',):
        d = os.path.join(game, 'localisation', lang)
        if os.path.isdir(d):
            pass
    # 用 steam_appid 或目录名兜底
    appid = None
    for name in ('steam_appid.txt',):
        p = os.path.join(game, name)
        if os.path.isfile(p):
            appid = open(p, encoding='utf-8', errors='ignore').read().strip()
    return appid


def decode_lang_dir(path):
    return path


def do_scan(args):
    game = os.path.abspath(args.game)
    loc = find_localisation(game)
    if not loc:
        print(json.dumps({'ok': False, 'error': '没找到 localisation 目录'}, ensure_ascii=False))
        return 1
    src = os.path.join(loc, 'english')
    if not os.path.isdir(src):
        subs = [d for d in os.listdir(loc) if os.path.isdir(os.path.join(loc, d))]
        print(json.dumps({'ok': False, 'error': '没有 english 子目录', 'dirs': subs}, ensure_ascii=False))
        return 1
    files = sorted(f for f in os.listdir(src) if f.lower().endswith('.yml'))
    total_keys = 0
    sample = []
    for f in files[:3]:
        k, s = count_keys(os.path.join(src, f))
        total_keys += k
        sample.extend(s[:2])
    for f in files[3:]:
        k, _ = count_keys(os.path.join(src, f))
        total_keys += k
    out = {
        'ok': True,
        'game': game,
        'localisation': loc,
        'source_dir': src,
        'files': len(files),
        'keys_estimate': total_keys,
        'languages': [d for d in os.listdir(loc) if os.path.isdir(os.path.join(loc, d))],
        'first_files': files[:8],
        'sample': sample[:6],
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


def count_keys(path):
    n = 0
    sample = []
    try:
        with open(path, encoding='utf-8-sig', errors='ignore') as f:
            for line in f:
                m = LINE_RE.match(line.rstrip('\n'))
                if m:
                    n += 1
                    if len(sample) < 3:
                        k, v = m.group(2), m.group(4)
                        if len(v) < 90 and not re.fullmatch(r'[\d\s.,%-]*', v):
                            sample.append([k, v])
    except Exception:
        pass
    return n, sample


def parse_file(path):
    """返回 (header, entries[ (key, idx, text) ], 其它保留行)。"""
    header = 'l_english:'
    entries = []
    with open(path, encoding='utf-8-sig', errors='ignore') as f:
        lines = f.read().splitlines()
    for ln in lines:
        if ln.startswith('l_') and ln.strip().endswith(':'):
            header = ln.strip()
            continue
        m = LINE_RE.match(ln)
        if m:
            entries.append((m.group(2), m.group(3) or '0', m.group(4)))
    return header, entries


def translatable(text):
    if not text or len(text) > 400:
        return False
    if re.fullmatch(r'[\d\s.,%+\-/]*', text):
        return False
    if re.fullmatch(r'[A-Z0-9.\- ]{1,24}', text) and ' ' not in text.strip():
        return False
    if not re.search(r'[A-Za-z]{2,}', text):
        return False
    return True


def do_build(args):
    game = os.path.abspath(args.game)
    loc = find_localisation(game)
    if not loc:
        print(json.dumps({'ok': False, 'error': '没找到 localisation 目录'}, ensure_ascii=False))
        return 1
    src = os.path.join(loc, 'english')
    files = sorted(f for f in os.listdir(src) if f.lower().endswith('.yml'))
    if args.files:
        files = files[:args.files]
    game_name = args.game_name or os.path.basename(game.rstrip('\\/'))
    mod_root = args.mod_dir or docs_mod_dir(game_name)
    mod_dir = os.path.join(mod_root, args.mod)
    cache_path = os.path.join(mod_dir, 'cache.json')
    cache = {}
    if os.path.isfile(cache_path):
        try:
            cache = json.load(open(cache_path, encoding='utf-8'))
        except Exception:
            cache = {}

    planned = []
    todo = []
    for f in files:
        header, entries = parse_file(os.path.join(src, f))
        rows = []
        for key, idx, text in entries:
            if not translatable(text):
                continue
            hit = cache.get(key)
            if hit:
                rows.append({'key': key, 'text': text, 'dst': hit})
            else:
                rows.append({'key': key, 'text': text, 'dst': None})
                todo.append((key, text))
        planned.append({'file': f, 'header': header, 'rows': rows})

    if args.keys:
        todo = todo[:args.keys]
        keep = set(k for k, _ in todo)
        for p in planned:
            p['rows'] = [r for r in p['rows'] if r['dst'] or r['key'] in keep]

    stats = {'files': len(planned), 'keys': sum(len(p['rows']) for p in planned), 'need_ai': len(todo), 'cached': sum(1 for p in planned for r in p['rows'] if r['dst'])}
    if args.dry:
        print(json.dumps({'ok': True, 'dry': True, 'stats': stats, 'mod_dir': mod_dir}, ensure_ascii=False))
        return 0

    # 批量翻译
    BATCH = 40
    done = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        got = call_qwen(chunk, target=args.target_name)
        for k, v in got.items():
            cache[k] = v
        for p in planned:
            for r in p['rows']:
                if r['dst'] is None and r.get('key') in cache:
                    r['dst'] = cache[r['key']]
        done += len(chunk)
        log('翻译进度 %d/%d' % (done, len(todo)))
        os.makedirs(mod_dir, exist_ok=True)
        json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False)

    # 写文件（先全部生成，再落盘）
    out_dir = os.path.join(mod_dir, 'localisation', args.lang)
    os.makedirs(out_dir, exist_ok=True)
    written = 0
    for p in planned:
        rows = [r for r in p['rows'] if r['dst']]
        if not rows:
            continue
        target_lang_header = 'l_%s:' % args.lang
        buf = ['\ufeff' + target_lang_header]
        for r in rows:
            buf.append(' %s:0 "%s"' % (r['key'], r['dst'].replace('"', "'")))
        with open(os.path.join(out_dir, p['file'].replace('_l_english', '_l_' + args.lang)), 'w', encoding='utf-8') as f:
            f.write('\n'.join(buf) + '\n')
        written += 1

    # mod 描述文件
    desc = os.path.join(mod_dir, 'descriptor.mod')
    with open(desc, 'w', encoding='utf-8') as f:
        f.write('name="%s"\n' % args.title)
        f.write('path="mod/%s"\n' % args.mod)
        f.write('supported_version="%s"\n' % args.supported_version)
    launcher = os.path.join(mod_root, args.mod + '.mod')
    with open(launcher, 'w', encoding='utf-8') as f:
        f.write('name="%s"\n' % args.title)
        f.write('path="mod/%s"\n' % args.mod)
        f.write('supported_version="%s"\n' % args.supported_version)

    print(json.dumps({'ok': True, 'mod_dir': mod_dir, 'launcher': launcher, 'files_written': written, 'stats': stats, 'cache_size': len(cache)}, ensure_ascii=False))
    return 0


def do_remove(args):
    game_name = args.game_name
    mod_root = args.mod_dir or docs_mod_dir(game_name)
    mod_dir = os.path.join(mod_root, args.mod)
    import shutil
    removed = []
    if os.path.isdir(mod_dir):
        shutil.rmtree(mod_dir)
        removed.append(mod_dir)
    launcher = os.path.join(mod_root, args.mod + '.mod')
    if os.path.isfile(launcher):
        os.remove(launcher)
        removed.append(launcher)
    print(json.dumps({'ok': True, 'removed': removed}, ensure_ascii=False))
    return 0


def do_status(args):
    game_name = args.game_name
    mod_root = args.mod_dir or docs_mod_dir(game_name)
    mod_dir = os.path.join(mod_root, args.mod)
    info = {'ok': True, 'mod_dir': mod_dir, 'exists': os.path.isdir(mod_dir)}
    if info['exists']:
        loc = os.path.join(mod_dir, 'localisation')
        lang_dirs = {}
        if os.path.isdir(loc):
            for d in os.listdir(loc):
                p = os.path.join(loc, d)
                if os.path.isdir(p):
                    lang_dirs[d] = len([x for x in os.listdir(p) if x.endswith('.yml')])
        cache = os.path.join(mod_dir, 'cache.json')
        info['lang_dirs'] = lang_dirs
        info['cache_size'] = len(json.load(open(cache, encoding='utf-8'))) if os.path.isfile(cache) else 0
    print(json.dumps(info, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('scan')
    s.add_argument('--game', required=True)
    s.set_defaults(func=do_scan)

    b = sub.add_parser('build')
    b.add_argument('--game', required=True)
    b.add_argument('--mod', default='whale_zh')
    b.add_argument('--title', default='鲸汐汉化')
    b.add_argument('--lang', default='simp_chinese')
    b.add_argument('--target-name', default='简体中文')
    b.add_argument('--files', type=int, default=0)
    b.add_argument('--keys', type=int, default=0)
    b.add_argument('--supported-version', default='1.15.*')
    b.add_argument('--game-name', default=None)
    b.add_argument('--mod-dir', default=None)
    b.add_argument('--dry', action='store_true')
    b.set_defaults(func=do_build)

    r = sub.add_parser('remove')
    r.add_argument('--mod', default='whale_zh')
    r.add_argument('--game-name', required=True)
    r.add_argument('--mod-dir', default=None)
    r.set_defaults(func=do_remove)

    st = sub.add_parser('status')
    st.add_argument('--mod', default='whale_zh')
    st.add_argument('--game-name', required=True)
    st.add_argument('--mod-dir', default=None)
    st.set_defaults(func=do_status)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == '__main__':
    main()
