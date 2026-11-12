#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""术语表自动构建：用「官方中文」对齐反推译名。

原理：绝大多数游戏都有官方简中（HOI4/Stellaris/EU4 都自带），把英文和官方中文按 key
对齐，就是一份人工校对过的平行语料。从里面挖出来的译名，比任何模型猜的都准。

  python align.py paradox --game "<游戏目录>" --src english --dst simp_chinese \
      --out glossary.json --sample 400

流程：对齐 → 抽样喂给千问抽「英文专名 = 官方译名」→ 按出现次数排序合并。
"""
import argparse
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from l10n import call_qwen, log  # noqa: E402

LINE_RE = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*:\s*(\d+)?\s*"(.*)"\s*$')


def read_paradox(path):
    out = {}
    with open(path, encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            m = LINE_RE.match(line.rstrip('\n'))
            if m:
                out[m.group(1)] = m.group(3)
    return out


def align_paradox(game, src_lang, dst_lang, limit_files=0):
    loc = os.path.join(game, 'localisation')
    if not os.path.isdir(loc):
        raise RuntimeError('没有 localisation 目录')
    sdir, ddir = os.path.join(loc, src_lang), os.path.join(loc, dst_lang)
    if not os.path.isdir(ddir):
        raise RuntimeError('没有 %s 目录（这个游戏没官方中文，只能走在线搜索模式）' % dst_lang)
    files = sorted(f for f in os.listdir(sdir) if f.endswith('.yml'))
    if limit_files:
        files = files[:limit_files]
    pairs = []
    for fn in files:
        a = read_paradox(os.path.join(sdir, fn))
        b_name = fn.replace('_l_' + src_lang, '_l_' + dst_lang)
        b_path = os.path.join(ddir, b_name)
        if not os.path.isfile(b_path):
            continue
        b = read_paradox(b_path)
        for k, en in a.items():
            zh = b.get(k)
            if not zh or zh == en:
                continue
            if len(en) > 120 or len(zh) > 120:
                continue
            pairs.append((en, zh))
    return pairs


def ask(prompt, retries=2):
    """直接问一段文本（call_qwen 需要 k/v 结构，这里要自由文本）。"""
    import urllib.request
    from l10n import api_key
    key = api_key()
    if not key:
        raise RuntimeError('没有千问 key')
    payload = {'model': 'qwen-flash', 'stream': True, 'temperature': 0.1,
               'messages': [{'role': 'user', 'content': prompt}]}
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(
                'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                data=json.dumps(payload).encode('utf-8'),
                headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
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
                        d = ch.get('delta') or {}
                        if d.get('content'):
                            buf += d['content']
            return buf
        except Exception as e:
            last = e
    raise RuntimeError('请求失败: %s' % last)


EXTRACT_PROMPT = (
    '下面是从游戏英文原文与官方简体中文译文里对齐出来的句对。\n'
    '请从中抽取「专有名词 / 系统术语」的译名对照，只抽那些在中文里也保持一致的词：\n'
    '- 人名、地名、国家、组织、舰船/装备型号、游戏机制名称\n'
    '- 不要抽普通动词、形容词、代词\n'
    '- 英文侧保留原文大小写；中文侧用官方译法\n'
    '只输出 JSON 数组，元素形如 {"en":"Poland","zh":"波兰"}，不要解释。\n\n'
)


def mine(pairs, batch=30, rounds=6):
    found = {}
    for r in range(rounds):
        chunk = random.sample(pairs, min(batch, len(pairs)))
        lines = '\n'.join('%d. %s ||| %s' % (i + 1, en, zh) for i, (en, zh) in enumerate(chunk))
        try:
            raw = ask(EXTRACT_PROMPT + lines)
        except Exception as e:
            log('第 %d 轮失败: %s' % (r + 1, e))
            continue
        # call_qwen 期望 {"k","v"} 结构，这里退回直接解析模型原文
        for m in re.finditer(r'\{"en"\s*:\s*"(.*?)"\s*,\s*"zh"\s*:\s*"(.*?)"\}', raw):
            en, zh = m.group(1), m.group(2)
            if en and zh and en != zh:
                found[en] = found.get(en) or {'zh': zh, 'hits': 0}
                found[en]['hits'] += 1
        log('第 %d 轮：累计 %d 条' % (r + 1, len(found)))
    return found


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['paradox'])
    p.add_argument('--game', required=True)
    p.add_argument('--src', default='english')
    p.add_argument('--dst', default='simp_chinese')
    p.add_argument('--out', default='glossary-auto.json')
    p.add_argument('--files', type=int, default=0)
    p.add_argument('--sample', type=int, default=400)
    p.add_argument('--rounds', type=int, default=6)
    a = p.parse_args()

    pairs = align_paradox(a.game, a.src, a.dst, a.files)
    log('对齐到 %d 句对' % len(pairs))
    if not pairs:
        return
    if a.sample:
        pairs = random.sample(pairs, min(a.sample, len(pairs)))

    found = mine(pairs, rounds=a.rounds)
    out = {'_comment': '自动构建：英文 = 官方中文，来源 %s / %s' % (a.src, a.dst),
           '_pairs_sampled': len(pairs)}
    for en, v in sorted(found.items(), key=lambda kv: -kv[1]['hits']):
        if v['hits'] >= 1:
            out[en] = v['zh']
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log('写入 %s：%d 条' % (a.out, len(found)))


if __name__ == '__main__':
    main()
