import time
import requests
import random
import threading
from datetime import datetime

processed_message_ids = set()
used_api_keys = set()
last_generated_text = None
cooldown_time = 86400


def log_message(queue, message, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    icon_map = {
        "SUCCESS": "✅",
        "ERROR": "🚨",
        "WARNING": "⚠️",
        "WAIT": "⌛",
        "INFO": "ℹ️",
    }
    icon = icon_map.get(level.upper(), "ℹ️")
    formatted_message = f"[{timestamp}] {icon} {message}"
    if queue:
        queue.put(formatted_message)
    else:
        print(formatted_message)


def get_random_api_key(google_api_keys, queue):
    global used_api_keys
    available_keys = [key for key in google_api_keys if key not in used_api_keys]
    if not available_keys:
        log_message(queue, "Semua API key 429. Menunggu 24 jam...", "ERROR")
        time.sleep(cooldown_time)
        used_api_keys.clear()
        return get_random_api_key(google_api_keys, queue)
    return random.choice(available_keys)


def generate_reply(prompt, prompt_language, use_google_ai, google_api_keys, queue):
    global last_generated_text
    if use_google_ai:
        if not google_api_keys or not any(google_api_keys):
            log_message(queue, "Tidak ada Google API Key.", "ERROR")
            return None

        google_api_key = get_random_api_key(google_api_keys, queue)

        if prompt_language == "id":
            ai_prompt = f"Balas pesan berikut dalam Bahasa Indonesia: '{prompt}'. Buat balasan menjadi satu kalimat santai dan kasual tanpa simbol seperti yang diucapkan manusia sehari-hari."
        else:
            ai_prompt = f"Reply to the following message in English: '{prompt}'. Make the reply a single, casual sentence like a human would say."

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={google_api_key}"
        headers = {"Content-Type": "application/json"}
        data = {"contents": [{"parts": [{"text": ai_prompt}]}]}

        try:
            response = requests.post(url, headers=headers, json=data, timeout=20)

            if response.status_code == 429:
                log_message(
                    queue,
                    f"API key {google_api_key[:5]}... kena rate limit (429). Tugas dihentikan sementara.",
                    "WARNING",
                )
                used_api_keys.add(google_api_key)
                return None

            response.raise_for_status()
            result = response.json()
            generated_text = result["candidates"][0]["content"]["parts"][0][
                "text"
            ].strip()

            if generated_text.lower() == last_generated_text or not generated_text:
                return generate_reply(
                    prompt, prompt_language, use_google_ai, google_api_keys, queue
                )

            last_generated_text = generated_text.lower()
            return generated_text

        except requests.exceptions.RequestException as e:
            log_message(queue, f"Request failed: {e}", "ERROR")
            return None

    else:
        try:
            with open("pesan.txt", "r", encoding="utf-8") as file:
                messages = [line.strip() for line in file.readlines() if line.strip()]
                return (
                    random.choice(messages)
                    if messages
                    else "Tidak ada pesan di pesan.txt"
                )
        except FileNotFoundError:
            return "File pesan.txt tidak ditemukan!"


def send_message(
    channel_id,
    message_text,
    token,
    queue,
    reply_to=None,
    delete_after=None,
    delete_immediately=False,
):
    headers = {"Authorization": token, "Content-Type": "application/json"}
    payload = {"content": message_text}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to}
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        message_id = data.get("id")
        log_message(
            queue, f'[{channel_id}] Pesan terkirim: "{message_text}"', "SUCCESS"
        )

        if delete_after is not None and delete_after >= 0:
            delay = 0 if delete_immediately else delete_after
            threading.Timer(
                delay, delete_message, args=(channel_id, message_id, token, queue)
            ).start()
            if delay > 0:
                log_message(
                    queue,
                    f"[{channel_id}] Pesan akan dihapus dalam {delay} detik.",
                    "WAIT",
                )

    except requests.exceptions.RequestException as e:
        log_message(queue, f"[{channel_id}] Gagal kirim pesan: {e}", "ERROR")


def delete_message(channel_id, message_id, token, queue):
    headers = {"Authorization": token}
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages/{message_id}"
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code == 204:
            log_message(queue, f"[{channel_id}] Pesan {message_id} dihapus.", "SUCCESS")
        else:
            log_message(
                queue,
                f"[{channel_id}] Gagal hapus {message_id}. Status: {response.status_code}",
                "ERROR",
            )
    except requests.exceptions.RequestException as e:
        log_message(queue, f"[{channel_id}] Error hapus pesan: {e}", "ERROR")


def auto_reply(channel_id, settings, token, google_api_keys, queue, stop_event):
    headers = {"Authorization": token}

    username, _, bot_user_id = get_bot_info(token, queue)
    if bot_user_id == "UnknownID":
        log_message(queue, f"[{channel_id}] Gagal memulai: Token tidak valid.", "ERROR")
        return

    while not stop_event.is_set():
        try:
            if settings.get("use_google_ai"):
                if stop_event.wait(timeout=settings.get("read_delay", 10)):
                    break

                response = requests.get(
                    f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=1",
                    headers=headers,
                )
                response.raise_for_status()
                messages = response.json()

                if messages:
                    last_message = messages[0]
                    author_id = last_message.get("author", {}).get("id")
                    message_id = last_message.get("id")

                    if (
                        author_id != bot_user_id
                        and message_id not in processed_message_ids
                    ):
                        processed_message_ids.add(message_id)
                        user_message = last_message.get("content", "").strip()

                        if user_message:
                            log_message(
                                queue,
                                f"[{channel_id}] Pesan diterima: {user_message}",
                                "INFO",
                            )
                            reply_text = generate_reply(
                                user_message,
                                settings.get("prompt_language"),
                                True,
                                google_api_keys,
                                queue,
                            )
                            if reply_text:
                                send_message(
                                    channel_id,
                                    reply_text,
                                    token,
                                    queue,
                                    reply_to=(
                                        message_id
                                        if settings.get("use_reply")
                                        else None
                                    ),
                                    delete_after=settings.get("delete_bot_reply"),
                                    delete_immediately=settings.get(
                                        "delete_immediately"
                                    ),
                                )
            else:
                if stop_event.wait(timeout=settings.get("delay_interval", 30)):
                    break
                message_text = generate_reply("", "", False, [], queue)
                send_message(
                    channel_id,
                    message_text,
                    token,
                    queue,
                    delete_after=settings.get("delete_bot_reply"),
                    delete_immediately=settings.get("delete_immediately"),
                )

            if stop_event.wait(timeout=settings.get("delay_interval", 30)):
                break

        except requests.exceptions.RequestException as e:
            log_message(queue, f"[{channel_id}] Terjadi Error: {e}", "ERROR")
            if stop_event.wait(timeout=60):
                break
        except Exception as e:
            log_message(
                queue, f"[{channel_id}] Terjadi kesalahan tak terduga: {e}", "ERROR"
            )
            if stop_event.wait(timeout=60):
                break


def get_channel_info(channel_id, token, queue):
    headers = {"Authorization": token}
    try:
        res = requests.get(
            f"https://discord.com/api/v9/channels/{channel_id}",
            headers=headers,
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        server_name = "Direct Message"
        if guild_id := data.get("guild_id"):
            guild_res = requests.get(
                f"https://discord.com/api/v9/guilds/{guild_id}",
                headers=headers,
                timeout=10,
            )
            guild_res.raise_for_status()
            server_name = guild_res.json().get("name", "Unknown Server")
        return server_name, data.get("name", "Unknown Channel")
    except requests.exceptions.RequestException:
        return "Akses Error", "Periksa Token/ID"


def get_bot_info(token, queue):
    headers = {"Authorization": token}
    try:
        res = requests.get(
            "https://discord.com/api/v9/users/@me", headers=headers, timeout=10
        )
        res.raise_for_status()
        data = res.json()
        return (
            data.get("username", "Unknown"),
            data.get("discriminator", "0000"),
            data.get("id", "UnknownID"),
        )
    except requests.exceptions.RequestException:
        log_message(queue, f"Token...{token[-4:]} tidak valid.", "ERROR")
        return "Unknown", "0000", "UnknownID"
