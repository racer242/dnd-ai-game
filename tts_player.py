#!/usr/bin/env python3
"""
tts_player.py — TTS плеер для озвучки текста Мастера D&D через edge-tts.
Воспроизводит речь через mpv без блокировки основного процесса.

Использование:
    python tts_player.py --start          # Запустить TTS-плеер
    python tts_player.py --stop           # Остановить TTS-плеер
"""

import sys
import os
import signal
import subprocess
import argparse
import time
import threading
import yaml
from typing import Optional
from pathlib import Path

# Глобальный флаг для выхода
_exit_requested = False

# Файл для межпроцессной коммуникации
_COMMAND_FILE = ".tts_command_queue"
_CURRENT_AUDIO_FILE = ".tts_current_audio"
_TEXT_FILE = ".tts_text_input"

# Путь к текущему скрипту (для определения директории проекта)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    """Загружает конфигурацию из tts_config.yaml."""
    config_path = os.path.join(SCRIPT_DIR, "tts_config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return config or {}
    except FileNotFoundError:
        print("⚠️  tts_config.yaml не найден. Используются настройки по умолчанию.")
        return {
            "master_voice": "ru-RU-DmitryNeural",
            "output_dir": "temp_tts/",
            "player_path": r"C:\Program Files\MPV Player\mpv.exe",
            "rate": "+50%",
            "volume": "+0%",
            "pitch": "+0Hz",
        }
    except Exception as e:
        print(f"⚠️  Ошибка загрузки конфигурации: {e}")
        return {}


def ensure_output_dir(output_dir: str):
    """Создаёт директорию для временных файлов, если её нет."""
    dir_path = os.path.join(SCRIPT_DIR, output_dir)
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


def stop_playback():
    """Останавливает все запущенные процессы mpv для TTS."""
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
        print("⏹ TTS воспроизведение остановлено.")


def generate_speech(text: str, voice: str, output_path: str, 
                    rate: str = "+50%", volume: str = "+0%", pitch: str = "+0Hz") -> bool:
    """Генерирует аудиофайл через edge-tts."""
    try:
        cmd = [
            "edge-tts",
            "--voice", voice,
            "--text", text,
            "--write-media", output_path,
        ]
        
        # Добавляем параметры если они отличаются от стандартных
        if rate != "+0%":
            cmd.extend(["--rate", rate])
        if volume != "+0%":
            cmd.extend(["--volume", volume])
        if pitch != "+0Hz":
            cmd.extend(["--pitch", pitch])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0 and os.path.exists(output_path):
            file_size = os.path.getsize(output_path)
            if file_size > 0:
                print(f"✓ Аудио сгенерировано: {output_path} ({file_size} байт)")
                return True
        
        print(f"⚠️  Ошибка генерации TTS: {result.stderr}")
        return False
        
    except subprocess.TimeoutExpired:
        print("⚠️  Таймаут генерации TTS (60 сек)")
        return False
    except FileNotFoundError:
        print("❌ edge-tts не найден. Установите: pip install edge-tts")
        return False
    except Exception as e:
        print(f"❌ Ошибка генерации TTS: {e}")
        return False


def play_audio(audio_path: str, player_path: str):
    """Запускает воспроизведение аудио через mpv."""
    try:
        # Проверяем существование файла
        if not os.path.exists(audio_path):
            print(f"❌ Аудиофайл не найден: {audio_path}")
            return False
        
        subprocess.Popen(
            [player_path, "--no-terminal", "--quiet", audio_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print(f"▶ Воспроизведение: {audio_path}")
        return True
        
    except FileNotFoundError:
        print(f"❌ mpv не найден по пути: {player_path}")
        print("  Установите mpv или укажите правильный путь в конфиге.")
        return False
    except Exception as e:
        print(f"❌ Ошибка запуска mpv: {e}")
        return False


def check_command_file() -> Optional[str]:
    """Проверяет файл команд и возвращает команду."""
    cmd_path = os.path.join(SCRIPT_DIR, _COMMAND_FILE)
    if os.path.exists(cmd_path):
        try:
            with open(cmd_path, "r", encoding="utf-8") as f:
                cmd = f.read().strip()
            os.remove(cmd_path)
            return cmd
        except Exception as e:
            print(f"⚠️  Ошибка чтения файла команд: {e}")
    return None


def split_text_into_chunks(text: str, max_chars: int = 4000) -> list:
    """Разбивает текст на части для edge-tts (ограничение ~4000 символов).
    Разбивка происходит по предложениям чтобы не разрывать слова."""
    
    if len(text) <= max_chars:
        return [text]
    
    chunks = []
    current_chunk = ""
    
    # Разбиваем по предложениям (., !, ?, \n)
    sentences = []
    current_sentence = ""
    
    for char in text:
        current_sentence += char
        if char in '.!?\n' and len(current_sentence) > 10:
            sentences.append(current_sentence.strip())
            current_sentence = ""
    
    if current_sentence.strip():
        sentences.append(current_sentence.strip())
    
    # Собираем чанки из предложений
    for sentence in sentences:
        if len(current_chunk) + len(sentence) + 1 <= max_chars:
            current_chunk += " " + sentence if current_chunk else sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sentence
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


def process_tts_command(cmd: str, config: dict, output_dir: str) -> bool:
    """Обрабатывает TTS команду и возвращает True если нужно выйти."""
    global _exit_requested
    
    if cmd == "--stop":
        print("⏹ Остановка TTS-плеера...")
        stop_playback()
        _exit_requested = True
        return True
    
    if cmd == "--process":
        # Читаем текст из файла
        text_path = os.path.join(SCRIPT_DIR, _TEXT_FILE)
        if not os.path.exists(text_path):
            print("⚠️  Файл с текстом не найден")
            return False
        
        try:
            with open(text_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"⚠️  Ошибка чтения текста: {e}")
            return False
        
        if not text or not text.strip():
            print("⚠️  Пустой текст для озвучки")
            return False
        
        print(f"🎤 Получен текст: {len(text)} символов")
        
        # Останавливаем предыдущее воспроизведение
        stop_playback()
        time.sleep(0.2)
        
        # Разбиваем текст на части если он слишком длинный
        chunks = split_text_into_chunks(text)
        print(f"📝 Текст разбит на {len(chunks)} частей")
        
        # Генерируем и воспроизводим каждую часть
        for i, chunk in enumerate(chunks):
            if _exit_requested:
                break
            
            print(f"🔊 Часть {i+1}/{len(chunks)} ({len(chunk)} символов)")
            
            # Имя файла для части
            if len(chunks) > 1:
                audio_path = os.path.join(output_dir, f"tts_part_{i}.mp3")
            else:
                audio_path = os.path.join(output_dir, "tts_output.mp3")
            
            # Генерируем аудио
            success = generate_speech(
                text=chunk,
                voice=config.get("master_voice", "ru-RU-DmitryNeural"),
                output_path=audio_path,
                rate=config.get("rate", "+50%"),
                volume=config.get("volume", "+0%"),
                pitch=config.get("pitch", "+0Hz"),
            )
            
            if not success:
                print(f"⚠️  Ошибка генерации части {i+1}, пропускаем")
                continue
            
            # Записываем текущий файл
            with open(os.path.join(SCRIPT_DIR, _CURRENT_AUDIO_FILE), "w") as f:
                f.write(audio_path)
            
            # Воспроизводим
            play_audio(
                audio_path=audio_path,
                player_path=config.get("player_path", "mpv"),
            )
            
            # Ждём окончания воспроизведения перед следующей частью
            if i < len(chunks) - 1 and not _exit_requested:
                wait_for_playback_end()
        
        return False
    
    print(f"⚠️  Неизвестная команда: {cmd}")
    return False


def wait_for_playback_end(timeout: float = 60):
    """Ждёт окончания воспроизведения mpv."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if _exit_requested:
            break
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq mpv.exe", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5
            )
            if "mpv.exe" not in result.stdout:
                return
        else:
            result = subprocess.run(
                ["pgrep", "-x", "mpv"], capture_output=True, text=True, timeout=5
            )
            if not result.stdout.strip():
                return
        time.sleep(0.3)


def main_loop(config: dict):
    """Основной цикл ожидания команд."""
    global _exit_requested
    
    output_dir = ensure_output_dir(config.get("output_dir", "temp_tts/"))
    
    print(f"\n{'='*60}")
    print("  ✅ TTS-плеер запущен и ожидает команды")
    print('  ➤ Используйте: python tts_remote.py --text "текст"')
    print("  ➤ Для остановки: python tts_remote.py --stop")
    print(f"{'='*60}\n")
    
    while not _exit_requested:
        cmd = check_command_file()
        if cmd:
            print(f"📩 Получена команда: {cmd}")
            should_exit = process_tts_command(cmd, config, output_dir)
            if should_exit:
                break
        time.sleep(0.3)


def start_tts_player():
    """Запускает TTS-плеер."""
    global _exit_requested
    _exit_requested = False
    
    config = load_config()
    
    print("🎙  Запуск TTS-плеера...")
    print(f"   Голос: {config.get('master_voice', 'ru-RU-DmitryNeural')}")
    print(f"   Скорость: {config.get('rate', '+50%')}")
    
    main_loop(config)


def stop_tts_player():
    """Останавливает TTS-плеер."""
    print("⏹  Отправка команды остановки TTS...")
    cmd_path = os.path.join(SCRIPT_DIR, _COMMAND_FILE)
    with open(cmd_path, "w", encoding="utf-8") as f:
        f.write("--stop")
    print("✓ Команда отправлена")


def main():
    parser = argparse.ArgumentParser(
        description="TTS-плеер для озвучки текста Мастера D&D"
    )
    parser.add_argument(
        "--start", action="store_true",
        help="Запустить TTS-плеер"
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Остановить TTS-плеер"
    )
    
    args = parser.parse_args()
    
    if args.start:
        start_tts_player()
    elif args.stop:
        stop_tts_player()
    else:
        parser.print_help()
        print("\n❌ Укажи одно из действий: --start или --stop")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹ TTS-плеер прерван пользователем.")
        stop_playback()
        sys.exit(0)