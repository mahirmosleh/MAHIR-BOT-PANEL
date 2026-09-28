import json
from pathlib import Path

# স্ক্রিপ্ট যে ফোল্ডারে আছে সেটাই অটোমেটিক ব্যবহার করবে
FOLDER = Path(__file__).resolve().parent
# অথবা চাইলে ম্যানুয়ালি দিতে পারেন:
# FOLDER = Path(r"C:\Users\YourName\Desktop\30+Account")

OUT_TXT = FOLDER / "MAHIR.txt"
OUT_JSON = FOLDER / "MAHIR.json"

# .dat ফাইলের ভেতরে যে key গুলো থেকে UID/Password নিবে
UID_KEYS = (
    "uid",
    "account_id",
    "com.garena.msdk.guest_uid",
)
PWD_KEYS = (
    "password",
    "com.garena.msdk.guest_password",
)


def _dig(data, keys):
    """nested dict এর ভেতরেও key খুঁজে বের করবে।"""
    if isinstance(data, dict):
        for k in keys:
            if k in data and data[k]:
                return data[k]
        for v in data.values():
            found = _dig(v, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _dig(item, keys)
            if found:
                return found
    return None


def extract_uid_password(file_path: Path):
    pairs = []
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                uid = _dig(data, UID_KEYS)
                password = _dig(data, PWD_KEYS)

                if uid and password:
                    pairs.append((str(uid), str(password)))
    except Exception as e:
        print(f"স্কিপ করা হলো {file_path.name}: {e}")
    return pairs


def main():
    if not FOLDER.exists():
        print(f"ফোল্ডার পাওয়া যায়নি: {FOLDER}")
        return

    all_pairs = []
    seen = set()

    # .txt এবং .dat দুটোই স্ক্যান করবে
    for file_path in FOLDER.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in {".txt", ".dat"}:
            continue
        if file_path.name in {OUT_TXT.name, OUT_JSON.name}:
            continue

        for uid, pwd in extract_uid_password(file_path):
            key = (uid, pwd)
            if key not in seen:
                seen.add(key)
                all_pairs.append(key)

    all_pairs.sort(key=lambda x: x[0])

    with OUT_TXT.open("w", encoding="utf-8") as f:
        for uid, pwd in all_pairs:
            f.write(f"{uid}:{pwd}\n")

    json_data = [{"uid": uid, "password": pwd} for uid, pwd in all_pairs]
    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    print(f"সম্পন্ন। মোট {len(all_pairs)} টি uid:password পাওয়া গেছে।")
    print(f"TXT ফাইল: {OUT_TXT}")
    print(f"JSON ফাইল: {OUT_JSON}")


if __name__ == "__main__":
    main()