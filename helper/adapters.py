# -*- coding: utf-8 -*-
"""适配器注册表。

一个适配器 = 一个引擎的「提取」和「注入」两个函数。翻译层不关心它们怎么实现。

新增引擎的步骤：
  1. 在本文件里写一个类，实现 extract() / pack()
  2. 用 @register('名字') 挂上去
  3. 在 CHANGELOG/文档表格里加一行

接口约定见 docs/SCHEMA.md。
"""
import json
import os
import re
import time

REGISTRY = {}


def register(name):
    def deco(cls):
        REGISTRY[name] = cls()
        return cls
    return deco


def get(name):
    if name not in REGISTRY:
        raise KeyError('没有这个适配器: %s（可用: %s）' % (name, ', '.join(sorted(REGISTRY))))
    return REGISTRY[name]


def list_adapters():
    return sorted(REGISTRY)


class Adapter:
    name = 'base'

    def extract(self, game_dir, opts):
        raise NotImplementedError

    def pack(self, game_dir, entries, opts):
        raise NotImplementedError

    # ---- 公用小工具 ----
    @staticmethod
    def write_jsonl(path, entries):
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')

    @staticmethod
    def read_jsonl(path):
        out = []
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    @staticmethod
    def backup(path):
        if os.path.isfile(path):
            bak = '%s.bak.%s' % (path, time.strftime('%Y%m%d-%H%M%S'))
            with open(path, 'rb') as src, open(bak, 'wb') as dst:
                dst.write(src.read())
            return bak
        return None


@register('paradox')
class Paradox(Adapter):
    """Clausewitz 系（HOI4 / Stellaris / EU4 / CK3）：localisation/<lang>/*.yml"""
    name = 'paradox'
    LINE_RE = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*:\s*(\d+)?\s*"(.*)"\s*$')

    def _loc_dir(self, game_dir):
        p = os.path.join(game_dir, 'localisation')
        return p if os.path.isdir(p) else None

    def extract(self, game_dir, opts):
        loc = self._loc_dir(game_dir)
        if not loc:
            raise RuntimeError('没找到 localisation 目录')
        src_lang = opts.get('src_lang') or 'english'
        src_dir = os.path.join(loc, src_lang)
        if not os.path.isdir(src_dir):
            raise RuntimeError('没有 %s 子目录' % src_lang)

        # 补全模式：只保留目标语言里还没有的 key
        missing = None
        only = opts.get('only_missing')
        if only:
            dst_dir = os.path.join(loc, only)
            if os.path.isdir(dst_dir):
                missing = {}
                for fn in os.listdir(dst_dir):
                    if not fn.endswith('.yml'):
                        continue
                    base = fn.replace('_l_' + only + '.yml', '_l_' + (opts.get('src_lang') or 'english') + '.yml')
                    have = missing.setdefault(base, set())
                    try:
                        with open(os.path.join(dst_dir, fn), encoding='utf-8-sig', errors='ignore') as f:
                            for line in f:
                                m = self.LINE_RE.match(line.rstrip('\n'))
                                if m:
                                    have.add(m.group(1))
                    except Exception:
                        continue
                print('[only-missing] %s 已有 %d 个文件' % (only, len(missing)), file=__import__('sys').stderr)

        limit_files = int(opts.get('files') or 0)
        files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith('.yml'))
        if limit_files:
            files = files[:limit_files]
        skipped = 0
        for fn in files:
            path = os.path.join(src_dir, fn)
            have = (missing or {}).get(fn)
            with open(path, encoding='utf-8-sig', errors='ignore') as f:
                for i, line in enumerate(f):
                    m = self.LINE_RE.match(line.rstrip('\n'))
                    if not m:
                        continue
                    key, _, text = m.group(1), m.group(2), m.group(3)
                    if missing is not None and have and key in have:
                        skipped += 1
                        continue
                    if not text.strip() or len(text) > 400:
                        continue
                    if re.fullmatch(r'[\d\s.,%+\-/]*', text):
                        continue
                    yield {
                        'id': 'paradox:%s:%s' % (fn, key),
                        'src': text,
                        'dst': '',
                        'file': os.path.relpath(path, game_dir).replace('\\', '/'),
                        'loc': 'row:%d' % (i + 1),
                        'kind': 'ui',
                        'context': '',
                        'status': 'new',
                        'note': key,
                    }

    def pack(self, game_dir, entries, opts):
        lang = opts.get('lang') or 'simp_chinese'
        mod_name = opts.get('mod') or 'whale_zh'
        mod_root = opts.get('mod_dir')
        if not mod_root:
            home = os.path.expanduser('~')
            game_name = opts.get('game_name') or os.path.basename(game_dir.rstrip('\\/'))
            mod_root = os.path.join(home, 'Documents', 'Paradox Interactive', game_name, 'mod')
        out_dir = os.path.join(mod_root, mod_name, 'localisation', lang)
        os.makedirs(out_dir, exist_ok=True)

        by_file = {}
        for e in entries:
            if e.get('status') == 'skip' or not e.get('dst'):
                continue
            fn = os.path.basename(e['file'])
            by_file.setdefault(fn, []).append(e)

        written = 0
        for fn, rows in by_file.items():
            out_name = fn.replace('_l_english', '_l_' + lang)
            lines = ['\ufeffl_%s:' % lang]
            for e in rows:
                key = (e.get('note') or '').strip()
                if not key:
                    continue
                lines.append(' %s:0 "%s"' % (key, e['dst'].replace('"', "'")))
            path = os.path.join(out_dir, out_name)
            self.backup(path)
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            written += 1

        # 写 .mod 描述文件，启动器才认这个外挂 mod
        mod_path = os.path.join(mod_root, mod_name, mod_name + '.mod')
        try:
            with open(mod_path, 'w', encoding='utf-8-sig') as f:
                f.write('name="%s"\n' % opts.get('mod_title', mod_name))
                f.write('path="mod/%s"\n' % mod_name)
                f.write('supported_version="%s"\n' % opts.get('game_version', '1.*'))
                if opts.get('tags'):
                    f.write('tags={%s}\n' % ' '.join('"%s"' % t for t in opts['tags']))
        except Exception as e:
            print('[warn] 写 .mod 失败: %s' % e, file=__import__('sys').stderr)

        return {'ok': True, 'mod_dir': os.path.join(mod_root, mod_name), 'mod_file': mod_path,
                'files_written': written, 'entries': sum(len(v) for v in by_file.values())}


@register('generic-json')
class GenericJson(Adapter):
    """游戏自带外挂语言包（如 Limbus 的 LimbusCompany_Data/Lang/<lang>/*.json）。

    opts:
      lang_dir  语言包根目录（相对游戏根），默认 LimbusCompany_Data/Lang
      src_lang  源语言目录名，默认 en
      fields    要翻的字段名，默认 ['dialog','teller','desc','name']
      files     最多处理多少个文件
    """
    name = 'generic-json'

    def _lang_root(self, game_dir, opts):
        rel = opts.get('lang_dir') or 'LimbusCompany_Data/Lang'
        return os.path.join(game_dir, rel)

    def extract(self, game_dir, opts):
        root = self._lang_root(game_dir, opts)
        src_lang = opts.get('src_lang') or 'en'
        src_dir = os.path.join(root, src_lang)
        fields = opts.get('fields') or ['dialog', 'teller', 'desc', 'name']
        if not os.path.isdir(src_dir):
            raise RuntimeError('没有源语言目录: %s' % src_dir)
        files = sorted(f for f in os.listdir(src_dir) if f.endswith('.json'))
        limit = int(opts.get('files') or 0)
        if limit:
            files = files[:limit]
        for fn in files:
            path = os.path.join(src_dir, fn)
            try:
                with open(path, encoding='utf-8-sig') as f:
                    data = json.load(f)
            except Exception as e:
                print('[skip] %s: %s' % (fn, e), file=__import__('sys').stderr)
                continue
            if not isinstance(data, dict) or 'dataList' not in data:
                continue
            for idx, row in enumerate(data['dataList']):
                if not isinstance(row, dict):
                    continue
                for fld in fields:
                    val = row.get(fld)
                    if not isinstance(val, str) or not val.strip():
                        continue
                    if re.fullmatch(r'[\d\s.,%+\-/]*', val):
                        continue
                    yield {
                        'id': 'generic-json:%s:%d:%s' % (fn, idx, fld),
                        'src': val,
                        'dst': '',
                        'file': os.path.relpath(path, game_dir).replace('\\', '/'),
                        'loc': 'json:%d.%s' % (idx, fld),
                        'kind': 'dialog' if fld in ('dialog', 'teller') else 'ui',
                        'context': fn,
                        'status': 'new',
                        'note': '%d|%s' % (idx, fld),
                    }

    def pack(self, game_dir, entries, opts):
        root = self._lang_root(game_dir, opts)
        lang = opts.get('lang') or 'whale_zh'
        src_lang = opts.get('src_lang') or 'en'
        out_dir = os.path.join(root, lang)
        os.makedirs(out_dir, exist_ok=True)

        by_file = {}
        for e in entries:
            if e.get('status') == 'skip' or not e.get('dst'):
                continue
            fn = os.path.basename(e['file'])
            by_file.setdefault(fn, []).append(e)

        written = 0
        for fn, rows in by_file.items():
            src_path = os.path.join(root, src_lang, fn)
            try:
                data = json.load(open(src_path, encoding='utf-8-sig'))
            except Exception:
                continue
            patch = {}
            for e in rows:
                note = e.get('note') or ''
                if '|' not in note:
                    continue
                idx_s, fld = note.split('|', 1)
                # note 可能被后续步骤追加过备注（如「name | 术语未对齐」），只取第一段
                fld = fld.split('|')[0].strip().split()[0] if fld.strip() else ''
                if not fld:
                    continue
                patch[(int(idx_s), fld)] = e['dst']
            for (idx, fld), dst in patch.items():
                try:
                    data['dataList'][idx][fld] = dst
                except Exception:
                    pass
            out_path = os.path.join(out_dir, fn)
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            written += 1

        # 写 config.json 指向新语言包
        cfg_path = os.path.join(root, 'config.json')
        if opts.get('switch_lang'):
            self.backup(cfg_path)
            cfg = {}
            try:
                cfg = json.load(open(cfg_path, encoding='utf-8'))
            except Exception:
                pass
            cfg['lang'] = lang
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

        return {'ok': True, 'lang_dir': out_dir, 'files_written': written, 'entries': sum(len(v) for v in by_file.values())}
