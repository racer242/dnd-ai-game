#!/usr/bin/env python3
"""
tts_remote.py — Управление запущенным tts_player.py через файлы команд.

Использование:
    python tts_remote.py --text "Текст для озвучки"
    python tts_remote.py --stop
"""
import sys
import os

COMMAND_FILE = ".tts_command_queue"
TEXT_FILE = ".tts_text_input"


def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print('  python tts_remote.py --text "Текст для озвучки"')
        print("  python tts_remote.py --stop")
        sys.exit(1)

    # Определяем команду
    if sys.argv[1] == "--stop":
        command = "--stop"
        with open(COMMAND_FILE, "w", encoding="utf-8") as f:
            f.write(command)
        print("✓ Команда остановки отправлена")
        return

    if sys.argv[1] == "--text":
        # Собираем весь текст после --text
        # Поддерживаем как кавычки, так и прямой ввод
        text_parts = sys.argv[2:]
        
        if not text_parts:
            print("⚠️  Текст не указан")
            sys.exit(1)
        
        # Объединяем все части
        full_text = " ".join(text_parts)
        
        # Убираем лишние кавычки если они есть (когда текст в кавычках)
        if full_text.startswith('"') and full_text.endswith('"'):
            full_text = full_text[1:-1]
        
        # Записываем текст в отдельный файл (надёжнее чем передавать через аргументы)
        with open(TEXT_FILE, "w", encoding="utf-8") as f:
            f.write(full_text)
        
        # Записываем команду обработки текста
        with open(COMMAND_FILE, "w", encoding="utf-8") as f:
            f.write("--process")
        
        print(f"✓ TTS текст отправлен ({len(full_text)} символов)")
        return

    print(f"⚠️  Неизвестная команда: {sys.argv[1]}")
    sys.exit(1)


if __name__ == "__main__":
    main()