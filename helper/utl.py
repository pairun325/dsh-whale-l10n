#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UTL —— 通用文本表工具链（地基）。

  python utl.py extract   --adapter generic-json --game "<游戏目录>" --out t.utl.jsonl [--files 2]
  python utl.py translate --table t.utl.jsonl [--batch 40] [--limit 0]
  python utl.py pack      --adapter generic-json --game "<游戏目录>" --table t.utl.jsonl [--lang whale_zh] [--switch-lang]
  python utl.py stats     --table t.utl.jsonl
  python utl.py adapters

三层分离：提取器产出统一文本表 → 翻译层只改 dst → 注入器按表写回。
新增引擎只需要在 helper/adapters.py 里加一个类，这里一行都不用改。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import adapters  # noqa: E402
from l10n import call_qwen, log  # noqa: E402


def read_table(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_table(path, rows):
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def load_glossary(path=None):
    """术语表：{'分组': {'src': 'dst'}} 或直接 {'src': 'dst'}，拍平成 {src: dst}。"""
    cands = [path] if path else [
        os.path.join(HERE, 'glossary.json'),
        os.path.join(os.getcwd(), 'glossary.json'),
    ]
    out = {}
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:
            data = json.load(open(p, encoding='utf-8-sig'))
        except Exception as e:
            log('术语表读取失败 %s: %s' % (p, e))
            continue
        for k, v in data.items():
            if k.startswith('_'):
                continue
            if isinstance(v, dict):
                out.update({a: b for a, b in v.items() if not a.startswith('_')})
        log('术语表 %s：%d 条' % (p, len(out)))
        break
    return out


def check_glossary(src_text, dst_text, terms):
    """术语表校验：原文里出现的专有名词，译文里必须是对应译名。返回缺失列表。"""
    if not src_text or not dst_text or not terms:
        return []
    missing = []
    for src, dst in terms.items():
        if src in src_text and dst and dst not in dst_text:
            missing.append(src)
    return missing


def cmd_extract(args):
    ad = adapters.get(args.adapter)
    rows = list(ad.extract(args.game, vars(args)))
    if args.max_chars:
        n, acc = 0, []
        for r in rows:
            acc.append(r)
            n += len(r['src'])
            if n >= args.max_chars:
                break
        rows = acc
    write_table(args.out, rows)
    log('提取 %d 条 → %s' % (len(rows), args.out))


def cmd_translate(args):
    rows = read_table(args.table)
    terms = load_glossary(getattr(args, 'glossary', None))
    # 先按术语表把纯专名行就地修正，不需要再花钱问模型
    fixed = 0
    for r in rows:
        key = (r.get('src') or '').strip()
        if key in terms and r.get('dst') != terms[key]:
            r['dst'] = terms[key]
            r['status'] = 'translated'
            fixed += 1
    if fixed:
        log('术语表直接修正 %d 条' % fixed)
    todo = [r for r in rows if r.get('status') != 'skip' and not r.get('dst')]
    if args.max_rows:
        todo = todo[:args.max_rows]
    if not todo:
        if fixed:
            write_table(args.table, rows)
        log('没有待翻译条目')
        return

    # 本地缓存：同一句不重复花钱
    cache_path = args.cache or os.path.join(os.path.dirname(os.path.abspath(args.table)), 'utl-cache.json')
    cache = {}
    if os.path.isfile(cache_path):
        try:
            cache = json.load(open(cache_path, encoding='utf-8'))
        except Exception:
            cache = {}

    done = 0
    terms = load_glossary(getattr(args, 'glossary', None))
    for i in range(0, len(todo), args.batch):
        chunk = todo[i:i + args.batch]
        pending = []
        for r in chunk:
            hit = cache.get(r['src'])
            if hit:
                r['dst'] = hit
                r['status'] = 'translated'
                done += 1
            else:
                pending.append(r)
        if pending:
            pairs = [(r['id'], r['src']) for r in pending]
            out = call_qwen(pairs, target=args.target, glossary=terms)
            for r in pending:
                dst = out.get(r['id'], '')
                # 整条就是一个专名时，术语表说了算（模型常常原样返回）
                if r['src'].strip() in terms:
                    dst = terms[r['src'].strip()]
                if dst:
                    if args.limit and len(dst) > args.limit:
                        dst = dst[:args.limit]
                    r['dst'] = dst
                    miss = check_glossary(r['src'], dst, terms)
                    if miss:
                        r['status'] = 'review'
                        r['issue'] = '术语未对齐: ' + ','.join(miss)
                    else:
                        r['status'] = 'translated'
                    cache[r['src']] = dst
                    done += 1
                else:
                    r['status'] = 'review'
        log('进度 %d/%d' % (min(i + args.batch, len(todo)), len(todo)))
        write_table(args.table, rows)
        json.dump(cache, open(cache_path, 'w', encoding='utf-8'), ensure_ascii=False)

    write_table(args.table, rows)
    log('翻译完成 %d 条（缓存命中 %d）' % (done, sum(1 for r in rows if r.get('dst'))))


def cmd_pack(args):
    ad = adapters.get(args.adapter)
    rows = read_table(args.table)
    if args.limit:
        for r in rows:
            if r.get('dst') and len(r['dst']) > args.limit:
                r['dst'] = r['dst'][:args.limit]
    res = ad.pack(args.game, rows, vars(args))
    log(json.dumps(res, ensure_ascii=False))


def cmd_glossary(args):
    """从官方中文对齐里挖术语表，可选并进 glossary.json 的某个分组。"""
    import align
    pairs = align.align_paradox(args.game, args.src, args.dst, args.files)
    log('对齐到 %d 句对' % len(pairs))
    if not pairs:
        return
    if args.sample:
        import random
        pairs = random.sample(pairs, min(args.sample, len(pairs)))
    found = align.mine(pairs, rounds=args.rounds)
    terms = {en: v['zh'] for en, v in sorted(found.items(), key=lambda kv: -kv[1]['hits'])}
    if not terms:
        log('没挖到术语')
        return

    if args.merge:
        target = args.out or os.path.join(HERE, 'glossary.json')
        data = {}
        if os.path.isfile(target):
            try:
                data = json.load(open(target, encoding='utf-8-sig'))
            except Exception:
                data = {}
        group = args.merge
        data.setdefault(group, {})
        data[group].update(terms)
        data.setdefault('_comment', '术语表：src 里的写法照抄原文（大小写敏感），dst 是必须使用的译名。')
        with open(target, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log('并入 %s 分组「%s」：%d 条' % (target, group, len(terms)))
    else:
        out = args.out or 'glossary-auto.json'
        payload = {'_comment': '自动构建：英文 = 官方中文（%s / %s）' % (args.src, args.dst)}
        payload.update(terms)
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log('写入 %s：%d 条' % (out, len(terms)))


def cmd_stats(args):
    rows = read_table(args.table)
    total = len(rows)
    tr = sum(1 for r in rows if r.get('status') == 'translated')
    skip = sum(1 for r in rows if r.get('status') == 'skip')
    review = sum(1 for r in rows if r.get('status') == 'review')
    print('总条目 %d\n已翻译 %d\n跳过 %d\n待审 %d\n未处理 %d' % (total, tr, skip, review, total - tr - skip - review))


def main():
    p = argparse.ArgumentParser(prog='utl')
    sub = p.add_subparsers(dest='cmd', required=True)

    e = sub.add_parser('extract')
    e.add_argument('--adapter', required=True)
    e.add_argument('--game', required=True)
    e.add_argument('--out', required=True)
    e.add_argument('--files', type=int, default=0)
    e.add_argument('--max-chars', dest='max_chars', type=int, default=0)
    e.add_argument('--lang-dir', dest='lang_dir')
    e.add_argument('--src-lang', dest='src_lang')
    e.add_argument('--only-missing', dest='only_missing', help='只提取目标语言里还没有的 key')
    e.set_defaults(func=cmd_extract)

    t = sub.add_parser('translate')
    t.add_argument('--table', required=True)
    t.add_argument('--batch', type=int, default=40)
    t.add_argument('--target', default='简体中文')
    t.add_argument('--limit', type=int, default=0)
    t.add_argument('--max-rows', dest='max_rows', type=int, default=0)
    t.add_argument('--cache')
    t.add_argument('--glossary')
    t.set_defaults(func=cmd_translate)

    k = sub.add_parser('pack')
    k.add_argument('--adapter', required=True)
    k.add_argument('--game', required=True)
    k.add_argument('--table', required=True)
    k.add_argument('--lang')
    k.add_argument('--src-lang', dest='src_lang')
    k.add_argument('--lang-dir', dest='lang_dir')
    k.add_argument('--mod')
    k.add_argument('--mod-dir', dest='mod_dir')
    k.add_argument('--game-name', dest='game_name')
    k.add_argument('--switch-lang', dest='switch_lang', action='store_true')
    k.add_argument('--limit', type=int, default=0)
    k.set_defaults(func=cmd_pack)

    s = sub.add_parser('stats')
    s.add_argument('--table', required=True)
    s.set_defaults(func=cmd_stats)

    g = sub.add_parser('glossary')
    g.add_argument('--game', required=True)
    g.add_argument('--src', default='english')
    g.add_argument('--dst', default='simp_chinese')
    g.add_argument('--files', type=int, default=0)
    g.add_argument('--sample', type=int, default=400)
    g.add_argument('--rounds', type=int, default=6)
    g.add_argument('--out')
    g.add_argument('--merge', help='并进 glossary.json 的这个分组名，比如 "Hearts of Iron IV"')
    g.set_defaults(func=cmd_glossary)

    a = sub.add_parser('adapters')
    a.set_defaults(func=lambda _a: print('\n'.join(adapters.list_adapters())))

    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
