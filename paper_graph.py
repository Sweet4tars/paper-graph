#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paper Graph - 论文引用图谱生成工具
===================================
基于 OpenAlex API 递归检索论文的引用(references)和被引(citations)关系,
BFS 构建论文图谱,输出交互式力导向图(HTML)。

数据源: OpenAlex (https://openalex.org) — 完全免费,无需 API key。
只需配置 mailto 进入 polite pool 获得更高速率。

用法:
  python paper_graph.py --seed 1512.03385
  python paper_graph.py --seed "Deep Residual Learning for Image Recognition" --depth 2
  python paper_graph.py --seed 10.48550/arXiv.1512.03385 --depth 1 --max-per-level 8
"""

import argparse
import json
import math
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from collections import deque
from pathlib import Path

# ============================================================
# OpenAlex API
# ============================================================
OA_BASE = "https://api.openalex.org"
OA_MAILTO = "papergraph@research.tool"  # polite pool


def fetch_json(url, retries=4, base_sleep=3):
    """带重试的 JSON 请求。"""
    if "?" in url:
        url += f"&mailto={OA_MAILTO}"
    else:
        url += f"?mailto={OA_MAILTO}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": f"PaperGraph/1.0 (mailto:{OA_MAILTO})",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = base_sleep * (attempt + 1) * 3
                print(f"  [rate-limit] 等待 {wait}s...")
                time.sleep(wait)
            elif e.code == 404:
                return None
            else:
                if attempt < retries - 1:
                    time.sleep(base_sleep * (attempt + 1))
                else:
                    raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(base_sleep * (attempt + 1))
            else:
                raise
    return None


def find_seed(seed):
    """通过 arXiv ID / DOI / 标题查找种子论文。返回 OpenAlex work 对象。"""
    seed = seed.strip()

    # 尝试 1: arXiv ID -> DOI
    parts = seed.replace("v", "").replace("V", "").split(".")
    if seed.upper().startswith("ARXIV:"):
        arxiv_id = seed.split(":")[1]
        doi = f"10.48550/arXiv.{arxiv_id}"
        url = f"{OA_BASE}/works/https://doi.org/{urllib.parse.quote(doi, safe='')}"
        print(f"[seed] 尝试 arXiv DOI: {doi}")
        data = fetch_json(url)
        if data:
            return data
    elif len(parts) == 2 and all(p.isdigit() for p in parts):
        doi = f"10.48550/arXiv.{seed}"
        url = f"{OA_BASE}/works/https://doi.org/{urllib.parse.quote(doi, safe='')}"
        print(f"[seed] 尝试 arXiv DOI: {doi}")
        data = fetch_json(url)
        if data:
            return data

    # 尝试 2: DOI 直接查
    if seed.startswith("10."):
        url = f"{OA_BASE}/works/https://doi.org/{urllib.parse.quote(seed, safe='')}"
        print(f"[seed] 尝试 DOI: {seed}")
        data = fetch_json(url)
        if data:
            return data

    # 尝试 3: 标题搜索
    query = urllib.parse.quote(seed)
    url = f"{OA_BASE}/works?search={query}&per_page=1"
    print(f"[seed] 标题搜索: {seed}")
    data = fetch_json(url)
    if data and data.get("results"):
        return data["results"][0]

    return None


def get_work(openalex_id):
    """通过 OpenAlex ID 获取论文详情。"""
    # 确保 ID 是完整 URL 格式
    if not openalex_id.startswith("https://"):
        openalex_id = f"https://openalex.org/{openalex_id}"
    url = f"{OA_BASE}/works/{openalex_id}"
    return fetch_json(url)


def get_cited_by(openalex_id, limit=25):
    """获取引用了该论文的论文列表 (citations / cited_by)。"""
    short_id = openalex_id.replace("https://openalex.org/", "")
    url = f"{OA_BASE}/works?filter=cites:{short_id}&per_page={limit}&sort=cited_by_count:desc"
    data = fetch_json(url)
    if not data:
        return []
    return data.get("results", [])


def get_references_batch(ref_ids, limit=25):
    """批量获取被引论文详情 (references)。ref_ids 是 OpenAlex ID 列表。"""
    if not ref_ids:
        return []
    # OpenAlex filter 用 | 分隔,最多 50 个
    short_ids = [r.replace("https://openalex.org/", "") for r in ref_ids[:limit]]
    filter_val = "|".join(short_ids)
    url = f"{OA_BASE}/works?filter=openalex:{urllib.parse.quote(filter_val, safe='')}&per_page={limit}"
    data = fetch_json(url)
    if not data:
        return []
    return data.get("results", [])


# ============================================================
# CCF / SCI 等级映射 (venue 关键词 → 等级)
# ============================================================
# 来源: 中国计算机学会 CCF 推荐目录(2022) + 中科院 SCI 分区(简化)
# 匹配规则: venue 字符串包含任一关键词即命中,长关键词优先。
CCF_LEVEL_MAP = [
    # CCF-A 期刊(精确名优先)
    ("IEEE Transactions on Pattern Analysis and Machine Intelligence", "CCF-A · SCI-Q1"),
    ("TPAMI", "CCF-A · SCI-Q1"),
    ("International Journal of Computer Vision", "CCF-A · SCI-Q1"),
    ("IJCV", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Image Processing", "CCF-A · SCI-Q1"),
    ("Journal of Machine Learning Research", "CCF-A · SCI-Q2"),
    ("JMLR", "CCF-A · SCI-Q2"),
    ("Artificial Intelligence", "CCF-A · SCI-Q1"),
    ("ACM Computing Surveys", "CCF-A · SCI-Q1"),
    ("ACM Transactions on Graphics", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Information Theory", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Software Engineering", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Computers", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Knowledge and Data Engineering", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Industrial Electronics", "CCF-B · SCI-Q1"),
    # CCF-A 会议
    ("CVPR", "CCF-A"), ("ICCV", "CCF-A"), ("NeurIPS", "CCF-A"), ("NIPS", "CCF-A"),
    ("ICML", "CCF-A"), ("AAAI", "CCF-A"), ("IJCAI", "CCF-A"),
    ("KDD", "CCF-A"), ("SIGGRAPH", "CCF-A"),
    ("STOC", "CCF-A"), ("FOCS", "CCF-A"), ("SODA", "CCF-A"),
    ("OSDI", "CCF-A"), ("SOSP", "CCF-A"), ("SIGCOMM", "CCF-A"),
    ("VLDB", "CCF-A"), ("SIGMOD", "CCF-A"), ("POPL", "CCF-A"), ("PLDI", "CCF-A"),
    ("ACL", "CCF-A"), ("SIGCHI", "CCF-A"),
    # CCF-B 会议/期刊
    ("ECCV", "CCF-B"), ("ICLR", "CCF-A"),
    ("WACV", "CCF-B"), ("BMVC", "CCF-B"),
    ("ICRA", "CCF-B"), ("IROS", "CCF-B"),
    ("CIKM", "CCF-B"), ("ICDM", "CCF-B"), ("SDM", "CCF-B"),
    ("PAKDD", "CCF-B"), ("PRICAI", "CCF-B"),
    ("EMNLP", "CCF-B"), ("NAACL", "CCF-B"), ("COLING", "CCF-B"),
    ("Pattern Recognition", "CCF-B · SCI-Q1"),
    ("IEEE Transactions on Cybernetics", "CCF-B · SCI-Q1"),
    ("IEEE Transactions on Neural Networks and Learning Systems", "CCF-B · SCI-Q1"),
    ("IEEE Transactions on Robotics", "CCF-B · SCI-Q1"),
    ("IEEE Transactions on Multimedia", "CCF-B · SCI-Q1"),
    ("IEEE Transactions on Visualization and Computer Graphics", "CCF-B · SCI-Q1"),
    ("IEEE Transactions on Information Forensics and Security", "CCF-A · SCI-Q1"),
    ("IEEE Transactions on Industrial Informatics", "CCF-C · SCI-Q1"),
    # CCF-C
    ("Neurocomputing", "CCF-C · SCI-Q2"),
    ("Neural Networks", "CCF-B · SCI-Q1"),
    ("Expert Systems with Applications", "CCF-C · SCI-Q1"),
    ("Knowledge-Based Systems", "CCF-C · SCI-Q1"),
    ("Engineering Applications of Artificial Intelligence", "CCF-C · SCI-Q1"),
    # 顶刊
    ("Nature Machine Intelligence", "SCI-Q1"),
    ("Nature Communications", "SCI-Q1"),
    ("Nature", "SCI-Q1"), ("Science", "SCI-Q1"), ("Cell", "SCI-Q1"),
    ("Proceedings of the IEEE", "SCI-Q1"),
    ("IEEE Signal Processing Magazine", "SCI-Q1"),
    # 工业/仪器 SCI
    ("IEEE Transactions on Instrumentation and Measurement", "SCI-Q1"),
    ("IEEE Transactions on Vehicular Technology", "SCI-Q1"),
    ("IEEE Transactions on Intelligent Transportation Systems", "SCI-Q1"),
    ("IEEE Transactions on Automation Science and Engineering", "SCI-Q1"),
    ("IEEE Sensors Journal", "SCI-Q1"),
    ("Measurement", "SCI-Q1"),
    ("Mechanical Systems and Signal Processing", "SCI-Q1"),
    # 预印本
    ("arXiv", "预印本 · 未正式发表"),
    ("bioRxiv", "预印本 · 未正式发表"),
    ("medRxiv", "预印本 · 未正式发表"),
    ("SSRN", "预印本 · 未正式发表"),
]


def classify_venue(venue):
    """根据 venue 字符串匹配 CCF/SCI 等级,未命中返回 '未收录'。"""
    if not venue:
        return ""
    for keyword, level in CCF_LEVEL_MAP:
        if keyword.lower() in venue.lower():
            return level
    return "未收录 · 未知等级"


# ============================================================
# Node extraction
# ============================================================
def extract_node(work, is_seed=False, depth=0):
    """从 OpenAlex work 提取节点信息。"""
    authors = []
    for a in (work.get("authorships") or [])[:4]:
        name = a.get("author", {}).get("display_name", "")
        if name:
            authors.append(name)
    author_str = ", ".join(authors)
    if len(work.get("authorships", [])) > 4:
        author_str += " et al."

    ids = work.get("ids", {}) or {}
    # 从 DOI 提取 arXiv ID
    doi = work.get("doi") or ""
    arxiv_id = ""
    if "arxiv" in doi.lower():
        arxiv_id = doi.split("arXiv.")[-1] if "arXiv." in doi else doi.split("arxiv.")[-1]

    abstract_recon = ""
    ai = work.get("abstract_inverted_index")
    if ai:
        # 重建摘要
        word_positions = []
        for word, positions in ai.items():
            for pos in positions:
                word_positions.append((pos, word))
        word_positions.sort()
        abstract_recon = " ".join(w for _, w in word_positions)

    venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name", "")

    return {
        "id": work.get("id", ""),
        "title": work.get("title", "Unknown") or "Unknown",
        "year": work.get("publication_year"),
        "citationCount": work.get("cited_by_count", 0) or 0,
        "referenceCount": len(work.get("referenced_works", []) or []),
        "authors": author_str,
        "arxivId": arxiv_id,
        "doi": doi.replace("https://doi.org/", "") if doi else "",
        "abstract": abstract_recon[:300],
        "isSeed": is_seed,
        "depth": depth,
        "venue": venue,
        "venueLevel": classify_venue(venue),
    }


# ============================================================
# Graph Builder (BFS)
# ============================================================
def build_graph(seed_id, max_depth=2, max_per_level=8, max_total=80):
    """
    BFS 递归构建论文引用图。
    """
    nodes = {}
    edges = []
    edge_set = set()
    visited = set()

    # 1. 查找种子论文
    seed_work = find_seed(seed_id)
    if not seed_work:
        raise ValueError(f"找不到论文: {seed_id}")

    seed_oa_id = seed_work.get("id", "")
    nodes[seed_oa_id] = extract_node(seed_work, is_seed=True, depth=0)
    visited.add(seed_oa_id)
    print(f"[seed] 找到: {nodes[seed_oa_id]['title']} ({nodes[seed_oa_id]['year']})")
    print(f"[seed] 被引 {nodes[seed_oa_id]['citationCount']} 次, 引用 {nodes[seed_oa_id]['referenceCount']} 篇")
    print(f"[seed] OpenAlex ID: {seed_oa_id}")

    # 缓存种子论文的 referenced_works (引用列表)
    seed_refs = seed_work.get("referenced_works", []) or []

    # 2. BFS
    queue = deque([(seed_oa_id, 0, seed_refs)])
    api_calls = 1

    while queue and len(nodes) < max_total:
        current_id, depth, cached_refs = queue.popleft()
        if depth >= max_depth:
            continue

        label = nodes.get(current_id, {}).get("title", current_id)[:50]
        print(f"\n[depth={depth+1}] 扩展: {label}...")

        # --- References (该论文引用了谁) ---
        if cached_refs:
            ref_ids = cached_refs
        else:
            work = get_work(current_id)
            api_calls += 1
            ref_ids = (work or {}).get("referenced_works", []) or []

        # 按... OpenAlex 不返回 references 的 cited_by_count,所以随机取
        # 但我们可以用 cited_by API 获取排序后的
        ref_works = get_references_batch(ref_ids, limit=max_per_level)
        api_calls += 1
        print(f"  references: 获取 {len(ref_works)} 篇")

        for rw in ref_works:
            rw_id = rw.get("id", "")
            if not rw_id:
                continue
            ekey = (current_id, rw_id)
            if ekey not in edge_set:
                edges.append({"source": current_id, "target": rw_id, "type": "references"})
                edge_set.add(ekey)
            if rw_id not in visited and len(nodes) < max_total:
                nodes[rw_id] = extract_node(rw, depth=depth + 1)
                visited.add(rw_id)
                rw_refs = rw.get("referenced_works", []) or []
                queue.append((rw_id, depth + 1, rw_refs))

        time.sleep(0.5)

        # --- Citations (谁引用了该论文) ---
        cites = get_cited_by(current_id, limit=max_per_level)
        api_calls += 1
        print(f"  citations: 获取 {len(cites)} 篇 (按被引排序)")

        for cw in cites:
            cw_id = cw.get("id", "")
            if not cw_id:
                continue
            ekey = (cw_id, current_id)
            if ekey not in edge_set:
                edges.append({"source": cw_id, "target": current_id, "type": "citations"})
                edge_set.add(ekey)
            if cw_id not in visited and len(nodes) < max_total:
                nodes[cw_id] = extract_node(cw, depth=depth + 1)
                visited.add(cw_id)
                cw_refs = cw.get("referenced_works", []) or []
                queue.append((cw_id, depth + 1, cw_refs))

        time.sleep(0.5)

    stats = {
        "seed_title": nodes[seed_oa_id]["title"],
        "seed_id": seed_oa_id,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "max_depth_reached": max((n["depth"] for n in nodes.values()), default=0),
        "api_calls": api_calls,
        "data_source": "OpenAlex",
    }
    return nodes, edges, stats


# ============================================================
# HTML Visualization (Cytoscape.js + Magic UI style, dark theme)
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Paper Graph · __SEED_TITLE__</title>
<script src="vendor/tailwind.js"></script>
<script src="vendor/cytoscape.min.js"></script>
<script src="vendor/layout-base.js"></script>
<script>if(window.layoutBase&&!window['layout-base']){window['layout-base']=window.layoutBase;}</script>
<script src="vendor/cose-base.js"></script>
<script src="vendor/cytoscape-fcose.js"></script>
<style>
  :root {
    --bg-base: #f8fafc; --bg-elev: #ffffff; --bg-soft: #f1f5f9;
    --border: #e2e8f0; --border-strong: #cbd5e1;
    --text-primary: #0f172a; --text-secondary: #475569; --text-muted: #94a3b8;
    --accent-1: #2563eb; --accent-2: #7c3aed; --accent-3: #db2777;
    --accent-cyan: #0891b2; --accent-amber: #d97706; --accent-green: #059669;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  html,body{height:100%;overflow:hidden}
  body{font-family:'Inter','Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,'Helvetica Neue',Arial,sans-serif;background:var(--bg-base);color:var(--text-primary)}
  .bg-deco{position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(ellipse 50% 35% at 15% 0%,rgba(37,99,235,0.08),transparent 60%),radial-gradient(ellipse 45% 40% at 100% 100%,rgba(124,58,237,0.07),transparent 60%)}
  .bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;opacity:0.5;background-image:linear-gradient(rgba(15,23,42,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(15,23,42,0.03) 1px,transparent 1px);background-size:48px 48px;mask-image:radial-gradient(ellipse at center,black 30%,transparent 85%)}
  .glass{background:rgba(255,255,255,0.78);backdrop-filter:blur(18px) saturate(180%);-webkit-backdrop-filter:blur(18px) saturate(180%);border:1px solid var(--border);box-shadow:0 1px 3px rgba(15,23,42,0.04),0 8px 24px rgba(15,23,42,0.04)}
  .gradient-text{background:linear-gradient(135deg,var(--accent-1),var(--accent-2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
  .shimmer{background:linear-gradient(90deg,transparent,rgba(37,99,235,0.5),transparent);background-size:200% 100%;animation:shimmer 1.6s infinite}
  @keyframes shimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
  .beam-border{position:relative;overflow:hidden}
  .beam-border::after{content:'';position:absolute;top:0;left:-100%;width:50%;height:2px;background:linear-gradient(90deg,transparent,var(--accent-1),var(--accent-3),transparent);animation:beam 3s linear infinite}
  @keyframes beam{0%{left:-50%}100%{left:100%}}
  .btn{display:inline-flex;align-items:center;gap:6px;padding:7px 13px;border-radius:8px;font-size:12.5px;font-weight:500;background:#fff;border:1px solid var(--border);color:var(--text-secondary);cursor:pointer;transition:all 0.18s;font-family:inherit;box-shadow:0 1px 2px rgba(15,23,42,0.03)}
  .btn:hover{background:var(--bg-soft);border-color:var(--accent-1);color:var(--accent-1);transform:translateY(-1px);box-shadow:0 2px 6px rgba(37,99,235,0.1)}
  .btn:active{transform:translateY(0)}
  .btn-primary{background:linear-gradient(135deg,var(--accent-1),var(--accent-2));border:none;color:#fff;box-shadow:0 2px 8px rgba(37,99,235,0.25)}
  .btn-primary:hover{box-shadow:0 4px 16px rgba(37,99,235,0.35);color:#fff}
  .chip{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:16px;font-size:11.5px;font-weight:500;background:#fff;border:1px solid var(--border);font-family:'JetBrains Mono',monospace;box-shadow:0 1px 2px rgba(15,23,42,0.03)}
  .chip .val{color:var(--accent-1);font-weight:600}
  .chip .lbl{color:var(--text-muted)}
  #cy{width:100%;height:100%;position:absolute;inset:0}
  .cy-tooltip{position:absolute;z-index:50;max-width:280px;padding:10px 13px;background:#fff;border:1px solid var(--border-strong);border-radius:10px;font-size:12px;color:var(--text-primary);pointer-events:none;box-shadow:0 8px 24px rgba(15,23,42,0.12);display:none}
  #detail-panel{position:fixed;top:72px;right:16px;bottom:16px;width:380px;z-index:40;border-radius:16px;padding:24px;overflow-y:auto;transform:translateX(420px);opacity:0;transition:transform 0.4s cubic-bezier(0.16,1,0.3,1),opacity 0.3s}
  #detail-panel.open{transform:translateX(0);opacity:1}
  #detail-panel::-webkit-scrollbar{width:6px}
  #detail-panel::-webkit-scrollbar-thumb{background:var(--border-strong);border-radius:3px}
  .detail-badge{display:inline-block;padding:3px 10px;border-radius:6px;font-size:10.5px;font-weight:600;letter-spacing:0.4px;font-family:'JetBrains Mono',monospace}
  .level-badge{display:inline-block;padding:4px 11px;border-radius:7px;font-size:11.5px;font-weight:600;font-family:'JetBrains Mono',monospace;margin-top:4px}
  #legend{position:fixed;bottom:16px;left:16px;z-index:30;border-radius:14px;padding:14px 18px}
  .legend-row{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:12px;color:var(--text-secondary)}
  .legend-node{width:14px;height:14px;border-radius:50%;border:2px solid}
  .legend-line{width:26px;height:2px;border-radius:1px}
  #loader{position:fixed;inset:0;z-index:100;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;background:var(--bg-base);transition:opacity 0.5s}
  .loader-ring{width:52px;height:52px;border-radius:50%;border:3px solid rgba(37,99,235,0.15);border-top-color:var(--accent-1);border-right-color:var(--accent-3);animation:spin 0.9s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .search-box{display:flex;align-items:center;gap:8px;padding:6px 12px;border-radius:10px;min-width:220px;background:#fff;border:1px solid var(--border);transition:all 0.18s;box-shadow:0 1px 2px rgba(15,23,42,0.03)}
  .search-box:focus-within{border-color:var(--accent-1);box-shadow:0 0 0 3px rgba(37,99,235,0.12)}
  .search-box input{background:transparent;border:none;outline:none;color:var(--text-primary);font-size:13px;width:100%;font-family:inherit}
  .search-box input::placeholder{color:var(--text-muted)}
  .fade-in{animation:fadeIn 0.6s ease-out}
  @keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
</style>
</head>
<body>
<div class="bg-deco"></div>
<div class="bg-grid"></div>

<div id="loader">
  <div class="loader-ring"></div>
  <div class="text-sm" style="color:var(--text-secondary);font-family:'JetBrains Mono',monospace">
    <span class="gradient-text font-semibold">Paper Graph</span> 引擎初始化中…
  </div>
  <div class="w-48 h-1 rounded-full overflow-hidden" style="background:var(--bg-soft)">
    <div class="shimmer h-full w-full"></div>
  </div>
</div>

<header class="glass beam-border fixed top-3 left-3 right-3 z-40 rounded-2xl px-5 py-3 flex items-center gap-4 fade-in" style="animation-delay:0.1s">
  <div class="flex items-center gap-3">
    <div class="w-9 h-9 rounded-xl flex items-center justify-center" style="background:linear-gradient(135deg,var(--accent-1),var(--accent-2));box-shadow:0 4px 14px rgba(37,99,235,0.3)">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><line x1="7.5" y1="7" x2="10.8" y2="16"/><line x1="16.5" y1="7" x2="13.2" y2="16"/><line x1="8.5" y1="6" x2="15.5" y2="6"/></svg>
    </div>
    <div>
      <div class="text-[15px] font-bold leading-tight gradient-text">Paper Graph</div>
      <div class="text-[10.5px] leading-tight" style="color:var(--text-muted);font-family:'JetBrains Mono',monospace">citation network explorer</div>
    </div>
  </div>
  <div class="h-8 w-px" style="background:var(--border)"></div>
  <div class="hidden md:flex items-center gap-2">
    <span class="chip"><span class="lbl">seed</span><span class="val" id="h-seed">—</span></span>
    <span class="chip"><span class="lbl">nodes</span><span class="val" id="h-nodes">—</span></span>
    <span class="chip"><span class="lbl">edges</span><span class="val" id="h-edges">—</span></span>
    <span class="chip"><span class="lbl">depth</span><span class="val" id="h-depth">—</span></span>
    <span class="chip"><span class="lbl">source</span><span class="val">__DATA_SOURCE__</span></span>
  </div>
  <div class="ml-auto flex items-center gap-2">
    <div class="search-box hidden lg:flex">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--text-muted)"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input id="search-input" placeholder="搜索论文标题…"/>
    </div>
    <button class="btn" onclick="cyFit()" title="适应屏幕"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8V5a2 2 0 0 1 2-2h3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M21 16v3a2 2 0 0 1-2 2h-3"/></svg></button>
    <button class="btn" id="btn-physics" onclick="togglePhysics()" title="物理引擎"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6M12 17v6M4.22 4.22l4.24 4.24M15.54 15.54l4.24 4.24M1 12h6M17 12h6M4.22 19.78l4.24-4.24M15.54 8.46l4.24-4.24"/></svg></button>
    <button class="btn btn-primary" onclick="exportJSON()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>导出</button>
  </div>
</header>

<main style="position:absolute;top:76px;left:12px;right:12px;bottom:12px">
  <div id="cy"></div>
  <div class="cy-tooltip" id="tooltip"></div>
</main>

<aside id="legend" class="glass fade-in" style="animation-delay:0.3s">
  <div class="text-[11px] font-semibold mb-2 uppercase tracking-wider" style="color:var(--text-muted);font-family:'JetBrains Mono',monospace">Legend</div>
  <div class="legend-row"><div class="legend-node" style="background:#fbbf24;border-color:#d97706;box-shadow:0 0 8px rgba(251,191,36,0.4)"></div>种子论文</div>
  <div class="legend-row" style="align-items:center;gap:8px">
    <div style="display:flex;gap:3px">
      <div class="legend-node" style="background:#dbeafe;border-color:#93c5fd;width:11px;height:11px"></div>
      <div class="legend-node" style="background:#60a5fa;border-color:#3b82f6;width:11px;height:11px"></div>
      <div class="legend-node" style="background:#1e40af;border-color:#1e3a8a;width:11px;height:11px"></div>
    </div>
    <span>距2026年:远(浅) → 近(深)</span>
  </div>
  <div class="legend-row"><div class="legend-line" style="background:#64748b"></div>引用关系(箭头指向被引论文)</div>
</aside>

<aside id="detail-panel" class="glass">
  <button onclick="closeDetail()" class="absolute top-4 right-4 w-7 h-7 rounded-lg flex items-center justify-center transition" style="color:var(--text-muted);background:var(--bg-soft)"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
  <div id="d-badge" class="mb-3"></div>
  <h2 id="d-title" class="text-[16px] font-semibold leading-snug mb-3" style="color:var(--text-primary)"></h2>
  <div class="space-y-2 mb-3" style="font-size:12.5px">
    <div class="flex items-center gap-2"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--text-muted)"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg><span style="color:var(--text-secondary)">年份</span><span id="d-year" class="font-mono font-semibold" style="color:var(--accent-1)"></span></div>
    <div class="flex items-center gap-2" id="d-venue-row"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--text-muted)"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg><span style="color:var(--text-secondary)">来源</span><span id="d-venue" style="color:var(--text-primary)"></span></div>
    <div class="flex items-center gap-2" id="d-level-row"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--text-muted)"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg><span style="color:var(--text-secondary)">等级</span><span id="d-level"></span></div>
    <div class="flex items-start gap-2"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--text-muted);margin-top:2px"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg><span style="color:var(--text-secondary);min-width:32px">作者</span><span id="d-authors" style="color:var(--text-primary)"></span></div>
    <div class="flex items-center gap-4 pt-2 border-t" style="border-color:var(--border)">
      <div class="flex items-center gap-1.5"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--accent-3)"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg><span class="font-mono font-semibold" id="d-cited" style="color:var(--accent-3)"></span><span style="color:var(--text-muted);font-size:11px">被引</span></div>
      <div class="flex items-center gap-1.5"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--accent-cyan)"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg><span class="font-mono font-semibold" id="d-refs" style="color:var(--accent-cyan)"></span><span style="color:var(--text-muted);font-size:11px">引用</span></div>
    </div>
  </div>
  <div class="mb-4"><div class="text-[11px] font-semibold mb-1.5 uppercase tracking-wider" style="color:var(--text-muted);font-family:'JetBrains Mono',monospace">摘要</div><p id="d-abstract" class="text-[12.5px] leading-relaxed" style="color:var(--text-secondary)"></p></div>
  <div id="d-links" class="flex flex-wrap gap-2"></div>
  <div class="mt-5 pt-4 border-t" style="border-color:var(--border)"><div class="text-[11px] font-semibold mb-2 uppercase tracking-wider" style="color:var(--text-muted);font-family:'JetBrains Mono',monospace">图谱中的连接</div><div id="d-connections" class="space-y-1.5 text-[12px]"></div></div>
</aside>

<script>
const GRAPH_DATA = __GRAPH_DATA__;
const STATS = GRAPH_DATA.stats;
const NODES = GRAPH_DATA.nodes;
const EDGES = GRAPH_DATA.edges;
document.getElementById('h-seed').textContent = STATS.seed_title.length > 18 ? STATS.seed_title.slice(0,18)+'…' : STATS.seed_title;
document.getElementById('h-nodes').textContent = STATS.total_nodes;
document.getElementById('h-edges').textContent = STATS.total_edges;
document.getElementById('h-depth').textContent = STATS.max_depth_reached;

// 年份 → 颜色 (绝对映射: 固定参考 2026, 距2026越近越深, 不随检索集合变化)
const NOW_YEAR = 2026;        // 参考年份(最新 = 最深)
const OLDEST_YEAR = 2000;     // 固定最老参考(最浅), 早于此年份 clamp 到最浅
const YEAR_SPAN = NOW_YEAR - OLDEST_YEAR;

function yearToColor(year) {
  if (!year) return { bg: '#cbd5e1', border: '#94a3b8' };
  // t: 0 = 最新(2026, 最深) → 1 = 最老(≤2000, 最浅)
  const t = Math.max(0, Math.min(1, (NOW_YEAR - year) / YEAR_SPAN));
  // HSL 蓝色 220: 亮度 32%(新,深) → 85%(老,浅), 饱和度 75%(新) → 50%(老)
  const lightness = 32 + t * 53;
  const saturation = 75 - t * 25;
  return { bg: `hsl(220, ${saturation}%, ${lightness}%)`, border: `hsl(220, ${saturation}%, ${Math.max(lightness - 12, 20)}%)` };
}

function nodeColor(n) {
  if (n.isSeed) return { bg: '#fbbf24', border: '#d97706' };
  return yearToColor(n.year);
}

function nodeSize(n) {
  if (n.isSeed) return 52;
  const cc = n.citationCount || 0;
  // 引用量越大节点越大:范围 24-72,log 缩放让差距更明显
  return Math.max(24, Math.min(72, 24 + Math.log10(cc + 1) * 12));
}

// 等级 → 颜色 (用于详情面板徽章)
function levelColor(level) {
  if (!level) return { bg: '#f1f5f9', fg: '#64748b', border: '#cbd5e1' };
  if (level.includes('CCF-A')) return { bg: '#fef3c7', fg: '#92400e', border: '#f59e0b' };
  if (level.includes('CCF-B')) return { bg: '#dbeafe', fg: '#1e40af', border: '#3b82f6' };
  if (level.includes('CCF-C')) return { bg: '#e0e7ff', fg: '#3730a3', border: '#6366f1' };
  if (level.includes('SCI-Q1')) return { bg: '#dcfce7', fg: '#166534', border: '#22c55e' };
  if (level.includes('SCI-Q2')) return { bg: '#d1fae5', fg: '#065f46', border: '#10b981' };
  if (level.includes('预印本')) return { bg: '#fee2e2', fg: '#991b1b', border: '#ef4444' };
  if (level.includes('未收录')) return { bg: '#f1f5f9', fg: '#64748b', border: '#cbd5e1' };
  return { bg: '#f1f5f9', fg: '#475569', border: '#cbd5e1' };
}

function buildElements() {
  const els = [];
  for (const [id, n] of Object.entries(NODES)) {
    const c = nodeColor(n);
    const s = nodeSize(n);
    // label: 标题截断 + 年份
    const titleShort = n.title.length > 38 ? n.title.slice(0, 38) + '…' : n.title;
    const label = `${titleShort}\n(${n.year || '?'})`;
    els.push({ data: { id, title: n.title, year: n.year, citationCount: n.citationCount, referenceCount: n.referenceCount, authors: n.authors, arxivId: n.arxivId, doi: n.doi, abstract: n.abstract, venue: n.venue, venueLevel: n.venueLevel, isSeed: n.isSeed, depth: n.depth, size: s, bg: c.bg, border: c.border, label: label } });
  }
  const nodeIds = new Set(Object.keys(NODES));
  let dropped = 0;
  for (const e of EDGES) {
    // 过滤无效边: source/target 必须都在节点里(避免被 max_total 截断的节点导致 cytoscape 崩溃)
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) { dropped++; continue; }
    // 统一箭头: source 引用 target,不区分颜色
    els.push({ data: { id: e.source + '->' + e.target, source: e.source, target: e.target } });
  }
  if (dropped > 0) console.warn(`[paper_graph] 丢弃 ${dropped} 条无效边(source/target 节点不在图中)`);
  return els;
}

let cy, physicsEnabled = true;
function initCytoscape() {
  cy = cytoscape({
    container: document.getElementById('cy'),
    elements: buildElements(),
    style: [
      { selector: 'node', style: { 'background-color': 'data(bg)', 'border-color': 'data(border)', 'border-width': 2.5, 'width': 'data(size)', 'height': 'data(size)', 'shape': 'ellipse', 'label': 'data(label)', 'font-family': 'Inter, sans-serif', 'font-size': 10, 'font-weight': 500, 'color': '#0f172a', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 6, 'text-wrap': 'ellipsis', 'text-max-width': 130, 'text-outline-width': 2, 'text-outline-color': '#f8fafc', 'overlay-opacity': 0 } },
      { selector: 'node[?isSeed]', style: { 'shape': 'star', 'border-width': 3.5, 'font-size': 12, 'font-weight': 700, 'color': '#92400e', 'text-outline-color': '#fffbeb' } },
      { selector: 'edge', style: { 'width': 1.6, 'line-color': '#94a3b8', 'line-opacity': 0.45, 'target-arrow-color': '#64748b', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.85, 'curve-style': 'bezier', 'overlay-opacity': 0 } },
      { selector: 'node.hover', style: { 'border-width': 4, 'opacity': 1, 'z-index': 999 } },
      { selector: 'edge.hover', style: { 'line-opacity': 0.85, 'width': 2.5, 'line-color': '#2563eb', 'target-arrow-color': '#2563eb' } },
      { selector: 'node.faded', style: { 'opacity': 0.18 } },
      { selector: 'edge.faded', style: { 'opacity': 0.06 } },
      { selector: 'node.selected', style: { 'border-color': '#2563eb', 'border-width': 4 } },
    ],
    layout: { name: 'fcose', animate: 'end', animationDuration: 1200, quality: 'default', randomize: true, nodeRepulsion: 18000, idealEdgeLength: 180, edgeElasticity: 0.45, gravity: 0.25, numIter: 2500, nodeSeparation: 120, padding: 70, uniformNodeDimensions: true, fit: true },
    wheelSensitivity: 0.2,
  });

  cy.on('mouseover', 'node', function(evt) {
    const node = evt.target; node.addClass('hover');
    const nb = node.closedNeighborhood();
    cy.elements().difference(nb).addClass('faded'); nb.edges().addClass('hover');
    const d = node.data();
    const tip = document.getElementById('tooltip');
    tip.innerHTML = '<div style="font-weight:600;margin-bottom:3px">' + d.title + '</div><div style="color:var(--text-muted);font-size:11px">' + (d.year || 'N/A') + ' · 被引 ' + d.citationCount + (d.venueLevel ? ' · ' + d.venueLevel : '') + '</div>';
    tip.style.display = 'block';
    const pos = evt.renderedPosition; const rect = document.getElementById('cy').getBoundingClientRect();
    tip.style.left = (rect.left + pos.x + 14) + 'px'; tip.style.top = (rect.top + pos.y - 10) + 'px';
  });
  cy.on('mouseout', 'node', function() { cy.elements().removeClass('hover faded'); document.getElementById('tooltip').style.display = 'none' });
  cy.on('tap', 'node', function(evt) { cy.elements().removeClass('selected'); evt.target.addClass('selected'); showDetail(evt.target.data().id) });
  cy.on('tap', function(evt) { if (evt.target === cy) closeDetail() });
  cy.one('layoutstop', () => { setTimeout(() => { const l = document.getElementById('loader'); l.style.opacity = '0'; setTimeout(() => l.style.display = 'none', 500) }, 300) });
  setTimeout(() => { const l = document.getElementById('loader'); if (l.style.display !== 'none') { l.style.opacity = '0'; setTimeout(() => l.style.display = 'none', 500) } }, 4000);
}

function showDetail(id) {
  const n = NODES[id]; if (!n) return;
  const badge = document.getElementById('d-badge');
  if (n.isSeed) { badge.innerHTML = '<span class="detail-badge" style="background:#fef3c7;color:#92400e;border:1px solid #f59e0b">★ SEED · 种子论文</span>' }
  else { badge.innerHTML = '<span class="detail-badge" style="background:#dbeafe;color:#1e40af;border:1px solid #3b82f6">LAYER ' + n.depth + '</span>' }
  document.getElementById('d-title').textContent = n.title;
  document.getElementById('d-year').textContent = n.year || 'N/A';
  const vr = document.getElementById('d-venue-row');
  if (n.venue) { document.getElementById('d-venue').textContent = n.venue; vr.style.display = 'flex' } else { vr.style.display = 'none' }
  // 等级徽章
  const lr = document.getElementById('d-level-row');
  const lc = levelColor(n.venueLevel);
  document.getElementById('d-level').innerHTML = '<span class="level-badge" style="background:' + lc.bg + ';color:' + lc.fg + ';border:1px solid ' + lc.border + '">' + (n.venueLevel || '未知') + '</span>';
  lr.style.display = 'flex';
  document.getElementById('d-authors').textContent = n.authors || 'N/A';
  document.getElementById('d-cited').textContent = (n.citationCount || 0).toLocaleString();
  document.getElementById('d-refs').textContent = n.referenceCount || 0;
  document.getElementById('d-abstract').textContent = n.abstract || '(无摘要)';
  const links = document.getElementById('d-links'); links.innerHTML = '';
  if (n.arxivId) links.innerHTML += '<a href="https://arxiv.org/abs/' + n.arxivId + '" target="_blank" class="btn" style="border-color:#bfdbfe;color:#2563eb">arXiv: ' + n.arxivId + ' ↗</a>';
  if (n.doi) links.innerHTML += '<a href="https://doi.org/' + n.doi + '" target="_blank" class="btn" style="border-color:#fbcfe8;color:#db2777">DOI ↗</a>';
  // 连接信息 (统一箭头语义)
  const conns = document.getElementById('d-connections'); conns.innerHTML = '';
  const refs = EDGES.filter(e => e.source === id).map(e => NODES[e.target]);
  const citedBy = EDGES.filter(e => e.target === id).map(e => NODES[e.source]);
  if (refs.length) { conns.innerHTML += '<div style="color:var(--accent-cyan);font-weight:600;margin-bottom:4px">引用了 ' + refs.length + ' 篇 →</div>'; refs.slice(0, 5).forEach(r => { conns.innerHTML += '<div style="color:var(--text-muted);padding-left:12px">· ' + r.title.slice(0, 45) + (r.title.length > 45 ? '…' : '') + ' (' + (r.year || '?') + ')</div>' }) }
  if (citedBy.length) { conns.innerHTML += '<div style="color:var(--accent-3);font-weight:600;margin:6px 0 4px">被 ' + citedBy.length + ' 篇引用 ←</div>'; citedBy.slice(0, 5).forEach(r => { conns.innerHTML += '<div style="color:var(--text-muted);padding-left:12px">· ' + r.title.slice(0, 45) + (r.title.length > 45 ? '…' : '') + ' (' + (r.year || '?') + ')</div>' }) }
  if (!refs.length && !citedBy.length) conns.innerHTML = '<div style="color:var(--text-muted)">无连接</div>';
  document.getElementById('detail-panel').classList.add('open');
}

function closeDetail() { document.getElementById('detail-panel').classList.remove('open'); cy.elements().removeClass('selected') }
function cyFit() { cy.animate({ fit: { eles: cy.elements(), padding: 60 }, duration: 600 }) }
function togglePhysics() { physicsEnabled = !physicsEnabled; const btn = document.getElementById('btn-physics'); if (physicsEnabled) { cy.layout({ name: 'fcose', animate: true, animationDuration: 900, quality: 'default', randomize: false, nodeRepulsion: 18000, idealEdgeLength: 180, gravity: 0.25, numIter: 2000, nodeSeparation: 120, uniformNodeDimensions: true, fit: false }).run(); btn.style.color = '' } else { btn.style.color = 'var(--text-muted)' } }
function exportJSON() { const blob = new Blob([JSON.stringify(GRAPH_DATA, null, 2)], { type: 'application/json' }); const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'paper_graph.json'; a.click() }
document.getElementById('search-input').addEventListener('input', (e) => { const q = e.target.value.toLowerCase().trim(); if (!q) { cy.elements().removeClass('faded'); return } const matched = cy.nodes().filter(n => n.data('title').toLowerCase().includes(q)); cy.elements().addClass('faded'); matched.removeClass('faded'); matched.neighborhood().removeClass('faded'); if (matched.length) cy.animate({ center: { eles: matched }, zoom: 1.2, duration: 500 }) });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail() });
// 全局错误处理 + 兜底隐藏 loader
window.addEventListener('error', function(e) { console.error('PaperGraph error:', e.message); hideLoader() });
function hideLoader() { const l = document.getElementById('loader'); if (l && l.style.display !== 'none') { l.style.opacity = '0'; setTimeout(() => { l.style.display = 'none' }, 400) } }
setTimeout(hideLoader, 5000);
function safeInit() { if (typeof cytoscape === 'undefined') { console.error('Cytoscape.js not loaded'); document.getElementById('loader').innerHTML = '<div style="color:#db2777;font-size:14px;text-align:center;padding:20px">Cytoscape.js 加载失败<br><span style="font-size:12px;color:#475569">请检查网络或用 Chrome/Edge 打开</span></div>'; return } if (typeof cytoscape('layout','fcose') === 'undefined' && !window.fcoseRegistered) { console.warn('fCoSE 插件未加载,回退到 cose 布局') } try { initCytoscape() } catch (e) { console.error('[initCytoscape失败]', e.stack || e.message || e); hideLoader() } }
if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', safeInit) } else { safeInit() }
</script>
</body>
</html>"""


def generate_html(nodes, edges, stats, output_path):
    """生成精美交互式 HTML 图谱 (Cytoscape.js + Magic UI 风格)。"""
    graph_data = {"stats": stats, "nodes": nodes, "edges": edges}
    html = HTML_TEMPLATE
    html = html.replace("__SEED_TITLE__", stats["seed_title"][:40])
    html = html.replace("__DATA_SOURCE__", stats.get("data_source", "OpenAlex"))
    html = html.replace("__GRAPH_DATA__", json.dumps(graph_data, ensure_ascii=False))
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Paper Graph - 论文引用图谱生成工具 (OpenAlex)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python paper_graph.py --seed 1512.03385
  python paper_graph.py --seed "Attention Is All You Need" --depth 2
  python paper_graph.py --seed 10.48550/arXiv.1512.03385 --depth 1 --max-per-level 10
        """,
    )
    parser.add_argument("--seed", required=True, help="种子论文: arXiv ID / DOI / 标题")
    parser.add_argument("--depth", type=int, default=2, help="递归深度 (默认2)")
    parser.add_argument("--max-per-level", type=int, default=8, help="每层最多取多少 references/citations (默认8)")
    parser.add_argument("--max-total", type=int, default=80, help="图中最大节点总数 (默认80)")
    parser.add_argument("--output", default="paper_graph.html", help="输出 HTML 文件名")
    args = parser.parse_args()

    print("=" * 60)
    print("  Paper Graph - 论文引用图谱生成工具 (OpenAlex)")
    print("=" * 60)
    print(f"  种子: {args.seed}")
    print(f"  深度: {args.depth} | 每层上限: {args.max_per_level} | 总上限: {args.max_total}")
    print("=" * 60)

    nodes, edges, stats = build_graph(
        args.seed,
        max_depth=args.depth,
        max_per_level=args.max_per_level,
        max_total=args.max_total,
    )

    print("\n" + "=" * 60)
    print(f"  构建完成!")
    print(f"  节点: {stats['total_nodes']} | 边: {stats['total_edges']} | 深度: {stats['max_depth_reached']}")
    print(f"  API 调用: {stats['api_calls']} 次")
    print("=" * 60)

    output_path = Path(args.output)
    generate_html(nodes, edges, stats, str(output_path))
    print(f"\n  HTML 已生成: {output_path.resolve()}")

    json_path = output_path.with_suffix(".json")
    graph_data = {"stats": stats, "nodes": nodes, "edges": edges}
    json_path.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON 已生成: {json_path.resolve()}")


if __name__ == "__main__":
    main()
