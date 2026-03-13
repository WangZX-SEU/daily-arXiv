#!/usr/bin/env python3
"""Generate daily keyword report and Google source-guide style summary."""

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Set, Tuple

import requests


TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "autonomous exploration": [
        "autonomous exploration",
        "robot exploration",
        "active exploration",
        "frontier exploration",
    ],
    "reinforcement learning": [
        "reinforcement learning",
        "deep reinforcement learning",
        "policy gradient",
        "q-learning",
        "rl",
    ],
    "path planning": [
        "path planning",
        "motion planning",
        "trajectory planning",
        "route planning",
    ],
    "VLM": [
        "vlm",
        "vision-language model",
        "vision language model",
        "vision-language models",
        "multimodal model",
    ],
}

TARGET_AUTHORS: Dict[str, List[str]] = {
    "Gao Fei": ["gao fei", "fei gao"],
    "Zhou Boyu": ["zhou boyu", "boyu zhou"],
    "Cao Yuhong": ["cao yuhong", "yuhong cao"],
    "Daniele Nardi": ["daniele nardi"],
    "Vincenzo Suriani": ["vincenzo suriani"],
    "Guillaume Sartoretti": ["guillaume sartoretti"],
}


def get_bjt_date() -> str:
    """Return date in Asia/Shanghai timezone without external dependency."""
    bjt = timezone(timedelta(hours=8))
    return datetime.now(bjt).strftime("%Y-%m-%d")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily focused arXiv report")
    parser.add_argument("--input", type=str, default="", help="Input jsonl file path")
    parser.add_argument("--date", type=str, default="", help="Report date, e.g. 2026-03-09")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="report",
        help="Directory for output markdown reports",
    )
    parser.add_argument(
        "--generate-source-guide",
        action="store_true",
        help="Generate NotebookLM-style source guide with Google API",
    )
    parser.add_argument(
        "--source-guide-dir",
        type=str,
        default="report/source_guides",
        help="Directory for source guide markdown output",
    )
    return parser.parse_args()


def load_papers(input_file: str) -> List[dict]:
    papers: List[dict] = []
    if not input_file or not os.path.exists(input_file):
        return papers

    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                papers.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return papers


def normalize_author_tokens(name: str) -> Set[str]:
    return set(re.findall(r"[a-z]+", name.lower()))


def build_alias_token_map() -> Dict[str, List[Set[str]]]:
    alias_map: Dict[str, List[Set[str]]] = {}
    for target, aliases in TARGET_AUTHORS.items():
        alias_map[target] = [normalize_author_tokens(alias) for alias in aliases]
    return alias_map


def match_target_authors(authors: Iterable[str], alias_token_map: Dict[str, List[Set[str]]]) -> List[str]:
    matched: List[str] = []
    for target, alias_sets in alias_token_map.items():
        for author in authors:
            author_tokens = normalize_author_tokens(str(author))
            if not author_tokens:
                continue
            if any(alias_tokens.issubset(author_tokens) for alias_tokens in alias_sets):
                matched.append(target)
                break
    return matched


def collect_search_text(item: dict) -> str:
    text_fields = [
        item.get("title", ""),
        item.get("summary", ""),
        item.get("comment", ""),
        " ".join(item.get("categories", [])) if isinstance(item.get("categories"), list) else "",
    ]
    ai = item.get("AI", {})
    if isinstance(ai, dict):
        text_fields.extend(str(v) for v in ai.values())
    return "\n".join(str(x) for x in text_fields if x).lower()


def match_topics(text: str) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for topic, keywords in TOPIC_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text]
        if hits:
            result[topic] = sorted(set(hits))
    return result


def short_summary(item: dict, limit: int = 220) -> str:
    ai = item.get("AI", {})
    candidate = ""
    if isinstance(ai, dict):
        candidate = str(ai.get("tldr", "")).strip()
    if not candidate:
        candidate = str(item.get("summary", "")).strip().replace("\n", " ")
    if len(candidate) > limit:
        return candidate[: limit - 3] + "..."
    return candidate


def analyze_matches(papers: List[dict]) -> Tuple[List[dict], Counter, Counter]:
    alias_map = build_alias_token_map()
    matched_records: List[dict] = []
    topic_counter: Counter = Counter()
    author_counter: Counter = Counter()

    for item in papers:
        topics = match_topics(collect_search_text(item))
        authors = item.get("authors", [])
        authors = authors if isinstance(authors, list) else []
        matched_authors = match_target_authors(authors, alias_map)
        if topics or matched_authors:
            matched_records.append({"item": item, "topics": topics, "authors": matched_authors})
            topic_counter.update(topics.keys())
            author_counter.update(matched_authors)
    return matched_records, topic_counter, author_counter


def build_source_entries(matched_records: List[dict], max_items: int = 40) -> List[dict]:
    entries = []
    for idx, record in enumerate(matched_records[:max_items], start=1):
        item = record["item"]
        entries.append(
            {
                "id": f"S{idx}",
                "title": item.get("title", "Untitled"),
                "url": item.get("abs", ""),
                "authors": ", ".join(item.get("authors", [])) if isinstance(item.get("authors"), list) else "",
                "topics": ", ".join(record["topics"].keys()) if record.get("topics") else "",
                "snapshot": short_summary(item),
            }
        )
    return entries


def build_local_source_guide(date_str: str, matched_records: List[dict]) -> str:
    if not matched_records:
        return "今日没有命中关键词/作者的论文，因此无来源指南内容。"
    lines = [
        f"本指南基于 {date_str} 命中的论文自动生成（本地回退版本）。",
        "主题概览：",
    ]
    topic_counter = Counter()
    for record in matched_records:
        topic_counter.update(record["topics"].keys())
    for topic in TOPIC_KEYWORDS:
        lines.append(f"- {topic}: {topic_counter.get(topic, 0)} 篇")
    lines.append("")
    lines.append("最值得优先阅读的方向：")
    if topic_counter:
        top_topics = [x[0] for x in topic_counter.most_common(2)]
        lines.append(f"- 今日命中更集中在 {', '.join(top_topics)}，建议先看该方向中的高频关键词论文。")
    else:
        lines.append("- 今日主要由作者命中触发，建议先看作者相关论文。")
    return "\n".join(lines)


def generate_source_guide_text(date_str: str, matched_records: List[dict], source_entries: List[dict]) -> str:
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    model_name = os.environ.get("NOTEBOOKLM_MODEL", "").strip() or "gemini-2.0-flash"
    if not api_key or not source_entries:
        return build_local_source_guide(date_str, matched_records)

    source_block = []
    for source in source_entries:
        source_block.append(
            f"[{source['id']}] 标题: {source['title']}\n"
            f"作者: {source['authors'] or 'Unknown'}\n"
            f"关键词关联: {source['topics'] or 'N/A'}\n"
            f"链接: {source['url'] or 'N/A'}\n"
            f"一句话摘要: {source['snapshot']}"
        )

    prompt = (
        "你是 NotebookLM 风格的研究助手。请基于给定来源，生成“来源指南”中文文本，"
        "聚焦主题脉络、代表性工作、创新趋势和与关键词相关性。\n"
        "输出格式要求：\n"
        "1) 用 4-6 条要点概述主题。\n"
        "2) 每条要点尽量附来源引用，如 [S1], [S3]。\n"
        "3) 最后给出“今天最值得关注的3篇”。\n"
        "4) 不要编造来源。\n\n"
        f"日期: {date_str}\n\n来源列表:\n" + "\n\n".join(source_block)
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1500},
    }

    try:
        response = requests.post(url, json=payload, timeout=80)
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return build_local_source_guide(date_str, matched_records)
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "\n".join(part.get("text", "").strip() for part in parts if part.get("text"))
        return text.strip() if text.strip() else build_local_source_guide(date_str, matched_records)
    except Exception as exc:  # noqa: BLE001
        fallback = build_local_source_guide(date_str, matched_records)
        return f"{fallback}\n\n> 注：Google 生成失败，已回退本地指南。错误：{exc}"


def save_source_guide(date_str: str, matched_records: List[dict], source_guide_dir: str) -> str:
    os.makedirs(source_guide_dir, exist_ok=True)
    source_entries = build_source_entries(matched_records)
    guide_text = generate_source_guide_text(date_str, matched_records, source_entries)
    output_path = os.path.join(source_guide_dir, f"{date_str}.md")

    lines = [
        f"# 来源指南（{date_str}）",
        "",
        "> 面向关键词命中文献的主题概述与阅读建议",
        "> 生成方式：Google NotebookLM 风格（通过 Google API 自动总结）",
        "",
        "## 主题概述",
        "",
        guide_text,
        "",
        "## 来源清单",
        "",
    ]
    if source_entries:
        for source in source_entries:
            lines.append(f"- [{source['id']}] [{source['title']}]({source['url']})")
    else:
        lines.append("- 今日无命中文献来源。")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return output_path


def render_report(
    date_str: str,
    input_file: str,
    papers: List[dict],
    matched_records: List[dict],
    topic_counter: Counter,
    author_counter: Counter,
    source_guide_rel_path: str = "",
) -> str:
    lines: List[str] = []
    lines.append(f"# arXiv 关键词日报（{date_str}）")
    lines.append("")
    lines.append("> 时区：北京时间（UTC+8）")
    lines.append(
        "> 关注主题关键词：autonomous exploration, reinforcement learning, path planning, VLM"
    )
    lines.append(
        "> 关注作者关键词：Gao Fei, Zhou Boyu, Cao Yuhong, Daniele Nardi, Vincenzo Suriani, Guillaume Sartoretti"
    )
    lines.append("")

    if not papers:
        lines.append("## 今日概览")
        lines.append("")
        if input_file:
            lines.append(f"- 数据文件：`{input_file}` 不存在或为空。")
        else:
            lines.append("- 今日无可用数据文件（可能是去重后无新内容）。")
        if source_guide_rel_path:
            lines.append(f"- 来源指南：[{source_guide_rel_path}]({source_guide_rel_path})")
        lines.append("- 本日报已自动生成，占位说明。")
        return "\n".join(lines) + "\n"

    lines.append("## 今日概览")
    lines.append("")
    lines.append(f"- 扫描论文总数：**{len(papers)}**")
    lines.append(f"- 命中（主题/作者）论文数：**{len(matched_records)}**")
    if source_guide_rel_path:
        lines.append(f"- 来源指南：[{source_guide_rel_path}]({source_guide_rel_path})")
    lines.append("")

    lines.append("### 主题命中统计")
    lines.append("")
    for topic in TOPIC_KEYWORDS:
        lines.append(f"- {topic}: {topic_counter.get(topic, 0)}")
    lines.append("")

    lines.append("### 作者命中统计")
    lines.append("")
    for author in TARGET_AUTHORS:
        lines.append(f"- {author}: {author_counter.get(author, 0)}")
    lines.append("")

    if not matched_records:
        lines.append("## 结论")
        lines.append("")
        lines.append("- 今日未发现与关注主题或作者关键词直接相关的论文。")
        return "\n".join(lines) + "\n"

    lines.append("## 按主题分类")
    lines.append("")
    for topic in TOPIC_KEYWORDS:
        topic_records = [r for r in matched_records if topic in r["topics"]]
        lines.append(f"### {topic}（{len(topic_records)}）")
        lines.append("")
        if not topic_records:
            lines.append("- 无")
            lines.append("")
            continue
        for idx, record in enumerate(topic_records, start=1):
            item = record["item"]
            title = item.get("title", "Untitled")
            abs_url = item.get("abs", "")
            categories = ", ".join(item.get("categories", [])) if isinstance(item.get("categories"), list) else ""
            authors = ", ".join(item.get("authors", [])) if isinstance(item.get("authors"), list) else ""
            matched_topic_terms = ", ".join(record["topics"].get(topic, []))
            matched_author_terms = ", ".join(record["authors"]) if record["authors"] else "无"
            lines.append(f"{idx}. **{title}**")
            if abs_url:
                lines.append(f"   - 链接：{abs_url}")
            lines.append(f"   - 作者：{authors if authors else '未知'}")
            lines.append(f"   - 分类：{categories if categories else '未知'}")
            lines.append(f"   - 主题命中词：{matched_topic_terms}")
            lines.append(f"   - 作者关键词命中：{matched_author_terms}")
            lines.append(f"   - 摘要：{short_summary(item)}")
            lines.append("")

    lines.append("## 按关注作者聚类")
    lines.append("")
    for author in TARGET_AUTHORS:
        author_records = [r for r in matched_records if author in r["authors"]]
        lines.append(f"### {author}（{len(author_records)}）")
        lines.append("")
        if not author_records:
            lines.append("- 无")
            lines.append("")
            continue
        for idx, record in enumerate(author_records, start=1):
            item = record["item"]
            title = item.get("title", "Untitled")
            abs_url = item.get("abs", "")
            topic_tags = ", ".join(record["topics"].keys()) if record["topics"] else "仅作者命中"
            lines.append(f"{idx}. **{title}**")
            if abs_url:
                lines.append(f"   - 链接：{abs_url}")
            lines.append(f"   - 主题关联：{topic_tags}")
            lines.append("")

    lines.append("## 备注")
    lines.append("")
    lines.append("- 命中规则基于关键词字符串匹配与作者名 token 匹配，可能存在少量误报/漏报。")
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    date_str = args.date.strip() if args.date else get_bjt_date()
    papers = load_papers(args.input)

    matched_records, topic_counter, author_counter = analyze_matches(papers)

    source_guide_rel_path = ""
    if args.generate_source_guide:
        source_guide_path = save_source_guide(
            date_str=date_str,
            matched_records=matched_records,
            source_guide_dir=args.source_guide_dir,
        )
        source_guide_abs = os.path.abspath(source_guide_path)
        source_guide_rel_path = os.path.relpath(
            source_guide_abs, start=os.path.abspath(args.output_dir)
        ).replace(os.sep, "/")

    report_text = render_report(
        date_str=date_str,
        input_file=args.input,
        papers=papers,
        matched_records=matched_records,
        topic_counter=topic_counter,
        author_counter=author_counter,
        source_guide_rel_path=source_guide_rel_path,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    output_file = os.path.join(args.output_dir, f"{date_str}.md")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"Generated report: {output_file}")
    if source_guide_rel_path:
        print(f"Generated source guide: {source_guide_rel_path}")


if __name__ == "__main__":
    main()
