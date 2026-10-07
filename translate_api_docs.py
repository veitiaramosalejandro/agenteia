import json
import re
import time
import urllib.parse
import urllib.request


SOURCE = "api-docs.md"
TARGET = "api-docs.pt.md"


def translate(text: str) -> str:
    placeholders = []

    def hold(match):
        placeholders.append(match.group(0))
        return f" ZZPH{len(placeholders)-1}ZZ "

    protected = re.sub(r"`[^`]*`", hold, text)
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=es&tl=pt-PT&dt=t&q=" + urllib.parse.quote(protected)
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    result = "".join(part[0] for part in data[0] if part and part[0])
    for index, value in enumerate(placeholders):
        result = result.replace(f"ZZPH{index}ZZ", value).replace(f" ZZPH{index} ZZ", value)
    return result


lines = open(SOURCE, encoding="utf-8").read().splitlines(keepends=True)
out = []
prose = []
prose_size = 0
in_fence = False


def flush():
    global prose_size
    if not prose:
        return
    text = "".join(prose)
    body = text.rstrip("\n")
    ending = text[len(body):]
    if body.strip() and not re.fullmatch(r"[-*_ ]+", body.strip()):
        for attempt in range(3):
            try:
                translated = translate(body)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1)
        out.append(translated + ending)
    else:
        out.append(text)
    prose.clear()
    prose_size = 0


for line in lines:
    if line.lstrip().startswith("```"):
        flush()
        out.append(line)
        in_fence = not in_fence
    elif in_fence:
        out.append(line)
    else:
        prose.append(line)
        prose_size += len(line)
        if prose_size >= 4500:
            flush()
flush()
open(TARGET, "w", encoding="utf-8", newline="").write("".join(out))
print(TARGET)