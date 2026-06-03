#!/usr/bin/env python3
"""
yandex_remote.py — Управление запущенным yandex_playlist_player.py через файл-команду.

Использование:
    python yandex_remote.py --playlist "название"
    python yandex_remote.py --wave
    python yandex_remote.py --wave "эпичное"
    python yandex_remote.py --liked
    python yandex_remote.py --stop
    python yandex_remote.py --silence
"""
import sys
import os

COMMAND_FILE = ".command_queue"


def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print('  python yandex_remote.py --playlist "название"')
        print("  python yandex_remote.py --wave [тема]")
        print("  python yandex_remote.py --liked")
        print("  python yandex_remote.py --stop")
        print("  python yandex_remote.py --silence")
        sys.exit(1)

    command = " ".join(sys.argv[1:])

    # Записываем команду в файл
    with open(COMMAND_FILE, "w", encoding="utf-8") as f:
        f.write(command)

    print(f"✓ Команда отправлена: {command}")


if __name__ == "__main__":
    main()