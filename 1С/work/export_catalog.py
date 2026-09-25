"""
Экспорт каталога 1С -> data/catalog.json для сайта СТРОЙМАРКЕТ.

Источник: OData Catalog_Номенклатура (только GET, только чтение).
Сфера: строго папка 00.ИНСТРУМЕНТ И ЭЛЕКТРОТОВАРЫ + все вложенные папки (BFS).
Соседние папки верхнего уровня НЕ запрашиваются и НЕ включаются.

Креды читаются из 1С/.env (никогда не хардкодятся).
Запуск: python 1С/work/export_catalog.py
"""
import json
import os
import sys
from datetime import datetime
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))          # 1С/work
ROOT_1C = os.path.dirname(HERE)                            # 1С
SITE_ROOT = os.path.dirname(ROOT_1C)                       # корень сайта
TEMP_DIR = os.path.join(ROOT_1C, 'temp')
DATA_DIR = os.path.join(SITE_ROOT, 'data')

ROOT_FOLDER_NAME = '00.ИНСТРУМЕНТ И ЭЛЕКТРОТОВАРЫ'
ENTITY = 'Catalog_Номенклатура'
FIELDS = [
    'Ref_Key', 'Code', 'Description', 'НаименованиеПолное', 'Артикул',
    'IsFolder', 'Parent_Key', 'ВидНоменклатуры_Key', 'ТипНоменклатуры',
    'ЕдиницаИзмерения_Key', 'СтавкаНДС_Key', 'Производитель_Key',
    'КодПоставщика', 'МИНМАКС',
]


def load_env(path):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env


def get_json(session, url, tries=3):
    last = None
    for _ in range(tries):
        try:
            r = session.get(url, timeout=180)
            if r.status_code == 200:
                return r.json()
            last = f'HTTP {r.status_code}: {r.text[:200]}'
        except Exception as e:  # noqa: BLE001
            last = str(e)
    raise RuntimeError(f'Запрос не удался: {url} -> {last}')


def main():
    env = load_env(os.path.join(ROOT_1C, '.env'))
    base = env.get('ONEC_BASE_URL', '').rstrip('/') + '/odata/standard.odata/'
    auth = (env.get('ONEC_LOGIN', ''), env.get('ONEC_PASSWORD', ''))
    if not base.startswith('http') or not auth[0]:
        print('Нет кредов в 1С/.env (ONEC_BASE_URL / ONEC_LOGIN / ONEC_PASSWORD)')
        return 1

    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    session = requests.Session()
    session.auth = auth
    # Сервер по умолчанию отдаёт Atom XML — просим JSON.
    # Принимаются только 'application/json' и 'application/json;odata=minimal'.
    session.headers.update({'Accept': 'application/json'})

    # ВАЖНО: сервер 1С игнорирует $skip и не отдаёт nextLink.
    # Рабочий способ: запрос БЕЗ $top возвращает все строки одним ответом.
    # Кодируем URL целиком, сохраняя служебные символы OData ($ = , ( ) и т.д.).
    raw = f'{ENTITY}?$select=' + ','.join(FIELDS)
    url = base + quote(raw, safe="%/:?&=$,()'@+*;")
    print(f'Загрузка {ENTITY} ...', flush=True)
    payload = get_json(session, url)
    rows = payload.get('value', [])
    print(f'  получено строк: {len(rows)}')

    raw_path = os.path.join(TEMP_DIR, 'nomenklatura_raw.json')
    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False)
    print(f'  сырой снимок: {raw_path}')

    by_key = {r.get('Ref_Key'): r for r in rows if r.get('Ref_Key')}

    # --- ищем корень сферы (папка с точным именем, верхний уровень) ---
    root = None
    for r in rows:
        if not r.get('IsFolder'):
            continue
        if (r.get('Description') or '').strip() != ROOT_FOLDER_NAME:
            continue
        if not r.get('Parent_Key'):
            root = r
            break
    if root is None:
        for r in rows:
            if r.get('IsFolder') and (r.get('Description') or '').strip() == ROOT_FOLDER_NAME:
                root = r
                break
    if root is None:
        print(f'Корневая папка «{ROOT_FOLDER_NAME}» не найдена в справочнике!')
        return 2
    print(f'  корень: {root["Description"]} ({root["Ref_Key"]})')

    # --- BFS по вложенным папкам: наружу (к соседям) не выходим ---
    children = {}
    for r in rows:
        p = r.get('Parent_Key')
        if p:
            children.setdefault(p, []).append(r)

    allowed = {root['Ref_Key']}
    queue = [root['Ref_Key']]
    while queue:
        key = queue.pop(0)
        for ch in children.get(key, []):
            ck = ch.get('Ref_Key')
            if ch.get('IsFolder') and ck and ck not in allowed:
                allowed.add(ck)
                queue.append(ck)
    # --- строим дерево каталога ---
    folders = [k for k in allowed if k in by_key]
    node = {k: {'title': (by_key[k].get('Description') or '').strip(), 'folders': [], 'items': []}
            for k in folders}

    for r in rows:
        p = r.get('Parent_Key')
        ck = r.get('Ref_Key')
        if not p or p not in node or not ck:
            continue
        if r.get('IsFolder'):
            if ck in node:
                node[p]['folders'].append(ck)
        else:
            node[p]['items'].append({
                'key': ck,
                'title': (r.get('Description') or '').strip(),
                'full': (r.get('НаименованиеПолное') or '').strip(),
                'art': (r.get('Артикул') or '').strip(),
                'code': (r.get('Code') or '').strip(),
                'type': (r.get('ТипНоменклатуры') or '').strip(),
                'supplier_code': (r.get('КодПоставщика') or '').strip(),
            })

    def sort_tree(key):
        n = node[key]
        n['folders'].sort(key=lambda k: node[k]['title'].lower())
        n['items'].sort(key=lambda x: (x['title'].lower(), x['art']))
        for k in n['folders']:
            sort_tree(k)

    for k in folders:
        sort_tree(k)

    def public(key):
        n = node[key]
        return {
            'title': n['title'],
            'folders': [public(k) for k in n['folders']],
            'items': n['items'],
        }

    out = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'source': f'1С OData {ENTITY}, папка «{ROOT_FOLDER_NAME}»',
        'root': public(root['Ref_Key']),
    }
    out_path = os.path.join(DATA_DIR, 'catalog.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    def count(n):
        return len(n['items']) + sum(count(c) for c in n['folders'])

    print(f'  папок в сфере: {len(folders)}')
    print(f'  товаров: {count(out["root"])}')
    print(f'Готово: {out_path} ({os.path.getsize(out_path) // 1024} КБ)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
