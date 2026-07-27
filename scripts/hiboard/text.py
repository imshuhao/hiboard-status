"""UTF-16 语义的文本工具（服务端按 Java String.length 计长）。"""

import hashlib
import re


def utf16_len(s: str) -> int:
    """按服务端语义计长：UTF-16 码元数，emoji 计 2。"""
    return len(s.encode("utf-16-le")) // 2


def truncate_utf16(s: str, limit: int) -> str:
    """截断到 limit 个 UTF-16 码元以内，超长加 …（… 本身占 1 码元）。"""
    if limit <= 0:
        return ""
    if utf16_len(s) <= limit:
        return s
    cut = s.encode("utf-16-le")[: max(limit - 1, 0) * 2]
    # 若切口落在代理对中间，decode(ignore) 会丢弃孤立代理项
    return cut.decode("utf-16-le", "ignore").rstrip() + "…"


def slugify_ascii(name: str) -> str:
    """主题名 → 稳定 slug。含非 ASCII 字符时附加短哈希——否则所有中文主题
    会塌缩成同一个 ID 互相覆盖，而主题卡永久存在，塌缩无法挽回。"""
    s = "".join(c if c.isascii() and c.isalnum() else "_" for c in name).lower()
    s = re.sub(r"_+", "_", s).strip("_")
    if not name.isascii():
        h = hashlib.md5(name.encode("utf-8")).hexdigest()[:6]
        s = f"{s}_{h}" if s else h
    return s or "topic"
