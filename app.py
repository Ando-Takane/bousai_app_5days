from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# ワークショップ課題：青森市の市区町村コードに変更する
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def save_shelters():
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]

SHELTER_REQUIREMENTS = {
    'elderly': '高齢者',
    'children': '子供連れ',
    'foreigners': '外国人',
    'care': '要介護者',
    'pets': 'ペット'
}

CONGESTION_OPTIONS = ('混雑', 'やや混雑', '空き')
CAPACITY_OPTIONS = (100, 200)
CONGESTION_ORDER = {'空き': 0, 'やや混雑': 1, '混雑': 2}
FACILITY_TYPE_OPTIONS = (
    '指定避難所', '一時避難所', '福祉避難所', '防災倉庫', '物資集積所', 'その他'
)
DISASTER_TYPE_OPTIONS = ('津波', '地震', '洪水')

def calculate_congestion(accepted_count, capacity):
    """受入れ人数と収容人数の割合から混雑状況を判定する"""
    try:
        accepted = max(0, float(accepted_count))
        total_capacity = float(capacity)
    except (TypeError, ValueError):
        return '空き'

    if total_capacity <= 0:
        return '空き'
    occupancy_rate = accepted / total_capacity
    if occupancy_rate <= 0.5:
        return '空き'
    if occupancy_rate < 0.8:
        return 'やや混雑'
    return '混雑'

def refresh_shelter_congestion(items):
    """受入れ人数を持つ避難所の混雑状況を最新の割合で更新する"""
    for shelter in items:
        if 'accepted_count' in shelter and 'capacity' in shelter:
            shelter['congestion'] = calculate_congestion(
                shelter.get('accepted_count'), shelter.get('capacity')
            )
    return items

def sort_shelters_by_congestion(items):
    """避難所を空き、やや混雑、混雑の順に並べる"""
    refresh_shelter_congestion(items)
    return sorted(
        items,
        key=lambda shelter: CONGESTION_ORDER.get(shelter.get('congestion'), 1)
    )

def shelter_supports(shelter, requirement):
    """避難所が検索条件に対応しているかを確認する"""
    supports = shelter.get('supports', [])
    if isinstance(supports, dict):
        return bool(supports.get(requirement))
    if isinstance(supports, list):
        return requirement in supports or SHELTER_REQUIREMENTS[requirement] in supports
    return bool(shelter.get(requirement, False))

CONCERN_TO_REQUIREMENT = {
    '高齢者（65歳以上）': 'elderly',
    '高齢者': 'elderly',
    '子供連れ': 'children',
    '障害者・要介護者': 'care',
    '要介護者': 'care',
    '外国人': 'foreigners',
    'ペット': 'pets'
}

def valid_coordinates(latitude, longitude):
    """緯度経度が数値として有効か確認する"""
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude

def haversine_distance(latitude, longitude, target_latitude, target_longitude):
    """2地点間の距離をkmで返す"""
    earth_radius = 6371.0
    lat1, lat2 = math.radians(latitude), math.radians(target_latitude)
    delta_lat = math.radians(target_latitude - latitude)
    delta_longitude = math.radians(target_longitude - longitude)
    value = (math.sin(delta_lat / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin(delta_longitude / 2) ** 2)
    return earth_radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

def search_shelters(name='', concerns=None, latitude=None, longitude=None):
    """避難所名、配慮事項、現在地に一致する避難所を返す"""
    name = (name or '').strip().casefold()
    requirements = [
        CONCERN_TO_REQUIREMENT.get(concern, concern)
        for concern in (concerns or [])
    ]
    current_coordinates = valid_coordinates(latitude, longitude)
    results = []
    for shelter in shelters:
        shelter_name = str(shelter.get('name', ''))
        if name and name not in shelter_name.casefold():
            continue
        if not all(shelter_supports(shelter, requirement) for requirement in requirements):
            continue
        result = dict(shelter)
        result['_distance_km'] = None
        if current_coordinates:
            shelter_coordinates = valid_coordinates(
                shelter.get('latitude'), shelter.get('longitude')
            )
            if shelter_coordinates:
                result['_distance_km'] = haversine_distance(
                    current_coordinates[0], current_coordinates[1],
                    shelter_coordinates[0], shelter_coordinates[1]
                )
        results.append(result)

    if current_coordinates:
        results.sort(key=lambda shelter: (
            shelter['_distance_km'] is None,
            shelter['_distance_km'] if shelter['_distance_km'] is not None else 0
        ))
    return results

@app.context_processor
def inject_shelter_helpers():
    return {'shelter_supports': shelter_supports}


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    template_data = {
        'requirement_labels': SHELTER_REQUIREMENTS,
        'facility_type_options': FACILITY_TYPE_OPTIONS,
        'disaster_type_options': DISASTER_TYPE_OPTIONS,
        'congestion_options': CONGESTION_OPTIONS,
        'capacity_options': CAPACITY_OPTIONS,
        'shelters': shelters
    }
    edit_id = request.args.get('edit_id', '').strip()
    edit_shelter = next(
        (shelter for shelter in shelters if str(shelter.get('id')) == edit_id),
        None
    ) if edit_id else None
    if edit_id and edit_shelter is None:
        template_data.update(error=True, message='編集対象の避難所が見つかりません。')
    if edit_shelter:
        template_data['edit_shelter'] = edit_shelter

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        name_roman = request.form.get('name_roman', '').strip()
        address = request.form.get('address', '').strip()
        if not name or not name_roman or not address:
            template_data.update(error=True, message='施設名、施設名（ローマ字）、施設住所を入力してください。')
            return render_template('shelter_register.html', **template_data)
        if request.form.get('confirm') != '1':
            template_data.update(error=True, message='内容を確認したことにチェックしてください。')
            return render_template('shelter_register.html', **template_data)

        selected_supports = [
            requirement for requirement in SHELTER_REQUIREMENTS
            if request.form.get(requirement) == '1'
        ]
        try:
            capacity = int(request.form.get('capacity', '100'))
        except ValueError:
            capacity = 100
        if capacity < 0:
            capacity = 100
        selected_disaster_types = request.form.getlist('disaster_type')
        selected_disaster_types = [
            disaster_type for disaster_type in selected_disaster_types
            if disaster_type in DISASTER_TYPE_OPTIONS
        ]
        if not selected_disaster_types:
            template_data.update(error=True, message='災害対応区分を1つ以上選択してください。')
            return render_template('shelter_register.html', **template_data)

        try:
            accepted_count = max(0, int(request.form.get('accepted_count', '0') or 0))
        except ValueError:
            accepted_count = 0
        try:
            stock_count = max(0, int(request.form.get('stock_count', '0') or 0))
        except ValueError:
            stock_count = 0

        shelter_data = {
            'name': name,
            'name_roman': name_roman,
            'facility_type': request.form.get('facility_type', '').strip(),
            'supports': selected_supports,
            'accepted_count': accepted_count,
            'check_time': request.form.get('check_time', '').strip(),
            'stock_count': stock_count,
            'address': address,
            'address_english': request.form.get('address_english', '').strip(),
            'disaster_type': selected_disaster_types,
            'congestion': calculate_congestion(accepted_count, capacity),
            'congestion_updated_at': get_japan_time(),
            'capacity': capacity
        }
        if edit_shelter:
            edit_shelter.update(shelter_data)
            success_message = f'「{name}」の情報を更新しました。'
        else:
            next_id = max((shelter.get('id', 0) for shelter in shelters), default=0) + 1
            shelters.append({'id': next_id, **shelter_data})
            success_message = f'「{name}」を登録しました。'
        save_shelters()
        template_data.update(success=True, message=success_message, registered_name=name)
        return render_template('shelter_register.html', **template_data)

    return render_template('shelter_register.html', **template_data)


@app.route('/shelter_delete/<int:shelter_id>', methods=['POST'])
@login_required
def shelter_delete(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is not None:
        shelters.remove(shelter)
        save_shelters()
    return redirect(url_for('shelter_register', deleted='1'))

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=sort_shelters_by_congestion(shelters),
        name='',
        location='',
        selected_concerns=[],
        requirement_labels=SHELTER_REQUIREMENTS,
        nearby=False
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    name = request.args.get('name', '').strip()
    location = request.args.get('location', '')
    selected_concerns = request.args.getlist('concern')
    selected_requirements = [
        requirement for requirement in SHELTER_REQUIREMENTS
        if request.args.get(requirement) == '1'
    ]
    selected_concerns.extend(selected_requirements)
    latitude = request.args.get('latitude', '')
    longitude = request.args.get('longitude', '')
    current_coordinates = valid_coordinates(latitude, longitude)
    results = search_shelters(
        name=name,
        concerns=selected_concerns,
        latitude=latitude,
        longitude=longitude
    )
    if location and not name:
        location_query = location.casefold()
        results = [
            shelter for shelter in results
            if location_query in ' '.join(
                str(shelter.get(field, ''))
                for field in ('name', 'location', 'district', 'address')
            ).casefold()
        ]
    if not current_coordinates:
        results = sort_shelters_by_congestion(results)
    return render_template(
        'search_results.html',
        results=results,
        name=name,
        location=location,
        selected_concerns=selected_concerns,
        requirement_labels=SHELTER_REQUIREMENTS,
        nearby=bool(current_coordinates)
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
