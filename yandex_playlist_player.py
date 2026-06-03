#!/usr/bin/env python3
"""
yandex_playlist_player.py — Управление Яндекс Музыкой из консоли.
Воспроизводит плейлисты через mpv без скачивания файлов.

Использование:
    python yandex_playlist_player.py --playlist "название"
    python yandex_playlist_player.py --liked
    python yandex_playlist_player.py --wave
    python yandex_playlist_player.py --wave "эпичное"
    python yandex_playlist_player.py --stop
"""

import sys
import os
import signal
import subprocess
import argparse
import time
import re
import threading
from typing import List, Optional, Callable

from dotenv import load_dotenv
from yandex_music import Client

# Загружаем переменные окружения из .env файла
load_dotenv()

# ──────────────────────────────────────────────
# ⚠️  Токен берётся из переменной окружения YANDEX_MUSIC_TOKEN
#     Создай файл .env с содержимым:
#     YANDEX_MUSIC_TOKEN=ваш_токен_здесь
# Инструкция: https://github.com/MarshalX/yandex-music-api/discussions/513
# ──────────────────────────────────────────────
TOKEN = os.getenv("YANDEX_MUSIC_TOKEN", "")

# Путь к mpv (Windows). На Linux/macOS обычно просто "mpv".
MPV_PATH = r"C:\Program Files\MPV Player\mpv.exe"

# ──────────────────────────────────────────────
# Глобальный флаг для выхода из воспроизведения
# ──────────────────────────────────────────────
_exit_requested = False

# Файл для межпроцессной коммуникации (команды от yandex_remote.py)
_COMMAND_FILE = ".command_queue"

# Глобальная переменная для хранения текущей команды
_current_command = None


def check_command_file() -> Optional[str]:
    """Проверяет файл команд и возвращает команду, если есть."""
    global _current_command
    if os.path.exists(_COMMAND_FILE):
        try:
            with open(_COMMAND_FILE, "r", encoding="utf-8") as f:
                cmd = f.read().strip()
            # Удаляем файл после прочтения
            os.remove(_COMMAND_FILE)
            _current_command = cmd
            print(f"\n📩 Получена команда: {cmd}")
            return cmd
        except Exception as e:
            print(f"⚠️  Ошибка чтения файла команд: {e}")
    return None


def start_keyboard_listener(on_exit: Callable):
    """Запускает фоновый поток, слушающий Ctrl+X для выхода."""
    def _listener():
        global _exit_requested
        if sys.platform == "win32":
            import msvcrt
            while not _exit_requested:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    # Ctrl+X = 24 (0x18)
                    if key == b'\x18':
                        _exit_requested = True
                        print("\n\n⏹ Выход...")
                        on_exit()
                        break
                time.sleep(0.1)
        else:
            import termios
            import tty
            import select
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                while not _exit_requested:
                    r, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if r:
                        key = sys.stdin.read(1)
                        # Ctrl+X = 24 = 0x18
                        if key == '\x18':
                            _exit_requested = True
                            print("\n\n⏹ Выход...")
                            on_exit()
                            break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    thr = threading.Thread(target=_listener, daemon=True)
    thr.start()
    return thr


def get_client() -> Client:
    """Инициализирует и возвращает клиент Яндекс Музыки."""
    if not TOKEN or TOKEN.strip() == "":
        print("❌ Ошибка: не указан TOKEN. Создай файл .env с переменной YANDEX_MUSIC_TOKEN.")
        sys.exit(1)
    try:
        client = Client(TOKEN).init()
        print(f"✓ Авторизация успешна. Пользователь: {client.me.account.first_name}")
        return client
    except Exception as e:
        print(f"❌ Ошибка авторизации: {e}")
        sys.exit(1)


def stop_playback():
    """Останавливает все запущенные процессы mpv."""
    stopped = 0
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq mpv.exe", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5
            )
            if "mpv.exe" in result.stdout:
                subprocess.run(["taskkill", "/F", "/IM", "mpv.exe"],
                               capture_output=True, timeout=5)
                stopped = 1
        else:
            result = subprocess.run(
                ["pgrep", "-x", "mpv"], capture_output=True, text=True, timeout=5
            )
            if result.stdout.strip():
                for pid in result.stdout.strip().splitlines():
                    os.kill(int(pid), signal.SIGTERM)
                    stopped += 1
    except Exception as e:
        print(f"⚠️  Ошибка при остановке плеера: {e}")
        return

    if stopped:
        print("⏹ Воспроизведение остановлено.")
    else:
        print("ℹ️  Активных процессов mpv не найдено.")


def get_track_url(track, client: Client) -> Optional[str]:
    """Получает прямую ссылку на трек для стриминга."""
    try:
        download_info = track.get_download_info()
        if not download_info:
            print(f"  ⚠️  Нет доступных форматов для трека '{track.title}'")
            return None

        # Выбираем лучший доступный кодек: предпочитаем AAC/MP3
        best = download_info[0]
        for info in download_info:
            codec_priority = {"aac": 3, "mp3": 2, "opus": 1}
            cur_codec = info.codec if hasattr(info, 'codec') else ''
            best_codec = best.codec if hasattr(best, 'codec') else ''
            if codec_priority.get(cur_codec, 0) > codec_priority.get(best_codec, 0):
                best = info
            elif cur_codec == best_codec and info.bitrate_in_kbps > best.bitrate_in_kbps:
                best = info

        direct_link = best.get_direct_link()
        if direct_link:
            return direct_link
        else:
            print(f"  ⚠️  Не удалось получить ссылку для трека '{track.title}'")
            return None
    except Exception as e:
        print(f"  ⚠️  Ошибка получения URL трека '{track.title}': {e}")
        return None


def play_track(url: str, track_title: str, artist_name: str):
    """Запускает mpv для воспроизведения трека по прямой ссылке."""
    print(f"  ▶ Сейчас играет: {artist_name} — {track_title}")
    try:
        subprocess.Popen(
            [MPV_PATH, "--no-terminal", "--quiet", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        print(f"  ❌ mpv не найден по пути: {MPV_PATH}")
        print("  Установи mpv или укажи правильный путь в MPV_PATH.")
        sys.exit(1)
    except Exception as e:
        print(f"  ❌ Ошибка запуска mpv: {e}")
        sys.exit(1)


def play_playlist_by_name(name: str):
    """Ищет публичный или личный плейлист по названию и запускает воспроизведение."""
    client = get_client()
    print(f"🔍 Поиск плейлиста: '{name}'...")

    try:
        search_result = client.search(name, type_="playlist")
    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
        sys.exit(1)

    playlist = None

    # Проверяем поиск публичных плейлистов
    if search_result and search_result.playlists:
        search_playlists = search_result.playlists
        if hasattr(search_playlists, 'results') and search_playlists.results:
            playlist = search_playlists.results[0]

    # Если не нашли в публичных — ищем среди своих
    if not playlist:
        print("  Публичных плейлистов не найдено. Ищу в вашей библиотеке...")
        try:
            users_playlists = client.users_playlists_list()
        except Exception:
            users_playlists = []

        target_playlist = None
        for pl in users_playlists:
            if name.lower() in pl.title.lower():
                target_playlist = pl
                break

        if not target_playlist:
            print(f"❌ Плейлист '{name}' не найден.")
            sys.exit(1)

        playlist = target_playlist

    # Загружаем полную информацию с треками через kind
    if hasattr(playlist, 'kind') and playlist.kind:
        if not playlist.tracks:
            playlist = client.users_playlists(playlist.kind)

    print(f"✓ Найден плейлист: {playlist.title} ({playlist.track_count} треков)")
    play_tracks(playlist, client)


def play_liked_tracks():
    """Запускает воспроизведение плейлиста 'Мне нравится'."""
    client = get_client()

    try:
        likes = client.users_likes_tracks()
        if not likes or not likes.tracks:
            print("  В плейлисте 'Мне нравится' нет треков.")
            return
        print(f"✓ Плейлист 'Мне нравится': {len(likes.tracks)} треков")
        play_tracks(likes.tracks, client)
    except Exception as e:
        print(f"❌ Ошибка загрузки плейлиста 'Мне нравится': {e}")
        sys.exit(1)


def play_tracks(tracks_source, client: Client):
    """Запускает последовательное воспроизведение треков из плейлиста (в перемешанном порядке)."""
    import random

    if hasattr(tracks_source, 'tracks'):
        tracks_data = tracks_source.tracks
    else:
        tracks_data = tracks_source

    # Перемешиваем треки для режима shuffle
    random.shuffle(tracks_data)

    total = len(tracks_data)
    print(f"\n{'='*60}")
    print(f"  Начинаю воспроизведение {total} треков (в перемешанном порядке)")
    print(f"{'='*60}\n")

    for idx, track_data in enumerate(tracks_data, 1):
        try:
            if hasattr(track_data, 'fetch_track'):
                track = track_data.fetch_track()
            else:
                track = track_data.track
        except Exception as e:
            print(f"  [{idx}/{total}] ⚠️  Не удалось загрузить трек: {e}")
            continue

        if not track:
            print(f"  [{idx}/{total}] ⚠️  Трек недоступен")
            continue

        title = getattr(track, 'title', 'Unknown')
        artists = track.artists if hasattr(track, 'artists') else []
        artist_str = ", ".join(a.name for a in artists) if artists else "Unknown"

        print(f"[{idx}/{total}] {artist_str} — {title}")
        print(f"  └ Загрузка информации...")

        track_url = get_track_url(track, client)
        if not track_url:
            continue

        stop_playback()
        time.sleep(0.3)

        play_track(track_url, title, artist_str)

        if idx < total:
            print(f"  └ Ожидание завершения трека (Ctrl+C — пропустить)...")
            cmd = _wait_for_track_or_command()
            if cmd:
                # Получена команда из файла — обрабатываем
                _handle_remote_command(cmd, client)
                if _exit_requested:
                    break
                continue
            try:
                pass  # Цикл уже завершился (трек доиграл)
            except KeyboardInterrupt:
                print("\n  ⏭ Пропускаю трек...")
                stop_playback()
                continue


def _wait_for_track_or_command() -> Optional[str]:
    """Ждёт завершения трека или появления команды. Возвращает команду или None."""
    while True:
        cmd = check_command_file()
        if cmd:
            return cmd
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq mpv.exe", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5
            )
            if "mpv.exe" not in result.stdout:
                return None
        else:
            result = subprocess.run(
                ["pgrep", "-x", "mpv"], capture_output=True, text=True, timeout=5
            )
            if not result.stdout.strip():
                return None
        time.sleep(0.5)


def _handle_remote_command(cmd: str, client: Client):
    """Обрабатывает команду от yandex_remote.py."""
    global _exit_requested
    
    print(f"\n📩 Обрабатываю команду: {cmd}")
    
    if cmd == "--stop":
        print("  └ Остановка и выход...")
        stop_playback()
        _exit_requested = True
        os._exit(0)
    
    elif cmd == "--silence":
        print("  └ Тишина (остановка воспроизведения)...")
        stop_playback()
        # Не выходим, продолжаем ждать команд
    
    elif cmd.startswith("--playlist"):
        # Извлекаем название плейлиста
        parts = cmd.split('"')
        if len(parts) >= 2:
            playlist_name = parts[1]
        else:
            playlist_name = cmd.replace("--playlist", "").strip()
        
        print(f"  └ Переключение на плейлист: {playlist_name}")
        stop_playback()
        play_playlist_by_name(playlist_name)
    
    elif cmd.startswith("--wave"):
        # Извлекаем тему волны
        parts = cmd.split('"')
        if len(parts) >= 2:
            wave_theme = parts[1]
        else:
            wave_theme = cmd.replace("--wave", "").strip() or None
        
        print(f"  └ Переключение на волну: {wave_theme or 'персональная'}")
        stop_playback()
        play_wave(wave_theme)
    
    elif cmd == "--liked":
        print("  └ Переключение на 'Мне нравится'")
        stop_playback()
        play_liked_tracks()
    
    else:
        print(f"  ⚠️  Неизвестная команда: {cmd}")


def find_station_id_by_query(query: str, client: Client) -> str:
    """Ищет станцию «Моя волна» по текстовому запросу.

    Проходит по всем доступным станциям (жанры, настроения, активности,
    микрожанры, эпохи и т.д.) и ищет наиболее подходящую по названию.
    Возвращает ID станции в формате 'type:tag'."""

    # Стоп-слова для фильтрации
    stop_words = {'music', 'для', 'под', 'из', 'на', 'в', 'с', 'и', 'а', 'но',
                  'или', 'не', 'ни', '—', '-', 'dnd', 'dn', 'музыка', 'это',
                  'для', 'про', 'без', 'со', 'все', 'по', 'за', 'от', 'до'}

    # Словарь синонимов: пользовательский запрос → ключевые слова для поиска
    synonym_map = {
        'битва': ['aggressive', 'epic', 'hard', 'battle', 'fight', 'heavy'],
        'битвы': ['aggressive', 'epic', 'hard', 'battle', 'fight', 'heavy'],
        'эпичн': ['epic'],
        'эпическое': ['epic'],
        'эпичная': ['epic'],
        'грустн': ['sad', 'sentimental', 'calm'],
        'весел': ['happy', 'energetic', 'party'],
        'радостн': ['happy'],
        'спокойн': ['calm', 'relaxed', 'dream', 'lounge'],
        'агрессивн': ['aggressive', 'hard', 'heavy', 'metal'],
        'бодр': ['energetic', 'run', 'workout', 'party'],
        'энергичн': ['energetic', 'run', 'workout', 'party'],
        'работа': ['work', 'study', 'concentration', 'calm'],
        'работать': ['work', 'study', 'concentration', 'calm'],
        'концентрац': ['study', 'work', 'concentration'],
        'сон': ['sleep', 'fall', 'calm', 'relaxed', 'dream'],
        'засыпа': ['fall', 'sleep', 'calm'],
        'дорог': ['driving', 'road'],
        'путешеств': ['road', 'driving', 'drive'],
        'спорт': ['sport', 'workout', 'run', 'energetic'],
        'тренировк': ['workout', 'run', 'sport', 'energetic'],
        'бег': ['run', 'sport', 'energetic'],
        'романтик': ['romantic', 'beloved', 'love', 'relaxed'],
        'свидан': ['romantic', 'date', 'romantic'],
        'кино': ['films', 'soundtrack', 'movie', 'cinema'],
        'фильм': ['films', 'soundtrack', 'movie', 'cinema'],
        'игра': ['videogame', 'game', 'animated', 'epic'],
        'видеоигр': ['videogame', 'game'],
        'приключен': ['adventure', 'epic', 'folk'],
        'героическ': ['epic', 'heroic', 'epicmetal'],
        'таинствен': ['mystery', 'haunting', 'dark', 'dream'],
        'мистическ': ['mystery', 'haunting', 'dark'],
        'тревожн': ['dark', 'haunting', 'aggressive', 'sad'],
        'мотивирующ': ['energetic', 'epic', 'party', 'happy'],
        'ностальг': ['sentimental', 'calm', 'relaxed', 'dream'],
        'космос': ['space', 'ambient', 'epic', 'newage'],
        'природ': ['nature', 'relax', 'calm', 'ambient'],
        'ночь': ['night', 'dark', 'calm', 'dream'],
        'утро': ['morning', 'wake', 'energetic', 'happy'],
        'джаз': ['jazz'],
        'классик': ['classical', 'classicalmusic'],
        'рок': ['rock', 'allrock'],
        'метал': ['metal', 'heavy'],
        'тяжел': ['metal', 'hardrock', 'heavy'],
        'электрон': ['electronic', 'electronics', 'techno', 'house'],
        'танцевальн': ['dance', 'party', 'electronic', 'edm'],
        'релакс': ['relax', 'calm', 'lounge'],
        'медитац': ['meditation', 'calm', 'relax', 'ambient'],
    }

    stations = client.rotor_stations_list()
    query_lower = query.lower().strip()

    # Разбиваем запрос на ключевые слова и убираем стоп-слова
    raw_keywords = set(re.findall(r'\w+', query_lower))
    keywords = [kw for kw in raw_keywords if kw not in stop_words]

    # Добавляем синонимы
    expanded_keywords = set(keywords)
    for kw in keywords:
        for synonym_key, synonyms in synonym_map.items():
            if synonym_key in kw or kw in synonym_key:
                for syn in synonyms:
                    expanded_keywords.add(syn)

    # Также проверяем каждый исходный keyword на частичное совпадение с ключами синонимов
    for kw in keywords:
        for synonym_key, synonyms in synonym_map.items():
            if synonym_key in kw:
                for syn in synonyms:
                    expanded_keywords.add(syn)

    keywords = list(expanded_keywords)

    best_match = None
    best_score = 0

    for s in stations:
        st = s.station
        name = st.name.lower()
        station_id = f"{st.id.type}:{st.id.tag}"

        # Пропускаем персональные, редакционные, авторские
        if st.id.type in ('personal', 'editorial', 'author', 'epoch', 'local-language'):
            continue

        # Считаем совпадения ключевых слов в названии станции
        score = 0
        for kw in keywords:
            if kw in name:
                score += 1

        # Точное совпадение слова целиком — дополнительный балл
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', name):
                score += 2

        # Бонус за совпадение с тегом (английский ID станции)
        tag_lower = st.id.tag.lower()
        for kw in keywords:
            if kw in tag_lower:
                score += 3

        # Приоритет для mood (настроение) и activity (активность) — они точнее
        type_bonus = {'mood': 2, 'activity': 2, 'genre': 1, 'micro-genre': 0.5}
        score *= type_bonus.get(st.id.type, 0.8)

        if score > best_score:
            best_score = score
            best_match = station_id

    if best_match and best_score > 0:
        return best_match

    # Если не нашли — возвращаем персональную волну
    print(f"  ⚠️  Не удалось найти станцию по запросу '{query}'. Запускаю персональную волну.")
    return "user:onyourwave"


def play_wave(query: Optional[str] = None):
    """Запускает бесконечное воспроизведение «Моей волны».

    Если query пустой — использует персональную станцию user:onyourwave.
    Иначе ищет станцию по текстовому запросу."""
    global _exit_requested
    _exit_requested = False

    client = get_client()

    if query:
        print(f"🔍 Поиск станции по запросу: '{query}'...")
        station_id = find_station_id_by_query(query, client)
    else:
        station_id = "user:onyourwave"
        print("🎵 Запуск персональной станции «Моя волна»...")

    print(f"✓ Станция: {station_id}")

    # Бесконечный цикл воспроизведения
    track_index = 0
    while not _exit_requested:
        track_index += 1
        try:
            station_tracks = client.rotor_station_tracks(station_id)
        except Exception as e:
            if _exit_requested:
                break
            print(f"❌ Ошибка загрузки треков станции: {e}")
            print("  Повторная попытка через 5 секунд...")
            time.sleep(5)
            continue

        if not station_tracks or not hasattr(station_tracks, 'sequence') or not station_tracks.sequence:
            if _exit_requested:
                break
            print("  ⚠️  Нет треков в очереди. Жду 5 секунд...")
            time.sleep(5)
            continue

        for batch_item in station_tracks.sequence:
            if _exit_requested:
                break

            try:
                if hasattr(batch_item, 'track'):
                    track = batch_item.track
                elif hasattr(batch_item, 'fetch_track'):
                    track = batch_item.fetch_track()
                else:
                    continue

                if not track:
                    continue

                title = getattr(track, 'title', 'Unknown')
                artists = track.artists if hasattr(track, 'artists') else []
                artist_str = ", ".join(a.name for a in artists) if artists else "Unknown"

                print(f"\n[{track_index}] {artist_str} — {title}")
                print(f"  └ Загрузка информации...")

                track_url = get_track_url(track, client)
                if not track_url:
                    continue

                stop_playback()
                time.sleep(0.3)

                if _exit_requested:
                    break

                play_track(track_url, title, artist_str)

                print(f"  └ Ожидание завершения (Ctrl+C — пропустить, Ctrl+X — выйти)...")
                cmd = _wait_for_wave_command()
                if cmd:
                    # Получена команду из файла — обрабатываем
                    _handle_remote_command(cmd, client)
                    if _exit_requested:
                        break
                    # После обработки команды перезапускаем внешний цикл для новых треков
                    track_index += 1
                    break
            except KeyboardInterrupt:
                print("\n  ⏭ Пропускаю трек...")
                stop_playback()
                continue

        if _exit_requested:
            break

    print("  └ Завершение воспроизведения волны.")


def _wait_for_wave_command() -> Optional[str]:
    """Ждёт завершения трека или появления команды в режиме wave. Возвращает команду или None."""
    while True:
        cmd = check_command_file()
        if cmd:
            return cmd
        if _exit_requested:
            return None
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq mpv.exe", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5
            )
            if "mpv.exe" not in result.stdout:
                return None
        else:
            result = subprocess.run(
                ["pgrep", "-x", "mpv"], capture_output=True, text=True, timeout=5
            )
            if not result.stdout.strip():
                return None
        time.sleep(0.5)


def _on_exit_global():
    """Callback для глобального выхода по Ctrl+X."""
    stop_playback()
    os._exit(0)


def main():
    parser = argparse.ArgumentParser(
        description="Управление Яндекс Музыкой из консоли"
    )
    parser.add_argument(
        "--playlist", type=str, default=None,
        help="Искать и запустить плейлист по названию"
    )
    parser.add_argument(
        "--liked", action="store_true",
        help="Запустить плейлист 'Мне нравится'"
    )
    parser.add_argument(
        "--wave", type=str, nargs="?", const="",
        help="Запустить «Мою волну». Без аргумента — персональная станция. "
             "С аргументом — поиск по жанру/настроению (например --wave \"эпичное\")"
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Остановить воспроизведение"
    )

    args = parser.parse_args()

    actions = sum([bool(args.playlist), args.liked, args.wave is not None, args.stop])
    if actions == 0:
        parser.print_help()
        print("\n❌ Укажи одно из действий: --playlist, --liked, --wave или --stop")
        sys.exit(1)
    elif actions > 1:
        print("❌ Укажи только одно действие.")
        sys.exit(1)

    # Запускаем глобальный слушатель Ctrl+X для всех режимов
    start_keyboard_listener(_on_exit_global)

    if args.stop:
        stop_playback()
    elif args.liked:
        play_liked_tracks()
    elif args.playlist:
        play_playlist_by_name(args.playlist)
    elif args.wave is not None:
        query = args.wave if args.wave else None
        play_wave(query)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹ Воспроизведение прервано пользователем.")
        stop_playback()
        sys.exit(0)
